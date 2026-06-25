# 📖 Tutorial: Event-Driven Agent Architecture

This tutorial explains the end-to-end design, deployment, and communication flows of the **Ambient Expense Reporting system**. It covers how the agent runtime, Pub/Sub event pipeline, and compliance dashboard collaborate in a secure, event-driven pattern.

---

## 1. Deployed Components Overview

The system architecture is divided into three layers:

```
[ Unstructured Inputs ] (Slack, Forms, Mail)
          │
          ▼  (1) Publish JSON Event
   ┌──────────────┐
   │ Pub/Sub Topic│ (expense-reports)
   └──────┬───────┘
          │
          ▼  (2) Push with OIDC Auth (noWrapper)
 ┌────────────────────────────────────────────────────────┐
 │ Vertex AI Agent Runtime (Reasoning Engine App)         │
 │  - Runs ADK 2.0 Workflow                               │
 │  - Auto-approves under $100                            │
 │  - Pauses & registers a pending interrupt for >= $100  │
 └────────────────────────┬───────────────────────────────┘
                          │
                          │ (3) Stores state & events in managed storage
                          ▼
            ┌───────────────────────────┐
            │   Vertex AI Session Store │
            └─────────────┬─────────────┘
                          ▲
                          │ (4) Reads pending interrupts
                          │ (5) Resumes session with decision ("yes"/"no")
 ┌────────────────────────┴───────────────────────────────┐
 │ Manager Dashboard (FastAPI on Cloud Run)               │
 │  - Displays pending expense cards                      │
 │  - Resumes execution flow using VertexAiSessionService │
 └────────────────────────────────────────────────────────┘
```

---

## 2. Event Ingestion & Pub/Sub Communication

### The Role of Pub/Sub
Google Cloud Pub/Sub serves as the **decoupled ingestion channel**. Instead of external systems (like Slack bots, ERP integrations, or email parsers) calling the Agent Runtime API synchronously, they publish an event to the `expense-reports` topic. This allows the system to:
*   Buffer bursts of requests.
*   Retain failed requests in a dead-letter queue (`expense-reports-dead-letter`) for debugging.
*   Execute agent flows asynchronously.

### Push Subscription Configuration
The Pub/Sub subscription `expense-reports-push` is configured as a **Push Subscription** targeting the Reasoning Engine endpoint:
```
https://us-east1-aiplatform.googleapis.com/v1/projects/ambientagents/locations/us-east1/reasoningEngines/<engine-id>:streamQuery
```

To configure this pipeline, we use two key features:
1.  **OIDC Authentication**: Because the stream query endpoint requires IAM authorization, the subscription is configured to generate an OIDC identity token using a custom service account (`pubsub-invoker@ambientagents.iam.gserviceaccount.com`).
2.  **No Wrapper (`noWrapper: {}`)**: By default, Pub/Sub wraps published messages in a standard envelope containing attributes and base64-encoded data. Enabling the "no-wrapper" setting instructs Pub/Sub to post only the raw payload. If you publish `{"input": {"message": "..."}}`, the endpoint receives that exact JSON payload as the request body.

### Publishing Messages
When a payload is published:
```bash
gcloud pubsub topics publish expense-reports \
  --message='{"input": {"message": "Office supplies for $45. Business reason: team meeting."}}'
```
Pub/Sub delivers `{"input": {"message": "..."}}` directly to the `streamQuery` FastAPI handler inside the reasoning engine. The FastAPI handler unpacks the `input` object and maps the key `message` to the keyword-only parameter list of the agent runner.

---

## 3. Deploying the Agent (Agent Runtime)

The agent is packaged and deployed as a **Vertex AI Reasoning Engine** (using `google-adk`).

### How Deployment Works
When you run `agents-cli deploy`, the CLI:
1.  Analyzes the source files in `app/`.
2.  Auto-generates dependency declarations (e.g. [pyproject.toml](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/pyproject.toml) and requirement files).
3.  Tars the source code and uploads it to GCS.
4.  Creates/updates a Reasoning Engine instance pointing to the entrypoint module `app.agent_runtime_app` and object `agent_runtime`.

### Supporting Pub/Sub pushes
The standard `AdkApp` class requires a `user_id` when invoking `stream_query` or `async_stream_query` because sessions are isolated per user. However, Pub/Sub pushes do not contain user identifiers. 
To resolve this, we override the stream methods inside [agent_runtime_app.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/agent_runtime_app.py#L67-L105):
```python
    def stream_query(
        self,
        *,
        message: Union[str, dict[str, Any]],
        user_id: str = "pubsub-user",
        session_id: Optional[str] = None,
        run_config: Optional[dict[str, Any]] = None,
        **kwargs,
    ):
        yield from super().stream_query(
            message=message,
            user_id=user_id,
            session_id=session_id,
            run_config=run_config,
            **kwargs,
        )
```
By making `user_id` optional and defaulting it to `"pubsub-user"`, the Pub/Sub pushes process successfully. A new session is automatically generated for each incoming event, allowing the workflow to run concurrently.

---

## 4. Deploying the Frontend & Communication

The dashboard is a **FastAPI application** packaged as a container and deployed to **Google Cloud Run**.

### Local Configuration
To communicate with the agent, the frontend loads the target Reasoning Engine references from the environment variables (configured via `.env` file locally):
*   `GOOGLE_CLOUD_PROJECT`: The project ID.
*   `AGENT_RUNTIME_ID`: The full resource path of the Reasoning Engine.

### Reading Pending Approvals
The dashboard reads all active reasoning engine sessions using the `VertexAiSessionService` class. It iterates through the session events to identify unresolved interrupts:
*   **Interrupt ID**: When the agent workflow encounters an expense of $100 or more, the workflow yields a `RequestInput(interrupt_id="approval", ...)` event. This writes a pending function call to the session event history.
*   **Matching Responses**: The dashboard scans the history. If it finds a function call named `adk_request_input` without a matching function response in the event list, it displays the item on the web UI as a pending approval card.

### Resuming the Workflow
When a manager clicks "Approve" or "Reject" on the UI, the frontend makes a POST request to `/api/action/{session_id}` and triggers the resume process:
1.  **Format the Response**: It builds a standard ADK function response:
    ```python
    message_payload = {
        "role": "user",
        "parts": [{
            "function_response": {
                "id": interrupt_id,
                "name": "adk_request_input",
                "response": {"result": "yes" if approved else "no"},
            }
        }],
    }
    ```
2.  **Invoke stream query**: The frontend calls `_agent_engine.async_stream_query()` passing the payload, matching the session owner's `user_id` and the original `session_id`.
3.  **Resume Execution**: The workflow node [review_agent](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/agent.py#L87) detects the input under `ctx.resume_inputs["approval"]`, extracts the result, and proceeds to commit the approval or rejection state.

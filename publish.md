# 🚀 Engineering Premium Agentic Systems: A 3-Part Technical Series

This series contains three publication-ready articles detailing the core architectural patterns behind the **Ambient Expense Reporting System**. They are written to be technical, highly informative, yet conversational and engaging for a professional developer audience on platforms like LinkedIn.

---

## 🛑 ARTICLE 1: Session Management & HITL
### **Stop Restarting Your Workflows: How Session Services & State Preservation Power True Human-in-the-Loop AI**

Most AI agents run in a single-turn, stateless sandbox: you prompt, they respond, the context resets. But what happens when an agent needs a human decision midway through a complex graph? Starting the entire workflow from scratch is slow, expensive, and ruins the user experience.

Enter the **Stateful Resume Pattern**, implemented using **ADK 2.0** and **Vertex AI Session Store**.

#### **The Anatomy of a Paused Graph**
In our ambient expense agent, any expense $\ge \$100$ triggers a manual compliance check. Instead of looping or spinning in a thread block (which wastes CPU and costs money), the workflow yields a `RequestInput` event:

```python
yield RequestInput(
    interrupt_id="approval",
    message="Expense requires manual review. Approve? (yes/no):"
)
```

The moment `RequestInput` is returned:
1.  **State Snapshotting**: The framework serializes the current workflow graph context (including `ctx.state`, node history, and pointer position) and locks it in the managed Vertex AI Session Store.
2.  **Execution Suspension**: The Reasoning Engine halts execution and returns a `200 OK` response to the caller, freeing up container resources. The session is marked as *suspended*.

#### **The Resume Contract**
To wake the agent up, the session manager receives the user's decision and formats an ADK-compliant **Resume Payload**:

```json
{
  "role": "user",
  "parts": [{
    "function_response": {
      "id": "approval",
      "name": "adk_request_input",
      "response": {"result": "yes"}
    }
  }]
}
```

When this payload is sent back via `stream_query` specifying the active `session_id` and the original `user_id`:
*   The `VertexAiSessionService` loads the saved state.
*   The graph engine identifies the node marked with `@node(rerun_on_resume=True)`.
*   It populates `ctx.resume_inputs["approval"] = "yes"` and re-executes the node from the exact point of suspension.

**The Takeaway:** True agentic automation isn't about building smarter prompts; it's about building resilient, state-preserving runtime environments that treat human input as just another asynchronous event.

---

## 🎨 ARTICLE 2: Frontend-Agent Collaboration
### **Designing the Glass Hourglass: Best Practices for Connecting Premium Frontends with Suspended AI Agents**

Connecting a user interface to a standard API is straightforward. Connecting a frontend to a collection of running, pausing, and resuming AI agents is a completely different challenge. 

If your frontend is constantly polling, or worse, recreating connections every time an agent pauses for review, you are burning resources and introducing lag. Here are the core patterns we used to build our premium **Glassmorphic Manager Dashboard**:

#### **Pattern 1: Singleton Client & Session Service**
Re-initializing GCP and Vertex AI clients on every incoming HTTP request causes massive overhead (TCP handshakes, credentials discovery). The solution? **Module-level initialization**.
In our FastAPI service, the Vertex client and session services are initialized once at startup:

```python
_vertexai_client = vertexai.Client(project=PROJECT_ID, location=LOCATION)
_agent_engine = _vertexai_client.agent_engines.get(name=AGENT_RUNTIME_ID)
_session_service = VertexAiSessionService(
    project=PROJECT_ID, location=LOCATION, agent_engine_id=_engine_id
)
```

#### **Pattern 2: The Pending Interrupt Registry**
Instead of the frontend managing agent states, it queries the source of truth—the `VertexAiSessionService`—using a clean scanning algorithm:
1.  **Session Discovery**: Fetch all session summaries under the Reasoning Engine.
2.  **Event Analysis**: For each active session, retrieve its full event log.
3.  **Interrupt matching**: Look for `adk_request_input` function calls in the log that **do not** have a corresponding `function_response` yet.
4.  **UX Presentation**: Bind the unresolved interrupt and the underlying session state to a sleek, responsive card on the dashboard.

#### **Pattern 3: Micro-Animations & Optimistic UI Updates**
A premium dashboard should feel alive:
*   Use subtle **glassmorphism styling** (light blur background with thin borders) to convey a modern, premium feel.
*   Implement **optimistic UI updates**: The moment a manager clicks "Approve", show a loading spinner on the button, disable sister actions, and apply a fading slide-out animation to the card only after the backend successfully resumes the session.

**The Takeaway:** The frontend should treat AI agents as asynchronous, state-holding backends. Keep the frontend light, pool sessions efficiently, and use premium animations to keep users engaged during async operations.

---

## 📡 ARTICLE 3: Async Communication Pipeline
### **Asynchronous, Secure, and Zero-Wrapper: Architecting the Ultimate Pub/Sub Pipeline Between Users and Agent Runtimes**

In production, agents should rarely be triggered via direct, synchronous API calls. If an upstream service (like a Slack integration or an email webhook) has to wait for a multi-second LLM processing loop to finish, it will timeout, drop packets, or fail under scale.

Here is how we built a highly resilient, **asynchronous push-based ingestion pipeline** using **GCP Pub/Sub** and **Vertex AI Reasoning Engines**.

#### **1. The Zero-Wrapper Push (`noWrapper: {}`)**
Normally, when Pub/Sub pushes a message to an HTTP endpoint, it wraps the data in an envelope: `{"message": {"data": "...", "messageId": "..."}}`.
To feed this directly into a Reasoning Engine, we must strip this envelope so the payload matches the FastAPI schema expected by `stream_query`. By configuring the Pub/Sub subscription with `noWrapper: {}`, Pub/Sub posts the raw JSON directly as the request body.

#### **2. OIDC Token Authentication**
Because the Reasoning Engine endpoint is private, Pub/Sub must authenticate itself. We configure the push subscription with an OIDC token matching the audience of the Reasoning Engine:

```yaml
pushConfig:
  noWrapper: {}
  oidcToken:
    audience: https://us-east1-aiplatform.googleapis.com/...:streamQuery
    serviceAccountEmail: pubsub-invoker@ambientagents.iam.gserviceaccount.com
```

GCP automatically handles token injection, signing, and renewal.

#### **3. Defaulting the User Context**
FastAPI unpacks the push payload, mapping properties inside `input` to the agent's query parameters. Since Pub/Sub messages do not carry user identity, the call would normally fail with a `TypeError` due to the missing required `user_id` parameter.
To resolve this, we override the entrypoint app methods to default the user context safely:

```python
    async def async_stream_query(
        self,
        *,
        message: Union[str, dict[str, Any]],
        user_id: str = "pubsub-user",
        session_id: Optional[str] = None,
        **kwargs,
    ):
        async for event in super().async_stream_query(
            message=message, user_id=user_id, session_id=session_id, **kwargs
        ):
            yield event
```

This generates a unique, isolated session for each incoming Pub/Sub event under the generic `"pubsub-user"` bucket, allowing thousands of expenses to be processed in parallel.

**The Takeaway:** By combining zero-wrapper pushes, OIDC authentication, and smart runtime parameter defaults, you can turn any Vertex AI Reasoning Engine into a highly scalable, event-driven worker.

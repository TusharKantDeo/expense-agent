# 📝 Future Development TODOs & Precise Requirements

This document outlines the precise technical requirements and specifications for next-generation developer skills and system nodes to build, deploy, and maintain robust Event-Driven Agentic systems.

---

## 🗂️ Tasks Checklist

- [ ] **Task 1: Build a Meta-Skill for ADK & Agent Runtime Best Practices**
- [ ] **Task 2: Build a Communication Channel Setup Skill**
- [ ] **Task 3: Build a Premium UI & Micro-Animation Guideline Skill**
- [ ] **Task 4: Build a Session Cleanup & Memory Manager Service**
- [ ] **Task 5: Build Input Validation Gate Nodes for Graph Workflows**

---

## 📄 Precise Requirements Documents

### 1. Build a Meta-Skill for ADK & Agent Runtime Best Practices
*   **Goal**: Create a reusable guidelines/instructional skill that contains the absolute best practices for building ADK 2.0 graphs, deploying Reasoning Engines, and managing packages.
*   **Requirements**:
    *   **Workflow Graph Structure**: Document type-safety rules. Every `LlmAgent` must use a Pydantic `output_schema` and `output_key`. Function nodes must return `Event` objects with `actions=EventActions(route=...)` instead of passing `route` parameters directly to `Event` constructor to ensure type-checking (`ty check`) passes.
    *   **Looping & Re-entrancy**: Document how to prevent infinite loops by using dynamic `interrupt_id` keys inside loops.
    *   **Dependency Management**: Guide users on maintaining separate `pyproject.toml` files for frontend and agent runtimes to isolate dependencies. Use `uv` and `agents-cli install` as the default dependency builders.
    *   **Environment Decoupling**: Enforce the rule of having ZERO hardcoded project IDs, location regions, or reasoning engine ID paths in the codebase. All details must load from `.env` using `python-dotenv`.

---

### 2. Build a Communication Channel Setup Skill
*   **Goal**: Define a Terraform/CLI automation skill for building secure, high-throughput Pub/Sub communication channels.
*   **Requirements**:
    *   **Topic and Dead-Letter Config**: Automate topic creation for main events (`expense-reports`) and failures (`expense-reports-dead-letter`). Configure subscriptions with a dead-letter policy setting `maxDeliveryAttempts = 5` and `ackDeadlineSeconds = 600`.
    *   **OIDC Secure Push**: Automate push subscription setup targeting `streamQuery`. Configure push parameters with `noWrapper: {}` and OIDC token generation using an invoker service account with `roles/aiplatform.user` IAM role.
    *   **Endpoint Address Mappings**: Document the exact REST mapping for pushing to Vertex AI reasoning engine endpoints:
        `https://<region>-aiplatform.googleapis.com/v1/projects/<project>/locations/<region>/reasoningEngines/<engine-id>:streamQuery`
    *   **Agent Parameter Defaulting**: Include the override template for `stream_query` and `async_stream_query` to automatically assign default `user_id` values (e.g. `"pubsub-user"`) when handling anonymous Pub/Sub push requests.

---

### 3. Build a Premium UI & Micro-Animation Guideline Skill
*   **Goal**: Document CSS/JS styling specifications to build state-of-the-art manager dashboards that keep users wowed.
*   **Requirements**:
    *   **Theme & Typography**: Default to deep slate dark mode backgrounds (`#0f172a`), using gradients from indigo (`#6366f1`) to pink (`#ec4899`), paired with clean Google Fonts (e.g. `Outfit` or `Inter`).
    *   **Glassmorphism Specs**: Apply `backdrop-filter: blur(16px)` and translucent card borders `border: 1px solid rgba(255,255,255,0.08)` to all dashboard cards.
    *   **Interactive Micro-Animations**:
        *   Implement transition scaling `transform: translateY(-5px)` and box-shadow glow on card hovers.
        *   Include loading spinners on buttons immediately upon action click.
        *   Use optimistic UI transitions: disable action triggers and apply smooth fade-out slides to approved/rejected cards as soon as the REST call is initiated.

---

### 4. Build a Session Cleanup & Memory Manager Service
*   **Goal**: Design a cron-based or trigger-based microservice to clean up completed sessions and prevent session store bloat.
*   **Requirements**:
    *   **Status Labeling**: Add a `"workflow_status": "COMPLETED"` or `"workflow_status": "REJECTED"` flag to the final output state of the workflow's terminal nodes.
    *   **Index Scanning**: Implement a Cloud Function that queries the `VertexAiSessionService` list and filters sessions based on active timestamp thresholds.
    *   **Storage Cleanup**: Automatically delete sessions that have been inactive or marked completed for more than 7 days using the `.delete_session()` service call to optimize performance and billing.
    *   **Firestore Indexing**: Integrate a Firestore tracking index to query pending approval sessions in $O(1)$ instead of scanning the full list of reasoning engine sessions.

---

### 5. Build Input Validation Gate Nodes for Graph Workflows
*   **Goal**: Implement a lightweight validator node at the start of workflow graphs to screen incoming requests and reduce LLM execution costs.
*   **Requirements**:
    *   **Gate Node Insertion**: Inject a `validate_input` function node between `START` and `parse_expense` in the graph edges.
    *   **Validation Logic**:
        *   Verify the request body contains valid text content.
        *   Enforce a length check (e.g., minimum 5 characters, maximum 1000 characters).
        *   Identify and filter out spam or irrelevant statements before forwarding to the LLM agent.
    *   **Conditional Routing**:
        *   If the input is valid: route to `parse_expense` with the original message.
        *   If the input is invalid: immediately return a `REJECTED` Event detailing the validation failure, bypassing downstream LLM steps entirely to save API costs.

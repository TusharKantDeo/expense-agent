# Expense Agent — Architecture & Improvement Guide

> **Stack**: ADK 2.0 · Vertex AI Agent Runtime · Pub/Sub · Cloud Run · Python 3.11+
> **Last Updated**: Post-fixes reanalysis

---

## 1. System Overview

```
┌──────────────┐     Pub/Sub Push       ┌─────────────────────────────┐
│   External   │ ──────────────────────▶ │  Vertex AI Agent Runtime    │
│   Systems    │   (expense-reports)     │  (Reasoning Engine)         │
│  (Slack, ERP,│                         │                             │
│   Forms)     │                         │  ┌───────────────────────┐  │
└──────────────┘                         │  │  ADK 2.0 Workflow     │  │
                                         │  │                       │  │
                                         │  │  START                │  │
                                         │  │    │                  │  │
                                         │  │    ▼                  │  │
                                         │  │  parse_expense (LLM)  │  │
                                         │  │    │                  │  │
                                         │  │    ▼                  │  │
                                         │  │  route_expense        │  │
                                         │  │    │          │       │  │
                                         │  │    ▼          ▼       │  │
                                         │  │  auto_     review_    │  │
                                         │  │  approve   agent      │  │
                                         │  │            (HITL)     │  │
                                         │  └───────────────────────┘  │
                                         │                             │
                                         │  Session Store (managed)    │
                                         └──────────────┬──────────────┘
                                                        │
                                            VertexAiSessionService
                                            (list/get sessions)
                                                        │
                                                        ▼
                                         ┌──────────────────────────┐
                                         │  Expense Manager         │
                                         │  Dashboard (Cloud Run)   │
                                         │                          │
                                         │  GET  /api/pending       │
                                         │  POST /api/action/{sid}  │
                                         │  GET  /                  │
                                         └──────────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 Agent Workflow (`app/agent.py`)

| Node | Type | Purpose |
|------|------|---------|
| `parse_expense` | `LlmAgent` | Extracts structured `Expense(item, amount, reason)` from free-text via Gemini |
| `route_expense` | `FunctionNode` | Routes to `auto_approve` (<$100) or `review` (≥$100). Uses typed `Expense` Pydantic input |
| `auto_approve` | `FunctionNode` | Reads from `ctx.state["parsed_expense"]`, emits combined content+output Event |
| `review_agent` | `FunctionNode` (`rerun_on_resume=True`) | Yields `RequestInput`, pauses workflow, awaits human decision via `ctx.resume_inputs` |

**Key configuration:**
- `ResumabilityConfig(is_resumable=True)` enables HITL pause/resume
- `output_key="parsed_expense"` auto-saves LLM output to `ctx.state["parsed_expense"]`
- `route_expense` uses Pydantic auto-conversion (`node_input: Expense`) for type-safe access

### 2.2 Agent Runtime Wrapper (`app/agent_runtime_app.py`)

- Extends `AdkApp` with telemetry, Cloud Logging, and feedback registration
- Environment variables (`_gemini_location`, `_logs_bucket_name`) read at module level before class definition
- Deployed as a Vertex AI Reasoning Engine (managed container)

### 2.3 Frontend Dashboard (`submission_frontend/main.py`)

- FastAPI app deployed to Cloud Run (`expense-manager-dashboard`)
- **Module-level initialization**: Vertex AI client, agent engine reference, and session service are created once at startup
- **`GET /api/pending`**: Scans all sessions via `VertexAiSessionService`, finds unresolved `adk_request_input` function calls
- **`POST /api/action/{session_id}`**: Resumes a suspended session with correct ADK `function_response` convention
- **Resume payload**: Sends `{"result": "yes"/"no"}` per ADK convention, passes `user_id` from the original session

### 2.4 Event Pipeline (Pub/Sub)

- **Topic**: `expense-reports` — receives incoming expense events
- **Dead-letter topic**: `expense-reports-dead-letter` — catches failed messages after 5 attempts
- **Subscription**: `expense-reports-push` — OIDC-authenticated push to Agent Runtime `:streamQuery`
- **Service account**: `pubsub-invoker` — used for push authentication

---

## 3. HITL Resume Contract (ADK 2.0 Best Practice)

### Agent Side (Workflow Node)
```python
@node(rerun_on_resume=True)
async def review_agent(ctx: Context, node_input: dict):
    if not ctx.resume_inputs or "approval" not in ctx.resume_inputs:
        yield RequestInput(interrupt_id="approval", message="Review this expense")
        return

    decision = ctx.resume_inputs["approval"].strip().lower()  # receives "yes" or "no"
    approved = decision in ["yes", "y", "approve", "approved"]
    ...
```

### Frontend Side (Resume Call)
```python
message_payload = {
    "role": "user",
    "parts": [{
        "function_response": {
            "id": interrupt_id,                    # Must match the function_call id
            "name": "adk_request_input",           # Must be this exact string
            "response": {"result": "yes"},         # "result" key → ctx.resume_inputs value
        }
    }]
}
await agent.async_stream_query(
    message=message_payload,
    user_id=original_user_id,  # Must match session owner
    session_id=session_id,
)
```

### Key Rules:
1. **`interrupt_id`** in `RequestInput` becomes the key in `ctx.resume_inputs`
2. **`response.result`** in `function_response` becomes the value at `ctx.resume_inputs[interrupt_id]`
3. **`user_id`** must match the original session's `user_id`
4. **`rerun_on_resume=True`** re-executes the entire node with `ctx.resume_inputs` populated

---

## 4. Fixes Applied

### ✅ Fix 1: Resume Payload Mismatch (was P0 Critical)

**Before**: Frontend sent `{"approved": true}` → agent called `.strip()` on a dict → `AttributeError`  
**After**: Frontend sends `{"result": "yes"}` per ADK convention → agent receives string correctly

### ✅ Fix 2: Hardcoded `user_id` in Resume (was P0 Critical)

**Before**: `user_id="default-user"` hardcoded → session mismatch for non-default users  
**After**: `user_id` flows from pending API → JS → POST body → resume call, matching original session owner

### ✅ Fix 3: Dead Code Removal (was P1)

**Before**: 42-line `LoopSafeGemini` class + unused `Client`/`EventActions` imports  
**After**: Clean imports, standard `Gemini` model instantiation, 142 total lines (down from 186)

### ✅ Fix 4: Variable Ordering in `agent_runtime_app.py` (was P1)

**Before**: `gemini_location` defined after the class that references it  
**After**: `_gemini_location` and `_logs_bucket_name` defined at module level before the class, with clear comments

### ✅ Fix 5: Client Initialization Per-Request (was P1)

**Before**: New `vertexai.Client()` + `agent_engines.get()` on every POST `/api/action/`  
**After**: `_vertexai_client`, `_agent_engine`, and `_session_service` initialized once at module level

---

## 5. Remaining Improvements (Not Yet Applied)

### 🟡 Session Cleanup / TTL (Moderate)

Completed sessions remain in the session store indefinitely. The dashboard scans all of them on every poll.

**Recommendation**: 
- Add `state["workflow_status"] = "COMPLETED"` in terminal nodes
- Filter by status in the pending API, or implement periodic cleanup
- For production: use a Firestore index for O(1) pending lookups

### 🟡 Dashboard Authentication (Moderate)

The Cloud Run dashboard is publicly accessible. Anyone with the URL can approve/reject expenses.

**Recommendation**:
- Enable Cloud Run IAM authentication
- Use Identity-Aware Proxy (IAP) for browser-based access
- Or add OAuth2/OIDC middleware in the FastAPI app

### 🟢 Input Validation Gate (Minor)

No pre-validation before the LLM. Malformed or irrelevant messages are processed and billed.

**Recommendation**: Add a lightweight `validate_input` node before `parse_expense`:
```python
def validate_input(ctx: Context, node_input: types.Content) -> Event:
    text = " ".join(p.text for p in node_input.parts if p.text) if node_input.parts else ""
    if not text or len(text) < 5:
        return Event(output={"status": "REJECTED", "reason": "Invalid input"})
    return Event(output=text, route="parse")
```

### 🟢 Results Topic for Downstream Systems (Minor)

Auto-approved and manually reviewed expenses have no downstream notification. An ERP or accounting system has no way to know the outcome.

**Recommendation**: Publish final outcomes to a `expense-results` Pub/Sub topic for downstream consumption.

---

## 6. File Reference

| File | Purpose | Lines |
|------|---------|-------|
| [agent.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/agent.py) | ADK Workflow definition, all nodes | 142 |
| [agent_runtime_app.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/agent_runtime_app.py) | Vertex AI Reasoning Engine wrapper | 74 |
| [main.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/submission_frontend/main.py) | Dashboard frontend (FastAPI + inline HTML) | 636 |
| [telemetry.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/app_utils/telemetry.py) | OpenTelemetry configuration | 53 |
| [typing.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/app/app_utils/typing.py) | Feedback Pydantic model | 35 |
| [test_expense_flow.py](file:///Users/tusharkantdeo/Desktop/agents/ambientAgents/day5/expense-agent/tests/integration/test_expense_flow.py) | Integration tests for both flows | 162 |

---

## 7. Test Results

All 7 tests pass after fixes:
```
tests/unit/test_dummy.py::test_dummy PASSED
tests/integration/test_agent.py::test_agent_stream PASSED
tests/integration/test_agent_runtime_app.py::test_agent_stream_query PASSED
tests/integration/test_agent_runtime_app.py::test_agent_feedback PASSED
tests/integration/test_expense_flow.py::test_auto_approve_flow PASSED
tests/integration/test_expense_flow.py::test_manual_review_flow_approve PASSED
tests/integration/test_expense_flow.py::test_manual_review_flow_reject PASSED
```

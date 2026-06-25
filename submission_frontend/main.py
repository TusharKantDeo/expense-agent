# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os

import vertexai
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from google.adk.sessions import VertexAiSessionService
from pydantic import BaseModel

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Retrieve environment variables
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-east1"
AGENT_RUNTIME_ID = os.environ.get("AGENT_RUNTIME_ID")

if not PROJECT_ID:
    raise ValueError(
        "GOOGLE_CLOUD_PROJECT (or PROJECT) environment variable is required."
    )
if not AGENT_RUNTIME_ID:
    raise ValueError("AGENT_RUNTIME_ID environment variable is required.")


# Initialize FastAPI app
app = FastAPI(title="Expense Agent Manager Dashboard")

# Initialize Vertex AI SDK client and agent reference once at module level
_vertexai_client = vertexai.Client(project=PROJECT_ID, location=LOCATION)
_agent_engine = _vertexai_client.agent_engines.get(name=AGENT_RUNTIME_ID)
_engine_id = (
    AGENT_RUNTIME_ID.split("/")[-1] if "/" in AGENT_RUNTIME_ID else AGENT_RUNTIME_ID
)
_session_service = VertexAiSessionService(
    project=PROJECT_ID, location=LOCATION, agent_engine_id=_engine_id
)


def extract_expense_details(session) -> dict:
    """Helper to extract expense item, amount, and reason from the session state or events."""
    state = session.state or {}

    # 1. Try extracting from session state parsed_expense dict
    if "parsed_expense" in state:
        pe = state["parsed_expense"]
        if isinstance(pe, dict) and pe.get("amount", 0) > 0:
            return {
                "item": pe.get("item", "Unknown"),
                "amount": float(pe.get("amount", 0.0)),
                "reason": pe.get("reason", "Unknown"),
            }

    # 2. Try extracting from root level of state
    if state.get("amount", 0) > 0:
        return {
            "item": state.get("item", "Unknown"),
            "amount": float(state.get("amount", 0.0)),
            "reason": state.get("reason", "Unknown"),
        }

    # 3. Fallback: Parse the user's initial JSON submission from events
    for event in session.events:
        if event.author == "user" and event.content:
            for part in event.content.parts:
                if part.text:
                    try:
                        data = json.loads(part.text)
                        if "data" in data:
                            d = data["data"]
                            return {
                                "item": d.get("description")
                                or d.get("item")
                                or "Unknown",
                                "amount": float(d.get("amount", 0.0)),
                                "reason": d.get("description") or "Unknown",
                            }
                    except Exception:
                        pass

    return {"item": "Unknown", "amount": 0.0, "reason": "Unknown"}


class ActionRequest(BaseModel):
    interrupt_id: str
    approved: bool
    user_id: str = "default-user"


@app.get("/api/pending")
async def get_pending_approvals():
    """Queries VertexAiSessionService and retrieves pending HILT interrupts."""
    try:
        # List all sessions under the Reasoning Engine
        list_resp = await _session_service.list_sessions(app_name=AGENT_RUNTIME_ID)

        pending_items = []
        for s_summary in list_resp.sessions:
            # Retrieve full session with events history
            session = await _session_service.get_session(
                app_name=AGENT_RUNTIME_ID,
                user_id=s_summary.user_id,
                session_id=s_summary.id,
            )
            if not session or not session.events:
                continue

            pending_calls = {}
            completed_ids = set()

            for event in session.events:
                # Find all adk_request_input function calls
                for fc in event.get_function_calls():
                    if fc.name == "adk_request_input":
                        pending_calls[fc.id] = {
                            "id": fc.id,
                            "args": fc.args or {},
                        }
                # Find all adk_request_input function responses
                for fr in event.get_function_responses():
                    if fr.name == "adk_request_input":
                        completed_ids.add(fr.id)

            # Find unresolved calls (calls that do not have a response)
            for fid, call in pending_calls.items():
                if fid not in completed_ids:
                    expense = extract_expense_details(session)
                    pending_items.append(
                        {
                            "session_id": session.id,
                            "interrupt_id": fid,
                            "message": call["args"].get("message", ""),
                            "expense": expense,
                            "user_id": session.user_id,
                            "last_updated": session.last_update_time,
                        }
                    )

        return pending_items
    except Exception as e:
        logger.error(f"Error fetching pending approvals: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/action/{session_id}")
async def resume_session(session_id: str, req: ActionRequest):
    """Resumes a suspended Agent Runtime session with the approval/rejection decision."""
    try:
        # Build the resume payload per ADK convention:
        # The "result" key value becomes ctx.resume_inputs[interrupt_id]
        decision = "yes" if req.approved else "no"
        message_payload = {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": req.interrupt_id,
                        "name": "adk_request_input",
                        "response": {"result": decision},
                    }
                }
            ],
        }

        # Resume session — user_id must match the original session owner
        responses = []
        async for event in _agent_engine.async_stream_query(
            message=message_payload, user_id=req.user_id, session_id=session_id
        ):
            if isinstance(event, dict):
                content = event.get("content")
                if content and "parts" in content:
                    for part in content["parts"]:
                        if "text" in part:
                            responses.append(part["text"])
            else:
                content = getattr(event, "content", None)
                if content and content.parts:
                    for part in content.parts:
                        if part.text:
                            responses.append(part.text)

        final_message = "\n".join(responses)
        return {"status": "success", "message": final_message}
    except Exception as e:
        logger.error(f"Error resuming session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves a premium glassmorphic dashboard interface."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Expense Agent Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f172a;
            --purple-glow: rgba(99, 102, 241, 0.15);
            --pink-glow: rgba(236, 72, 153, 0.15);
            --card-bg: rgba(255, 255, 255, 0.03);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --success: #10b981;
            --danger: #ef4444;
            --accent: #6366f1;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            background-image:
                radial-gradient(circle at 10% 20%, var(--purple-glow) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, var(--pink-glow) 0%, transparent 40%);
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 2rem 4rem;
            backdrop-filter: blur(8px);
            border-bottom: 1px solid var(--card-border);
        }

        header h1 {
            font-size: 1.8rem;
            font-weight: 800;
            background: linear-gradient(to right, #818cf8, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .refresh-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-primary);
            padding: 0.6rem 1.2rem;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            backdrop-filter: blur(4px);
        }

        .refresh-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }

        main {
            max-width: 1200px;
            margin: 0 auto;
            padding: 3rem 2rem;
        }

        .dashboard-subtitle {
            font-size: 1.1rem;
            color: var(--text-secondary);
            margin-bottom: 2rem;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 100%));
            gap: 2rem;
        }

        @media (min-width: 768px) {
            .grid {
                grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            }
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.8rem;
            backdrop-filter: blur(16px);
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            overflow: hidden;
        }

        .card:hover {
            transform: translateY(-5px);
            border-color: rgba(99, 102, 241, 0.4);
            box-shadow: 0 10px 30px -10px rgba(99, 102, 241, 0.2);
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 4px;
            background: linear-gradient(to right, #6366f1, #a855f7);
            opacity: 0.7;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.2rem;
        }

        .session-badge {
            font-size: 0.75rem;
            background: rgba(99, 102, 241, 0.15);
            color: #a5b4fc;
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            border: 1px solid rgba(99, 102, 241, 0.2);
            font-family: monospace;
        }

        .price-tag {
            font-size: 1.8rem;
            font-weight: 800;
            color: var(--text-primary);
        }

        .detail-row {
            margin-bottom: 0.8rem;
        }

        .detail-label {
            font-size: 0.8rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.2rem;
        }

        .detail-val {
            font-size: 1.05rem;
            font-weight: 600;
        }

        .actions-group {
            display: flex;
            gap: 1rem;
            margin-top: 1.8rem;
        }

        .action-btn {
            flex: 1;
            padding: 0.8rem;
            border-radius: 8px;
            border: none;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        .btn-approve {
            background: var(--success);
            color: #ffffff;
        }

        .btn-approve:hover {
            background: #059669;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
        }

        .btn-reject {
            background: var(--danger);
            color: #ffffff;
        }

        .btn-reject:hover {
            background: #dc2626;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
        }

        .btn-disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        /* Spinner */
        .spinner {
            width: 18px;
            height: 18px;
            border: 2px solid rgba(255,255,255,0.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 0.8s linear infinite;
            display: none;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* Modal styling */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(8px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 100;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.4s ease;
        }

        .modal-overlay.active {
            opacity: 1;
            pointer-events: all;
        }

        .modal-container {
            background: #1e293b;
            border: 1px solid var(--card-border);
            border-radius: 20px;
            width: 90%;
            max-width: 550px;
            padding: 2.5rem;
            transform: scale(0.9) translateY(20px);
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        }

        .modal-overlay.active .modal-container {
            transform: scale(1) translateY(0);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .modal-header h3 {
            font-size: 1.5rem;
            font-weight: 800;
            color: var(--text-primary);
        }

        .modal-close {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.5rem;
            cursor: pointer;
        }

        .modal-content-body {
            font-size: 1.05rem;
            line-height: 1.6;
            color: #e2e8f0;
            background: rgba(0, 0, 0, 0.2);
            padding: 1.5rem;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            max-height: 250px;
            overflow-y: auto;
            white-space: pre-wrap;
        }

        .no-data {
            text-align: center;
            grid-column: 1 / -1;
            padding: 4rem;
            background: var(--card-bg);
            border: 1px dashed var(--card-border);
            border-radius: 16px;
            color: var(--text-secondary);
        }
    </style>
</head>
<body>
    <header>
        <h1>Expense Agent Manager</h1>
        <button class="refresh-btn" onclick="fetchPending()">Refresh Dashboard</button>
    </header>

    <main>
        <p class="dashboard-subtitle">Review and act on pending expenses flagged for approval ($100 or above).</p>
        <div class="grid" id="pending-container">
            <div class="no-data">Loading pending approvals...</div>
        </div>
    </main>

    <!-- Modal for Compliance Review -->
    <div class="modal-overlay" id="review-modal">
        <div class="modal-container">
            <div class="modal-header">
                <h3>Compliance Review</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-content-body" id="modal-text"></div>
        </div>
    </div>

    <script>
        async function fetchPending() {
            const container = document.getElementById('pending-container');
            container.innerHTML = '<div class="no-data">Fetching latest data...</div>';

            try {
                const response = await fetch('/api/pending');
                const data = await response.json();

                if (!data || data.length === 0) {
                    container.innerHTML = '<div class="no-data">🎉 No pending approvals found!</div>';
                    return;
                }

                container.innerHTML = '';
                data.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.innerHTML = `
                        <div class="card-header">
                            <span class="session-badge">Session: ${item.session_id.substring(0, 10)}...</span>
                            <span class="price-tag">$${item.expense.amount.toFixed(2)}</span>
                        </div>
                        <div class="detail-row">
                            <p class="detail-label">Item / Description</p>
                            <p class="detail-val">${item.expense.item}</p>
                        </div>
                        <div class="detail-row">
                            <p class="detail-label">Business Justification</p>
                            <p class="detail-val">${item.expense.reason}</p>
                        </div>
                        <div class="actions-group">
                            <button class="action-btn btn-approve" onclick="handleAction('${item.session_id}', '${item.interrupt_id}', true, '${item.user_id}', this)">
                                <span class="spinner"></span> Approve
                            </button>
                            <button class="action-btn btn-reject" onclick="handleAction('${item.session_id}', '${item.interrupt_id}', false, '${item.user_id}', this)">
                                <span class="spinner"></span> Reject
                            </button>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (error) {
                container.innerHTML = '<div class="no-data" style="color: var(--danger)">❌ Error loading pending approvals.</div>';
                console.error(error);
            }
        }

        async function handleAction(sessionId, interruptId, approved, userId, button) {
            const spinner = button.querySelector('.spinner');
            const parent = button.parentElement;
            const buttons = parent.querySelectorAll('.action-btn');

            // Disable buttons and show spinner
            buttons.forEach(btn => btn.classList.add('btn-disabled'));
            spinner.style.display = 'inline-block';

            try {
                const response = await fetch(`/api/action/${sessionId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interrupt_id: interruptId, approved: approved, user_id: userId })
                });

                const result = await response.json();

                if (response.ok) {
                    showModal(result.message || 'Operation successful!');
                    fetchPending();
                } else {
                    alert(`Error: ${result.detail || 'Failed to complete action'}`);
                }
            } catch (error) {
                alert(`Error: ${error.message}`);
            } finally {
                buttons.forEach(btn => btn.classList.remove('btn-disabled'));
                spinner.style.display = 'none';
            }
        }

        function showModal(text) {
            document.getElementById('modal-text').innerText = text;
            document.getElementById('review-modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('review-modal').classList.remove('active');
        }

        // Initial Load
        fetchPending();
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

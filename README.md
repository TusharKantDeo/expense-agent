# 🧾 Ambient Expense Agent & Manager Dashboard

An intelligent, production-ready **Ambient Expense Reporting Assistant** built using **ADK 2.0 (Agent Development Kit)**, **Vertex AI Agent Runtime (Reasoning Engine)**, and **FastAPI**. 

The system automatically extracts expense details from unstructured messages (e.g. from Slack, ERP, or Forms), processes claims under $100 automatically, and flags expenses of $100 or more for manual review. Managers can review and approve/reject flagged claims via a premium, glassmorphic web dashboard.

---

## 🏗️ Architecture & Component Flow

The system consists of three main components:
1. **Agent Runtime (Reasoning Engine)**: Host of the ADK 2.0 workflow that parses, routes, and pauses for Human-in-the-Loop (HITL) compliance reviews.
2. **Event Pipeline (Pub/Sub)**: An asynchronous push subscription that delivers incoming unstructured expense requests directly to the Reasoning Engine.
3. **Manager Dashboard (FastAPI / Cloud Run)**: A web UI that queries pending sessions, displays details of flagged expenses, and resumes the paused agent workflows with the manager's decision.

```
┌──────────────┐     Pub/Sub Push       ┌─────────────────────────────┐
│   External   │ ──────────────────────▶ │  Vertex AI Agent Runtime    │
│   Systems    │   (expense-reports)     │  (Reasoning Engine)         │
│  (Slack, ERP,│                         │                             │
│   Forms)     │                         │  ┌───────────────────────┐  │
│              │                         │  │  ADK 2.0 Workflow     │  │
└──────────────┘                         │  │                       │  │
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

## 📂 Project Structure

```
expense-agent/
├── app/
│   ├── agent.py                 # Core ADK Workflow and agent nodes
│   ├── agent_runtime_app.py     # Vertex AI Agent Runtime wrapper and overrides
│   └── app_utils/               # Telemetry and typing schemas
├── submission_frontend/
│   ├── main.py                  # FastAPI Dashboard & compliance UI (HTML)
│   ├── Dockerfile               # Production Dockerfile for Dashboard deployment
│   └── pyproject.toml           # Frontend Python dependencies
├── tests/
│   ├── unit/                    # Unit tests
│   └── integration/             # Integration tests for agent, runtime, and expense flows
├── pyproject.toml               # Root agent dependencies
├── GEMINI.md                    # Coding agent guide & CLI references
└── .env.example                 # Environment variables template
```

---

## 🛠️ Setup & Local Development

### Prerequisites
- **uv**: Fast Python package manager ([Install uv](https://docs.astral.sh/uv/getting-started/installation/))
- **agents-cli**: Google Agents CLI (`uv tool install google-agents-cli`)
- **Google Cloud SDK**: authenticated and configured (`gcloud auth login`)

### 1. Initialize the Environment
Clone the repository, then copy the example environment file and configure the settings:
```bash
cp .env.example .env
```

Edit `.env` and set your GCP configuration values.

### 2. Install Dependencies
Run the installation command to set up the virtual environment:
```bash
agents-cli install
```

### 3. Run Automated Tests
Execute the test suite to verify that the logic is correct:
```bash
uv run pytest tests/unit tests/integration
```

### 4. Run the Local Playground
Interact and test the agent workflow locally using the playground UI:
```bash
agents-cli playground
```

---

## 🚀 Deployment

### 1. Deploy the Agent Runtime (Reasoning Engine)
Configure your Google Cloud Project and deploy the ADK agent engine:
```bash
gcloud config set project YOUR_PROJECT_ID
agents-cli deploy --project YOUR_PROJECT_ID --no-confirm-project
```

Upon successful deployment, copy the **Agent Runtime ID** (e.g. `projects/<project>/locations/<location>/reasoningEngines/<id>`) and save it as `AGENT_RUNTIME_ID` in your `.env` file.

### 2. Deploy the Dashboard (Cloud Run)
Navigate to the `submission_frontend` directory and deploy the dashboard container to Cloud Run:
```bash
cd submission_frontend
gcloud run deploy expense-manager-dashboard \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-east1,AGENT_RUNTIME_ID=YOUR_AGENT_RUNTIME_ID"
```

Once deployed, you will receive a public URL for your dashboard.

---

## 📬 Pub/Sub Integration & Live Testing

### 1. Publish an Auto-Approved Expense (< $100)
Run this command to publish an expense under $100. It will process immediately and be automatically approved:
```bash
gcloud pubsub topics publish expense-reports \
  --message='{"input": {"message": "I bought office supplies for $45. Business reason: team meeting preparation."}}'
```

### 2. Publish an Expense Requiring Review (>= $100)
Run this command to publish an expense that is $100 or more:
```bash
gcloud pubsub topics publish expense-reports \
  --message='{"input": {"message": "Bought new monitor for $150. Business reason: home office upgrade."}}'
```

### 3. Compliance Review & Resume Flow
1. Open the **Dashboard URL** generated during the Cloud Run deployment.
2. The dashboard will show the **$150.00** expense under "Pending Approvals".
3. Click **Approve** or **Reject**. The dashboard sends a resume signal back to the agent session.
4. The Reasoning Engine resumes the workflow, processes the decision, and completes the workflow execution.

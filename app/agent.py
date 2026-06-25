# ruff: noqa
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

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.events.request_input import RequestInput
from google.adk.models import Gemini
from google.adk.workflow import Workflow, node
from google.genai import types
from pydantic import BaseModel, Field

# Ensure correct region and Vertex AI settings
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = os.environ.get(
    "GOOGLE_GENAI_USE_ENTERPRISE", "True"
)


# Model definition
model = Gemini(
    model="gemini-2.5-flash",
    retry_options=types.HttpRetryOptions(attempts=3),
)


# Pydantic schemas
class Expense(BaseModel):
    item: str = Field(
        description="The name or brief description of the item or service purchased."
    )
    amount: float = Field(description="The exact numeric cost/amount of the expense.")
    reason: str = Field(
        description="The business justification or reason for this expense."
    )


# Nodes
parse_expense = LlmAgent(
    name="parse_expense",
    model=model,
    instruction=(
        "You are an expert expense parser. Extract the item name, numeric amount, and business justification/reason "
        "from the user's input message. If any details are missing, make a best guess based on the context."
    ),
    output_schema=Expense,
    output_key="parsed_expense",
)


def route_expense(ctx: Context, node_input: Expense) -> Event:
    """Routes the expense based on the amount ($100 threshold)."""
    route = "auto_approve" if node_input.amount < 100.0 else "review"
    return Event(output=node_input.model_dump(), actions=EventActions(route=route))


def auto_approve(ctx: Context, node_input: dict):
    """Automatically approves the expense if it is under $100."""
    expense = ctx.state.get("parsed_expense", {})
    item = expense.get("item", "Unknown")
    amount = expense.get("amount", 0.0)
    reason = expense.get("reason", "No reason provided")

    msg = f"Expense of ${amount:.2f} for '{item}' (Reason: {reason}) is automatically APPROVED (under $100)."

    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
        output={
            "status": "APPROVED",
            "reason": "Auto-approved (under $100)",
            "expense": {"item": item, "amount": amount, "reason": reason},
        },
    )


@node(rerun_on_resume=True)
async def review_agent(ctx: Context, node_input: dict):
    """Flags the expense for human-in-the-loop review if it is $100 or more."""
    expense = ctx.state.get("parsed_expense", {})
    item = expense.get("item", "Unknown")
    amount = expense.get("amount", 0.0)
    reason = expense.get("reason", "No reason provided")

    # Check if we have received a response to the review prompt
    if not ctx.resume_inputs or "approval" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="approval",
            message=(
                f"⚠️ Expense of ${amount:.2f} for '{item}' (Reason: {reason}) requires manual review because it is $100 or more.\n"
                f"Please approve this expense. Enter 'yes' to approve, or 'no' to reject:"
            ),
        )
        return

    # Process the user's decision
    decision = ctx.resume_inputs["approval"].strip().lower()
    approved = decision in ["yes", "y", "approve", "approved"]
    status = "APPROVED" if approved else "REJECTED"

    msg = f"Manual review complete: Expense of ${amount:.2f} for '{item}' has been {status}. (Decision: {decision})"

    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]),
        output={
            "status": status,
            "reason": f"Manual review decision: {decision}",
            "expense": {"item": item, "amount": amount, "reason": reason},
        },
    )


# Workflow definition
root_agent = Workflow(
    name="ambient_expense_workflow",
    edges=[
        ("START", parse_expense),
        (parse_expense, route_expense),
        (
            route_expense,
            {"auto_approve": auto_approve, "review": review_agent},
        ),
    ],
    description="An ambient expense reporting assistant that automatically approves claims under $100 and flags larger ones for review.",
)

# App definition must match directory name 'app'
app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)

"""Supervisor -- picks the next specialist, or decides there is enough.

The LLM does not "jump" anywhere. It writes a string into state, and a plain
Python routing function reads that string and returns a node name. That is
the whole mechanism; the model's only power over control flow is the value
it writes.

Two guards make the graph terminate regardless of what it writes:
  - a hop budget, checked before the model is consulted
  - repeat suppression, so it cannot call the same agent twice
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app import llm
from app.graph.state import AGENT_DESCRIPTIONS, AskState

MAX_HOPS = 6

SYSTEM = """You route an industrial parts sourcing question to one specialist \
at a time.

Specialists:
""" + "\n".join(f"- {name}: {desc}" for name, desc in AGENT_DESCRIPTIONS.items()) + """

Dependencies between them -- these decide the order:
- 'analyst' can only cost offers that 'scout' has already collected. For any
  price, cost or comparison question with no offers yet, route to 'scout'
  first, then 'analyst'.
- 'part' answers what a component IS -- specifications, signals, ratings,
  dimensions. It never answers what something COSTS. A question containing
  price, cost, landed, cheapest, quote or supplier is never a 'part' question,
  even when it names a part number.
- 'history' stands alone. It needs nothing else first.

Pick the single most useful next step given what has already been collected.

Choose 'finish' as soon as the evidence can answer the question. Gathering
more than you need is a cost, not a virtue. If an agent has already run and
returned nothing useful, do not call it again -- either try a different one
or finish and say what is missing."""


class Route(BaseModel):
    next: str = Field(
        description="One of: part, scout, history, analyst, finish"
    )
    why: str = Field(description="One short line: why this one next.")


VALID = set(AGENT_DESCRIPTIONS)


def supervisor_node(state: AskState) -> dict[str, Any]:
    hops = state.get("hops", 0)
    steps = state.get("steps", [])
    evidence = state.get("evidence", [])

    # Budget check happens before the model is asked, so an exhausted run
    # cannot be talked into another hop.
    if hops >= MAX_HOPS:
        return {
            "plan": "finish",
            "hops": hops + 1,
            "steps": [f"supervisor -> finish (hop budget {MAX_HOPS} reached)"],
        }

    already = {step.split(":")[0] for step in steps if ":" in step}

    decision = llm.structured(
        f"Question: {state['question']}\n"
        f"Agents already run: {sorted(already) or 'none'}\n"
        f"Evidence collected: {len(evidence)} items\n"
        f"Summary so far: {steps or 'nothing yet'}\n\n"
        "Which specialist next, or finish?",
        Route,
        system=SYSTEM,
        role="reasoning",
    )

    choice = decision.next.strip().lower()
    if choice not in VALID:
        choice = "finish"

    # Repeat suppression. Calling an agent twice is the standard way these
    # graphs loop forever, and it is cheaper to block it than to detect it.
    if choice in already:
        remaining = [a for a in VALID if a not in already and a != "finish"]
        choice = remaining[0] if remaining and not evidence else "finish"
        why = "already ran, moving on"
    else:
        why = decision.why

    return {
        "plan": choice,
        "hops": hops + 1,
        "steps": [f"supervisor -> {choice} ({why})"],
    }


def route_after_supervisor(state: AskState) -> str:
    """Plain Python. Reads a field the model wrote; decides nothing itself."""
    plan = state.get("plan", "finish")
    return "generate" if plan == "finish" else plan

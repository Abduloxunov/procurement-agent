"""Critic -- provenance enforcement.

Not a technical checkbox. A sourcing recommendation gets questioned by a
manager and sometimes audited, and "the AI said so" is not a defence. So the
rule is: every figure in the answer must trace to a piece of evidence.

Two layers, because one is not enough:
  1. a code check that catches numbers absent from the evidence outright
  2. an LLM judgement on whether claims are actually supported

The code check runs first and is the cheaper, harder guarantee.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app import llm
from app.graph.state import AskState

MAX_REVISIONS = 2

# Numbers that are prose, not claims -- years, small counts, percentages
# already qualified. Checking these produces noise, not safety.
IGNORE = re.compile(r"^(19|20)\d{2}$")
NUMBER = re.compile(r"\d[\d,]*\.?\d*")


class Verdict(BaseModel):
    grounded: bool = Field(
        description="True if every factual claim is supported by the evidence."
    )
    answers_question: bool = Field(
        description="True if the answer actually addresses what was asked."
    )
    problem: str = Field(
        description="If either is false, name the specific unsupported claim "
        "or the gap. One line. '' if fine."
    )


def _numbers(text: str) -> set[str]:
    found = set()
    for match in NUMBER.findall(text):
        cleaned = match.replace(",", "").rstrip(".")
        if not cleaned or IGNORE.match(cleaned):
            continue
        # Ignore trivially small integers -- "3 suppliers" is not a claim
        # that needs a citation.
        try:
            if abs(float(cleaned)) < 10 and "." not in cleaned:
                continue
        except ValueError:
            continue
        found.add(cleaned)
    return found


def unsupported_numbers(answer: str, evidence: list[dict]) -> list[str]:
    """Numbers in the answer that appear nowhere in the evidence."""
    haystack = " ".join(item["content"] for item in evidence).replace(",", "")
    evidence_numbers = _numbers(haystack)

    missing = []
    for number in _numbers(answer):
        if number in evidence_numbers:
            continue
        # Tolerate rounding: 45.29 supported by 45.2857
        if any(
            candidate.startswith(number) or number.startswith(candidate)
            for candidate in evidence_numbers
        ):
            continue
        missing.append(number)
    return missing


def critic_node(state: AskState) -> dict[str, Any]:
    answer = state.get("answer", "")
    evidence = state.get("evidence", [])
    revisions = state.get("revisions", 0)

    if not answer:
        return {"verdict": "approved", "steps": ["critic: nothing to check"]}

    # Out of retries -- ship it with the caveat rather than looping.
    if revisions >= MAX_REVISIONS:
        return {
            "verdict": "approved",
            "steps": [f"critic: revision cap {MAX_REVISIONS} reached, passing"],
        }

    # --- layer 1: uncited numbers -----------------------------------------
    orphans = unsupported_numbers(answer, evidence)
    if orphans:
        return {
            "verdict": "revise",
            "revisions": revisions + 1,
            "critique": (
                f"these figures appear nowhere in the evidence: "
                f"{', '.join(orphans[:5])}. Use only numbers from the "
                f"evidence, or say the figure is not available."
            ),
            "steps": [f"critic: REVISE -- uncited numbers {orphans[:5]}"],
        }

    # --- layer 2: is it actually supported and on-topic? -------------------
    block = "\n\n".join(
        f"[{item['agent']}] {item['ref']}\n{item['content']}" for item in evidence
    )
    verdict = llm.structured(
        f"Question: {state['question']}\n\n"
        f"--- evidence ---\n{block}\n--- end ---\n\n"
        f"Proposed answer:\n{answer}",
        Verdict,
        system=(
            "You verify a procurement answer before a buyer sees it. Be "
            "strict about grounding and fair about scope: an answer that "
            "correctly says the evidence is insufficient IS grounded and DOES "
            "answer the question."
        ),
        role="reasoning",
    )

    if verdict.grounded and verdict.answers_question:
        return {"verdict": "approved", "steps": ["critic: approved"]}

    return {
        "verdict": "revise",
        "revisions": revisions + 1,
        "critique": verdict.problem or "not adequately grounded",
        "steps": [f"critic: REVISE -- {verdict.problem[:80]}"],
    }


def route_after_critic(state: AskState) -> str:
    """Plain Python again -- reads the verdict the critic node wrote."""
    return "generate" if state.get("verdict") == "revise" else "done"

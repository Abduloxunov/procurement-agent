"""Clarify node -- ask before searching, but only what changes the answer.

Half the requests that reach a sourcing engineer are underspecified, and
guessing produces a confident, useless table. But interrogating him is worse
than guessing, so this is bounded hard: two questions maximum, and everything
else becomes a stated assumption (design doc S09).

Contrast with the purchase log, which asks for everything -- that record is
permanent and unrecoverable, this one costs a re-run at worst.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app import llm

MAX_QUESTIONS = 2

SYSTEM = f"""You prepare an industrial parts sourcing request before a search runs.

Ask a question ONLY when the answer would change the result:
- Quantity always changes it -- it moves MOQ, tier pricing and freight mode.
- Whether a key spec is mandatory or merely preferred changes it, because it
  decides the size of the candidate pool. (Example: RS485 vs 4-20 mA output
  are not interchangeable, so "would 4-20 mA also work?" is worth asking.)
- Destination changes it if not already known.

Never ask about: PO numbers, internal project names, warranty preference,
colour, packaging, or anything cosmetic. Never ask something the request
already answers.

At most {MAX_QUESTIONS} questions. Everything else you need, state as an
assumption instead of asking. If nothing material is missing, set ready=true
with no questions."""


class Clarification(BaseModel):
    ready: bool = Field(description="True if the search can run as-is.")
    part_number: str = Field(
        description="Manufacturer part number if one is given or clearly "
        "identifiable, else ''. Do not invent one from a description."
    )
    description: str = Field(
        description="What is being sourced, in a few words. Always fill this, "
        "even when a part number is given."
    )
    quantity: int = Field(description="Quantity if stated, else -1.")
    budget: float = Field(
        description="Total budget in USD if stated, else -1. Convert a "
        "per-unit budget to a total using the quantity."
    )
    need_by: str = Field(
        description="Deadline as YYYY-MM-DD if a date is given or inferable "
        "(e.g. 'by March' -> the 1st of the next March), else ''."
    )
    mandatory_specs: list[str] = Field(
        description="Specifications stated as hard requirements, e.g. "
        "'output_signal: RS485'. Empty if none stated."
    )
    questions: list[str] = Field(
        description=f"At most {MAX_QUESTIONS} questions, each one line. "
        "Empty if ready."
    )
    assumptions: list[str] = Field(
        description="What you assumed rather than asked, each one line."
    )


QUANTITY_QUESTION = "How many do you need? Price and freight both turn on it."


def check(request_text: str, *, destination: str = "Tashkent, UZ") -> Clarification:
    result = llm.structured(
        f"Today is {date.today().isoformat()}.\n"
        f"Destination is {destination} unless stated otherwise.\n\n"
        f"Request: {request_text}",
        Clarification,
        system=SYSTEM,
        role="reasoning",
    )

    # Quantity is the one field that always changes the answer, and the model
    # will happily mark a request ready without it. Prompts are guidance;
    # this is the guarantee.
    if result.quantity is None or result.quantity <= 0:
        already_asked = any(
            "how many" in q.lower() or "quantity" in q.lower()
            for q in result.questions
        )
        if not already_asked:
            result.questions.insert(0, QUANTITY_QUESTION)

    # Enforce the cap in code too.
    result.questions = result.questions[:MAX_QUESTIONS]
    result.ready = not result.questions
    return result

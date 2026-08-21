"""Intake -- turn "I need 50 X, budget Y, by March" into a sourcing request.

Stage 1 of the workflow. Runs the clarify node first: if something material
is missing the request is not created, the questions come back instead. Only
once it is answerable does a row appear.

Deliberately no partial requests. A half-specified request that quietly gets
searched anyway produces a confident, useless table.
"""

from __future__ import annotations

from typing import Any

from app.agents import clarify
from app.db.database import session


def preview(text: str) -> dict[str, Any]:
    """What we understood, and what we still need. Creates nothing."""
    result = clarify.check(text)
    return {
        "ready": result.ready,
        "understood": {
            "part_number": result.part_number,
            "description": result.description,
            "quantity": result.quantity if result.quantity > 0 else None,
            "budget": result.budget if result.budget > 0 else None,
            "need_by": result.need_by or None,
            "mandatory_specs": result.mandatory_specs,
        },
        "questions": result.questions,
        "assumptions": result.assumptions,
    }


def create(text: str, answer: str | None = None) -> dict[str, Any]:
    """Create the request, folding in an answer to the clarifying questions.

    Returns {'ready': False, 'questions': [...]} instead if it still cannot
    be answered -- the caller asks and comes back.
    """
    combined = f"{text}\n\nAdditional detail: {answer}" if answer else text
    result = clarify.check(combined)

    if not result.ready:
        return {
            "ready": False,
            "questions": result.questions,
            "assumptions": result.assumptions,
            "understood": preview(combined)["understood"],
        }

    mpn = result.part_number.strip()
    label = mpn or result.description.strip() or "unnamed part"

    with session() as conn:
        part_id = None
        if mpn:
            row = conn.execute(
                "SELECT id FROM parts WHERE mpn = ?", (mpn,)
            ).fetchone()
            part_id = row["id"] if row else conn.execute(
                "INSERT INTO parts (mpn, description, lifecycle) "
                "VALUES (?, ?, 'unknown')",
                (mpn, result.description or None),
            ).lastrowid

            # Specs he called hard requirements are mandatory. Nothing else
            # is -- the flag decides disqualification, so it is never guessed.
            for spec in result.mandatory_specs:
                key, _, value = spec.partition(":")
                if not value.strip():
                    continue
                conn.execute(
                    "INSERT INTO part_specs (part_id, spec_key, spec_value, "
                    "is_mandatory, source) VALUES (?, ?, ?, 1, 'user')",
                    (part_id, key.strip().lower()[:60], value.strip()[:200]),
                )

        quantity = result.quantity if result.quantity > 0 else 1
        request_id = conn.execute(
            "INSERT INTO requests (title, part_id, raw_request, quantity, "
            "need_by, budget, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')",
            (
                f"{label} x{quantity}",
                part_id,
                combined,
                quantity,
                result.need_by or None,
                result.budget if result.budget > 0 else None,
            ),
        ).lastrowid

    return {
        "ready": True,
        "request_id": request_id,
        "part_number": mpn,
        "description": result.description,
        "quantity": quantity,
        "budget": result.budget if result.budget > 0 else None,
        "need_by": result.need_by or None,
        "mandatory_specs": result.mandatory_specs,
        "assumptions": result.assumptions,
        "searchable": bool(mpn),
    }


def budget_check(request_id: int, quantity: int | None = None) -> dict[str, Any]:
    """Does the cheapest landed option fit the budget he stated?

    Distinguishes 'no budget was given' from 'a budget was given but nothing
    is priced yet' -- collapsing those two into one answer tells him his
    budget was ignored when it simply has not been tested.
    """
    from app.analysis import compare

    with session() as conn:
        row = conn.execute(
            "SELECT budget, quantity FROM requests WHERE id = ?", (request_id,)
        ).fetchone()

    if row is None:
        return {"state": "no_request"}
    if row["budget"] is None:
        return {"state": "no_budget"}

    qty = quantity or row["quantity"] or 1
    table = compare.build(request_id, qty, persist=False)

    if not table["priced"]:
        return {
            "state": "not_yet_priced",
            "budget": row["budget"],
            "quantity": qty,
            "unpriced": len(table["unpriced"]),
        }

    best = table["priced"][0]
    total = round(best["per_unit"] * qty, 2)
    return {
        "state": "checked",
        "budget": row["budget"],
        "quantity": qty,
        "cheapest_total": total,
        "within_budget": total <= row["budget"],
        "over_by": round(max(0.0, total - row["budget"]), 2),
        "headroom": round(max(0.0, row["budget"] - total), 2),
        "supplier": best["supplier"],
    }

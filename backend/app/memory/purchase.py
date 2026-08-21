"""Purchase log -- institutional memory from one sentence.

He types what he bought. The agent completes the record, because a purchase
is written once and cannot be reconstructed later (design doc S10).

This asks for MORE than the clarify node does, deliberately. Clarify asks the
minimum because a wrong assumption costs one re-run; this asks for everything
because a missing price basis silently corrupts every future comparison.
Questions are batched into one message, so it still costs him one reply.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app import llm
from app.db.database import session

UNKNOWN_STR = ""
UNKNOWN_NUM = -1.0
UNKNOWN_INT = -1


class PurchaseDraft(BaseModel):
    """Sentinels, not Optional -- see sources/models.py for why."""

    part_number: str = Field(description="MPN as stated. '' if not given.")
    supplier_name: str = Field(description="Who it was bought from. '' if not given.")
    quantity: int = Field(description="How many. -1 if not given.")
    unit_price: float = Field(description="Price per unit. -1 if not given.")
    currency: str = Field(description="ISO code. Default USD if a bare number.")
    price_basis: str = Field(
        description="FOB, EXW, DDP, CIF or 'landed'. '' if not stated. "
        "Critical: $41 FOB and $41 delivered are different numbers."
    )
    freight_paid: float = Field(description="Total freight paid. -1 if not given.")
    duty_vat_paid: float = Field(description="Total duty + VAT paid. -1 if not given.")
    ordered_on: str = Field(description="YYYY-MM-DD if stated or inferable, else ''.")
    received_on: str = Field(description="YYYY-MM-DD if stated or inferable, else ''.")
    lead_days: int = Field(description="Days between order and arrival. -1 if unknown.")
    condition: str = Field(description="'good', 'partial', 'faulty', or '' if not said.")
    would_buy_again: int = Field(description="1 yes, 0 no, -1 not stated.")


# Field -> the question to ask. Only fields that change a future answer.
QUESTIONS = {
    "part_number":   "which exact part number?",
    "supplier_name": "which supplier?",
    "quantity":      "how many?",
    "unit_price":    "price per unit?",
    "price_basis":   "was that price FOB, EXW, DDP or delivered/landed?",
    "freight_paid":  "what did freight cost, if you know?",
    "duty_vat_paid": "what did duty + VAT come to, if you know?",
    "ordered_on":    "roughly when did you order?",
    "condition":     "did it all arrive working?",
}

# Without these the record is not worth storing.
REQUIRED = ("part_number", "supplier_name", "quantity", "unit_price", "price_basis")

SYSTEM = (
    "You turn a buyer's shorthand about a past purchase into structured data. "
    "Extract only what is stated or clearly implied. Never invent a price "
    "basis, a date or a supplier. Use -1 for unknown numbers and '' for "
    "unknown text. If a bare number is given as a price, assume USD."
)


def _is_missing(draft: PurchaseDraft, field: str) -> bool:
    value = getattr(draft, field)
    if isinstance(value, str):
        return not value.strip()
    return value is None or value < 0


# Words that actually state a price basis. If none appears in what he wrote,
# no basis was given -- whatever the model returns is an inference.
BASIS_TERMS = (
    "fob", "exw", "ex works", "ddp", "ddu", "dap", "cif", "cip", "cfr", "fca",
    "landed", "delivered", "door to door", "door-to-door", "all in", "all-in",
    "free on board", "duty paid",
)


def _scrub_inferred_basis(draft: PurchaseDraft, source_text: str) -> PurchaseDraft:
    """Blank price_basis unless the buyer actually stated one.

    The model will happily infer 'landed' from a bare "$41 each". That is the
    most favourable reading, it is silent, and it poisons every later
    comparison -- a $41 FOB part lands near $60. So the prompt asks for
    restraint and this enforces it.
    """
    if not draft.price_basis:
        return draft
    lowered = source_text.lower()
    if not any(term in lowered for term in BASIS_TERMS):
        draft.price_basis = UNKNOWN_STR
    return draft


def parse(sentence: str) -> PurchaseDraft:
    draft = llm.structured(
        f"Today is {date.today().isoformat()}.\n\n"
        f"Buyer said: {sentence}\n\nExtract the purchase.",
        PurchaseDraft,
        system=SYSTEM,
    )
    return _scrub_inferred_basis(draft, sentence)


def missing(draft: PurchaseDraft) -> list[str]:
    return [field for field in QUESTIONS if _is_missing(draft, field)]


def question_block(draft: PurchaseDraft) -> str:
    """One batched message, not an interrogation."""
    gaps = missing(draft)
    if not gaps:
        return ""
    lines = [f"  {field:<14} {QUESTIONS[field]}" for field in gaps]
    return "Filling the gaps:\n" + "\n".join(lines)


def merge(draft: PurchaseDraft, reply: str) -> PurchaseDraft:
    """Fold his answer into the draft without losing what was already known."""
    known = {
        field: getattr(draft, field)
        for field in draft.model_fields
        if not _is_missing(draft, field)
    }
    updated = llm.structured(
        f"Today is {date.today().isoformat()}.\n\n"
        f"Already known: {known}\n\n"
        f"He then said: {reply}\n\n"
        "Return the complete record, keeping the known values unless he "
        "corrected them.",
        PurchaseDraft,
        system=SYSTEM,
    )
    # Same guard on the reply: he must state a basis, not have one inferred.
    if _is_missing(draft, "price_basis"):
        updated = _scrub_inferred_basis(updated, reply)
    return updated


def _derive(draft: PurchaseDraft) -> dict[str, Any]:
    """Compute what can be computed, so History can query it directly."""
    goods = draft.unit_price * draft.quantity if draft.quantity > 0 else 0.0
    freight = max(draft.freight_paid, 0.0)
    duties = max(draft.duty_vat_paid, 0.0)

    basis = (draft.price_basis or "").upper()
    if basis in {"DDP", "LANDED"}:
        total = goods                      # already all-in
    else:
        total = goods + freight + duties

    per_unit = round(total / draft.quantity, 2) if draft.quantity > 0 else None

    lead = draft.lead_days if draft.lead_days > 0 else None
    if lead is None and draft.ordered_on and draft.received_on:
        try:
            lead = (
                datetime.fromisoformat(draft.received_on)
                - datetime.fromisoformat(draft.ordered_on)
            ).days
        except ValueError:
            lead = None

    return {
        "total_landed": round(total, 2) if total else None,
        "landed_per_unit": per_unit,
        "lead_days_actual": lead,
    }


def save(draft: PurchaseDraft) -> dict[str, Any]:
    """Persist, and let the purchase update supplier trust and source weight."""
    gaps = [field for field in REQUIRED if _is_missing(draft, field)]
    if gaps:
        raise ValueError(f"cannot save without: {', '.join(gaps)}")

    derived = _derive(draft)

    with session() as conn:
        row = conn.execute(
            "SELECT id FROM parts WHERE mpn = ?", (draft.part_number,)
        ).fetchone()
        if row:
            part_id = row["id"]
        else:
            part_id = conn.execute(
                "INSERT INTO parts (mpn, lifecycle) VALUES (?, 'unknown')",
                (draft.part_number,),
            ).lastrowid

        row = conn.execute(
            "SELECT id, times_used, trust_score FROM suppliers WHERE name = ?",
            (draft.supplier_name,),
        ).fetchone()
        if row:
            supplier_id, times_used, trust = row["id"], row["times_used"], row["trust_score"]
        else:
            supplier_id = conn.execute(
                "INSERT INTO suppliers (name) VALUES (?)", (draft.supplier_name,)
            ).lastrowid
            times_used, trust = 0, 0.5

        purchase_id = conn.execute(
            """
            INSERT INTO purchases (
                part_id, supplier_id, quantity, unit_price, currency,
                price_basis, freight_paid, duty_vat_paid, total_landed,
                landed_per_unit, ordered_on, received_on, lead_days_actual,
                condition, would_buy_again
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                part_id, supplier_id, draft.quantity, draft.unit_price,
                draft.currency or "USD", draft.price_basis.upper(),
                draft.freight_paid if draft.freight_paid >= 0 else None,
                draft.duty_vat_paid if draft.duty_vat_paid >= 0 else None,
                derived["total_landed"], derived["landed_per_unit"],
                draft.ordered_on or None, draft.received_on or None,
                derived["lead_days_actual"],
                draft.condition or None,
                draft.would_buy_again if draft.would_buy_again >= 0 else None,
            ),
        ).lastrowid

        # A supplier we have actually bought from outranks an unknown one.
        went_well = draft.condition in ("", "good") and draft.would_buy_again != 0
        trust = min(0.95, trust + (0.12 if went_well else -0.2))
        conn.execute(
            "UPDATE suppliers SET times_used = ?, trust_score = ? WHERE id = ?",
            (times_used + 1, round(trust, 3), supplier_id),
        )

        # Promote the source this supplier came from (design doc S04:
        # the registry learns instead of being maintained by hand).
        conn.execute(
            "UPDATE sources SET weight = MIN(1.6, weight + 0.05) "
            "WHERE id = (SELECT source_id FROM suppliers WHERE id = ?)",
            (supplier_id,),
        )

    return {"purchase_id": purchase_id, "supplier_id": supplier_id, **derived}


def implied_freight_rate(draft: PurchaseDraft, unit_weight_kg: float | None) -> float | None:
    """Back out $/kg from a real invoice, to replace the default estimate."""
    if draft.freight_paid <= 0 or not unit_weight_kg or draft.quantity <= 0:
        return None
    total_weight = unit_weight_kg * draft.quantity
    return round(draft.freight_paid / total_weight, 2) if total_weight else None

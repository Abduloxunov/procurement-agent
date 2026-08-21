"""Build the comparison table -- the deliverable.

Every row states where it came from. A verified quote and a scraped listing
are not the same evidence and the table never pretends otherwise.
"""

from __future__ import annotations

import json
from typing import Any

from app.analysis import landed
from app.db.database import session

# Rows the buyer must see but must not rank against priced options.
UNPRICED_STATES = {"needs_rfq", "rfq_sent"}


def _request(request_id: int) -> dict[str, Any]:
    with session() as conn:
        row = conn.execute(
            "SELECT r.*, p.mpn, p.hs_code, p.weight_kg "
            "FROM requests r LEFT JOIN parts p ON p.id = r.part_id "
            "WHERE r.id = ?",
            (request_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"No request #{request_id}")
    return dict(row)


def _offers(request_id: int) -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT o.*, s.name AS supplier_name, s.trust_score,
                   src.name AS source_name, src.weight AS source_weight
            FROM offers o
            LEFT JOIN suppliers s   ON s.id = o.supplier_id
            LEFT JOIN sources   src ON src.id = s.source_id
            WHERE o.request_id = ?
            """,
            (request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _store(offer_id: int, result: dict[str, Any]) -> None:
    with session() as conn:
        conn.execute("DELETE FROM landed_costs WHERE offer_id = ?", (offer_id,))
        conn.execute(
            """
            INSERT INTO landed_costs (
                offer_id, quantity, goods, freight, freight_mode, freight_source,
                insurance, cif, hs_code, duty_rate, duty, vat_rate, vat, fees,
                total, per_unit, assumptions
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                offer_id, result["quantity"], result["goods"], result["freight"],
                result["freight_mode"], result["assumptions"].get("freight_source"),
                result["insurance"], result["cif"], result["hs_code"],
                result["duty_rate"], result["duty"], result["vat_rate"],
                result["vat"], result["fees"], result["total"], result["per_unit"],
                json.dumps(result["assumptions"]),
            ),
        )


def build(
    request_id: int,
    quantity: int | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Cost every offer at `quantity` and rank them."""
    request = _request(request_id)
    qty = quantity or request.get("quantity") or 1

    priced: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    disqualified: list[dict[str, Any]] = []

    for offer in _offers(request_id):
        base = {
            "offer_id": offer["id"],
            "supplier": offer["supplier_name"] or "?",
            "source": offer["source_name"] or "web",
            "rung": offer["rung"],
            "confidence": offer["confidence"] or 0.0,
            "trust": offer["trust_score"] or 0.5,
            "state": offer["state"],
            "url": offer["url"],
            "moq": offer["moq"],
            "lead_days": offer["lead_days"],
            "incoterm": offer["incoterm"],
        }

        if offer["state"] == "disqualified":
            disqualified.append({**base, "reason": offer["disqualified_reason"]})
            continue

        if offer["unit_price"] is None or offer["state"] in UNPRICED_STATES:
            unpriced.append(base)
            continue

        cost = landed.compute(
            unit_price=offer["unit_price"],
            currency_code=offer["currency"] or "USD",
            quantity=qty,
            incoterm=offer["incoterm"],
            hs_code=request.get("hs_code"),
            weight_kg_per_unit=request.get("weight_kg"),
        )
        if persist:
            _store(offer["id"], cost)

        # MOQ is not a disqualifier -- it is a fact the buyer must see.
        below_moq = bool(offer["moq"] and qty < offer["moq"])
        priced.append({**base, **cost, "below_moq": below_moq})

    priced.sort(key=lambda row: row["per_unit"])

    return {
        "request_id": request_id,
        "mpn": request.get("mpn"),
        "quantity": qty,
        "priced": priced,
        "unpriced": unpriced,
        "disqualified": disqualified,
    }


def sensitivity(request_id: int, quantities: list[int]) -> list[dict[str, Any]]:
    """How the winner changes with volume.

    His quantities vary, so this is a first-class feature rather than a
    footnote -- MOQ and tier breaks routinely reshuffle the ranking.
    """
    rows = []
    for qty in quantities:
        table = build(request_id, qty, persist=False)
        winner = next((r for r in table["priced"] if not r["below_moq"]), None)
        winner = winner or (table["priced"][0] if table["priced"] else None)
        rows.append(
            {
                "quantity": qty,
                "winner": winner["supplier"] if winner else None,
                "per_unit": winner["per_unit"] if winner else None,
                "below_moq": winner["below_moq"] if winner else None,
            }
        )
    return rows

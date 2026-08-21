"""Draft RFQ emails.

The fallback for suppliers who publish no price -- and for CAPTCHA-walled
marketplaces, where an inquiry is the better path anyway since listed prices
there are indicative rather than quantity prices.

Nothing here sends. Drafting and sending are deliberately separate so there
is no code path that reaches a supplier without approval.
"""

from __future__ import annotations

import secrets
from typing import Any

from app import llm
from app.db.database import session

# Goes in the subject so replies match back to a request and supplier
# without guessing.
def new_ref_code() -> str:
    return f"SD-{secrets.token_hex(3).upper()}"


SYSTEM = """You write short, professional RFQ emails from a buyer in Uzbekistan \
to an industrial component supplier, usually in China.

Style: plain, direct, no marketing language, no flattery. Suppliers read these
in a second language, so short sentences and a numbered list of questions.
Never invent company details, names or history.

Always ask for, as a numbered list:
1. Unit price at the stated quantity, and price breaks at other quantities
2. Both FOB and DDP to Tashkent, Uzbekistan, if they can quote DDP
3. Minimum order quantity
4. Lead time from order to dispatch
5. Shipping weight and carton dimensions per unit
6. Payment terms
7. Warranty period

Close by asking them to keep the reference code in the subject when replying."""


def draft_body(
    mpn: str,
    quantity: int,
    supplier_name: str,
    ref_code: str,
    specs: list[str] | None = None,
) -> dict[str, str]:
    spec_block = (
        "Required specifications:\n" + "\n".join(f"- {s}" for s in specs)
        if specs else ""
    )
    body = llm.complete(
        f"Supplier: {supplier_name}\n"
        f"Part: {mpn}\n"
        f"Quantity: {quantity}\n"
        f"Deliver to: Tashkent, Uzbekistan\n"
        f"Reference code: {ref_code}\n"
        f"{spec_block}\n\n"
        "Write the email body only. No subject line.",
        system=SYSTEM,
    ).strip()

    return {
        "subject": f"[{ref_code}] RFQ: {mpn} - quantity {quantity}",
        "body": body,
    }


def draft_for_request(request_id: int, limit: int = 8) -> list[dict[str, Any]]:
    """Draft one email per supplier on this request that has no usable price."""
    with session() as conn:
        request = conn.execute(
            "SELECT r.*, p.mpn FROM requests r LEFT JOIN parts p ON p.id = r.part_id "
            "WHERE r.id = ?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError(f"no request #{request_id}")

        specs = [
            f"{row['spec_key']}: {row['spec_value']}"
            for row in conn.execute(
                "SELECT spec_key, spec_value FROM part_specs "
                "WHERE part_id = ? AND is_mandatory = 1",
                (request["part_id"],),
            )
        ]

        targets = conn.execute(
            """
            SELECT DISTINCT s.id, s.name, s.contact_email
            FROM offers o JOIN suppliers s ON s.id = o.supplier_id
            WHERE o.request_id = ?
              AND (o.unit_price IS NULL OR o.state = 'needs_rfq')
              AND NOT EXISTS (
                  SELECT 1 FROM rfqs q
                  WHERE q.request_id = o.request_id AND q.supplier_id = s.id
              )
            LIMIT ?
            """,
            (request_id, limit),
        ).fetchall()

    drafts = []
    for supplier in targets:
        ref_code = new_ref_code()
        email = draft_body(
            request["mpn"], request["quantity"] or 1,
            supplier["name"], ref_code, specs,
        )
        with session() as conn:
            rfq_id = conn.execute(
                "INSERT INTO rfqs (request_id, supplier_id, ref_code, subject, "
                "body, status) VALUES (?, ?, ?, ?, ?, 'drafted')",
                (request_id, supplier["id"], ref_code,
                 email["subject"], email["body"]),
            ).lastrowid
        drafts.append({
            "rfq_id": rfq_id,
            "supplier": supplier["name"],
            "to": supplier["contact_email"] or "",
            "ref_code": ref_code,
            **email,
        })
    return drafts


def pending(request_id: int | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT q.*, s.name AS supplier, s.contact_email FROM rfqs q "
        "LEFT JOIN suppliers s ON s.id = q.supplier_id WHERE q.status = 'drafted'"
    )
    params: tuple = ()
    if request_id:
        query += " AND q.request_id = ?"
        params = (request_id,)
    with session() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def approve(rfq_id: int) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE rfqs SET status = 'approved' WHERE id = ? AND status = 'drafted'",
            (rfq_id,),
        )


def drop(rfq_id: int) -> None:
    with session() as conn:
        conn.execute("UPDATE rfqs SET status = 'dropped' WHERE id = ?", (rfq_id,))


def set_recipient(supplier_id: int, email: str) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE suppliers SET contact_email = ? WHERE id = ?",
            (email, supplier_id),
        )

"""The specialist nodes.

Each is a plain function taking state and returning a partial update. Only
`supervisor` and `critic` make routing decisions; these just do work and
append evidence.
"""

from __future__ import annotations

from typing import Any

from app import llm, vectorstore
from app.agents import history as history_agent
from app.analysis import compare
from app.db.database import session
from app.graph.state import AskState


def part_node(state: AskState) -> dict[str, Any]:
    """Datasheet RAG. Spec tables live in captioned images, so this reaches
    content that is not in the PDF's extracted text."""
    hits = vectorstore.search(state["question"], limit=4)
    evidence = [
        {
            "agent": "part",
            "ref": f"{hit.get('mpn', '?')} datasheet p{hit.get('page', '?')}"
                   f"{' (figure)' if hit.get('kind') == 'image' else ''}",
            "content": hit["text"][:600],
        }
        for hit in hits
    ]
    note = f"part: {len(evidence)} datasheet chunks" if evidence else "part: nothing indexed"
    return {"steps": [note], "evidence": evidence}


def history_node(state: AskState) -> dict[str, Any]:
    """Text-to-SQL over what we actually bought."""
    result = history_agent.ask(state["question"], explain=False)
    if not result["ok"]:
        return {"steps": [f"history: {result['answer']}"]}
    if not result["rows"]:
        return {"steps": ["history: no matching records"]}

    return {
        "steps": [f"history: {len(result['rows'])} rows"],
        "evidence": [
            {
                "agent": "history",
                "ref": f"purchases table via SQL: {result['sql'][:160]}",
                "content": str(result["rows"][:10]),
            }
        ],
    }


def scout_node(state: AskState) -> dict[str, Any]:
    """Web research.

    Reads offers already collected for this request rather than searching
    again. A live search costs a paid credit and the workflow pipeline is
    what runs those -- the chat graph should not quietly burn the budget.
    """
    request_id = state.get("request_id")
    if not request_id:
        return {"steps": ["scout: no request in context, nothing to read"]}

    with session() as conn:
        rows = conn.execute(
            """
            SELECT s.name AS supplier, o.unit_price, o.currency, o.moq,
                   o.lead_days, o.incoterm, o.state, o.rung, o.url
            FROM offers o LEFT JOIN suppliers s ON s.id = o.supplier_id
            WHERE o.request_id = ?
            """,
            (request_id,),
        ).fetchall()

    if not rows:
        return {"steps": ["scout: no offers collected yet -- run the scout script"]}

    return {
        "steps": [f"scout: {len(rows)} offers on record"],
        "evidence": [
            {
                "agent": "scout",
                "ref": row["url"] or "no url",
                "content": (
                    f"{row['supplier']}: {row['unit_price']} {row['currency'] or ''} "
                    f"MOQ {row['moq']} lead {row['lead_days']}d "
                    f"{row['incoterm'] or 'incoterm not stated'} "
                    f"[{row['state']}, via {row['rung']}]"
                ),
            }
            for row in rows
        ],
    }


def analyst_node(state: AskState) -> dict[str, Any]:
    """Landed cost and ranking. Arithmetic runs in Python, never in tokens."""
    request_id = state.get("request_id")
    if not request_id:
        return {"steps": ["analyst: no request in context"]}

    table = compare.build(request_id, state.get("quantity"))
    if not table["priced"]:
        return {"steps": ["analyst: no priced offers to cost"]}

    # Always state the USD-converted listed price alongside the original.
    # Handing the model "260 CNY" and "$40 USD" and expecting it to rank them
    # is asking it to do arithmetic in tokens -- it picked $40 as cheaper.
    lines = [
        f"{row['supplier']}: listed {row['unit_price_original']:g} "
        f"{row['currency_original']}"
        f" (= ${row['unit_price_usd']:.2f} USD)"
        f" {row['incoterm']}{' (assumed)' if row['incoterm_assumed'] else ''}"
        f" -> landed ${row['per_unit']:.2f}/unit at qty {table['quantity']}"
        + (f"  [{'; '.join(row['flags'])}]" if row["flags"] else "")
        for row in table["priced"]
    ]

    cheapest_listed = min(table["priced"], key=lambda r: r["unit_price_usd"])
    cheapest_landed = table["priced"][0]
    lines.append(
        f"CHEAPEST LISTED (USD): {cheapest_listed['supplier']} at "
        f"${cheapest_listed['unit_price_usd']:.2f}/unit"
    )
    lines.append(
        f"CHEAPEST LANDED: {cheapest_landed['supplier']} at "
        f"${cheapest_landed['per_unit']:.2f}/unit"
    )
    # State the relationship outright. Left to infer it, the model compares a
    # landed figure against a listed one and calls one "cheaper" than the
    # other -- they are different measures and not comparable.
    if cheapest_listed["offer_id"] != cheapest_landed["offer_id"]:
        lines.append(
            f"DIFFERENT SUPPLIERS WIN: {cheapest_listed['supplier']} has the "
            f"lowest listed price but {cheapest_landed['supplier']} is cheapest "
            f"once landed. The lowest listed price is not the cheapest option."
        )
    else:
        lines.append(
            f"SAME SUPPLIER WINS BOTH: {cheapest_landed['supplier']} has both "
            f"the lowest listed price and the lowest landed cost, so there is "
            f"no inversion here."
        )
    lines.append(
        "Note: listed and landed are different measures. Never describe a "
        "landed figure as cheaper or dearer than a listed one."
    )
    for row in table["unpriced"]:
        lines.append(f"{row['supplier']}: no published price ({row['state']})")

    return {
        "steps": [f"analyst: costed {len(table['priced'])} offers"],
        "evidence": [
            {
                "agent": "analyst",
                "ref": f"landed_costs computed for request #{request_id}",
                "content": "\n".join(lines),
            }
        ],
    }


GENERATE_SYSTEM = """You answer an industrial parts buyer using only the \
evidence given.

Rules:
- Every number you state must appear in the evidence. Never compute a new one.
- Attribute each fact: "per the datasheet", "we paid this in June", "listed on
  made-in-china".
- Compare landed cost, not listed price, when ranking options -- and say so.
- If the evidence does not answer the question, say exactly what is missing
  and which step would get it. A short honest answer beats a padded one.
- No preamble. No bullet lists unless comparing three or more things."""


def generate_node(state: AskState) -> dict[str, Any]:
    evidence = state.get("evidence", [])
    if not evidence:
        return {
            "answer": "I have no evidence to answer that yet. Run a search for "
                      "the part first, or log a purchase if you are asking "
                      "about history.",
            "steps": ["generate: no evidence"],
        }

    block = "\n\n".join(
        f"[{item['agent']}] ref: {item['ref']}\n{item['content']}"
        for item in evidence
    )
    critique = state.get("critique", "")
    correction = (
        f"\n\nYour previous answer was rejected: {critique}\nFix exactly that."
        if critique else ""
    )

    answer = llm.complete(
        f"Question: {state['question']}\n\n--- evidence ---\n{block}\n--- end ---"
        f"{correction}",
        system=GENERATE_SYSTEM,
        role="reasoning",
    )
    return {"answer": answer.strip(), "steps": ["generate"]}

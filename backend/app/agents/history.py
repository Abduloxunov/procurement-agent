"""History agent -- text-to-SQL over what we have actually bought.

Only worth having because of the purchase log. Without it there is nothing
to query and this is a toy; with it, "what have we paid for RS485 soil
sensors, and who delivered on time?" is a real question with a real answer.

Read-only by construction: the generated SQL must be a single SELECT, it is
checked before execution, and the connection runs against a query_only
database. Three independent guards, because one is not enough when the
statement is written by a model.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app import llm
from app.db.database import connect

MAX_ROWS = 50

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|truncate)\b",
    re.IGNORECASE,
)

SCHEMA_HINT = """
TABLES

parts(id, mpn, manufacturer, description, category, hs_code, weight_kg, lifecycle)
suppliers(id, name, source_id, country, trust_score, times_used, on_time_rate)
sources(id, name, domain, kind, weight, times_used)
purchases(id, part_id, supplier_id, quantity, unit_price, currency,
          price_basis, freight_paid, duty_vat_paid, total_landed,
          landed_per_unit, ordered_on, received_on, lead_days_actual,
          condition, would_buy_again)
offers(id, request_id, supplier_id, part_id, quantity, unit_price, currency,
       incoterm, moq, lead_days, state, rung, confidence, captured_at)
landed_costs(id, offer_id, quantity, goods, freight, duty, vat, total, per_unit)
requests(id, title, part_id, quantity, need_by, status, created_at)

HOW THE TABLES JOIN -- use exactly these, never invent a join

    purchases.part_id      -> parts.id
    purchases.supplier_id  -> suppliers.id
    offers.part_id         -> parts.id
    offers.supplier_id     -> suppliers.id
    offers.request_id      -> requests.id
    landed_costs.offer_id  -> offers.id
    suppliers.source_id    -> sources.id
    requests.part_id       -> parts.id

Note that suppliers joins on supplier_id ONLY. Joining suppliers on a
part_id is always wrong.

MEANING

- purchases = history, what we actually paid. offers = research, what we found.
- price_basis: FOB/EXW exclude freight and duty; DDP/landed include them.
  landed_per_unit is the only comparable cost across rows.
- lead_days_actual is real, measured. offers.lead_days is a supplier promise.
- Dates are TEXT 'YYYY-MM-DD'. Use date() for arithmetic.

EXAMPLES

Q: what have we paid for FST100 sensors?
SELECT p.mpn, s.name AS supplier, pu.quantity, pu.unit_price, pu.price_basis,
       pu.landed_per_unit, pu.lead_days_actual, pu.ordered_on
FROM purchases pu
JOIN parts p     ON p.id = pu.part_id
JOIN suppliers s ON s.id = pu.supplier_id
WHERE p.mpn LIKE '%FST100%'
ORDER BY pu.ordered_on DESC
LIMIT 50;

Q: which suppliers delivered late?
SELECT s.name AS supplier, COUNT(*) AS orders,
       AVG(pu.lead_days_actual) AS avg_lead_days
FROM purchases pu
JOIN suppliers s ON s.id = pu.supplier_id
WHERE pu.lead_days_actual IS NOT NULL
GROUP BY s.id
ORDER BY avg_lead_days DESC
LIMIT 50;
"""

SYSTEM = f"""You write SQLite SELECT queries for a procurement database.

{SCHEMA_HINT}

RULES
- One SELECT statement only. Never write, never modify.
- Match part numbers with LIKE '%...%', never with =. Buyers type 'FST100'
  when the stored value is 'FST100-2006A'; an exact match silently returns
  nothing, which is worse than an error.
- Match supplier and manufacturer names with LIKE too, for the same reason.
- Always join to parts and suppliers for names -- raw ids are useless.
- Prefer landed_per_unit over unit_price when comparing costs.
- Give computed columns an alias.
- LIMIT {MAX_ROWS}.
- If the schema cannot answer the question, explain why in `reason` and
  return an empty sql string rather than guessing."""


class SqlQuery(BaseModel):
    sql: str = Field(description="A single SELECT, or '' if unanswerable.")
    reason: str = Field(description="One line: what this query returns, or why not.")


def is_safe(sql: str) -> tuple[bool, str]:
    text = sql.strip().rstrip(";").strip()
    if not text:
        return False, "empty query"
    if not text.lower().startswith(("select", "with")):
        return False, "not a SELECT"
    if ";" in text:
        return False, "multiple statements"
    if FORBIDDEN.search(text):
        return False, "contains a write keyword"
    return True, ""


def run_sql(sql: str) -> list[dict[str, Any]]:
    """Execute against a query_only connection -- the last line of defence."""
    conn = connect()
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(sql).fetchmany(MAX_ROWS)
        return [dict(row) for row in rows]
    finally:
        conn.close()


def ask(question: str, *, explain: bool = True) -> dict[str, Any]:
    """Question in, rows out, plus a sentence a human can read."""
    query = llm.structured(
        f"Question: {question}", SqlQuery, system=SYSTEM, role="reasoning"
    )

    if not query.sql.strip():
        return {"sql": "", "rows": [], "answer": query.reason, "ok": False}

    safe, why = is_safe(query.sql)
    if not safe:
        return {
            "sql": query.sql, "rows": [], "ok": False,
            "answer": f"refused to run this query: {why}",
        }

    try:
        rows = run_sql(query.sql)
    except Exception as exc:  # noqa: BLE001
        return {
            "sql": query.sql, "rows": [], "ok": False,
            "answer": f"query failed: {str(exc)[:160]}",
        }

    result = {"sql": query.sql, "rows": rows, "ok": True, "answer": query.reason}

    if explain and rows:
        result["answer"] = llm.complete(
            f"Question: {question}\n\nRows: {rows}\n\n"
            "Answer in one or two sentences.",
            system=(
                "You report procurement data plainly. No preamble.\n"
                "Quote only numbers that appear literally in the rows. Never "
                "multiply, sum or otherwise compute a new figure -- arithmetic "
                "belongs in SQL, and a number you derive in prose will "
                "silently disagree with the stored value. If the question "
                "needs a total that is not in the rows, say it is not there."
            ),
        ).strip()
    elif explain and not rows:
        result["answer"] = "No matching records yet."

    return result

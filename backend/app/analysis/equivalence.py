"""Is this cheaper part actually a substitute?

The question with the most money attached and the highest chance of an
expensive wrong answer. Searching wide guarantees it comes up constantly --
Baidu will surface a dozen near-identical-looking sensors at half the price.

The split that matters:
  - the MODEL judges compatibility per parameter (is RS485 interchangeable
    with 4-20 mA? no) because that needs domain semantics
  - the CODE decides disqualification, because "fails a mandatory spec"
    must never be a judgement call

A part failing any mandatory spec is never ranked and never recommended.
It is reported as disqualified, with the failing parameter named, so he can
overrule it if he knows something the datasheet does not.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app import llm
from app.db.database import session

Verdict = Literal["match", "hard_fail", "soft_diff", "unknown"]


class SpecVerdict(BaseModel):
    spec_key: str = Field(description="The parameter being compared.")
    required: str = Field(description="What the reference part specifies.")
    candidate: str = Field(description="What the alternative specifies, or '' if absent.")
    verdict: str = Field(
        description="One of: match, hard_fail, soft_diff, unknown.\n"
        "hard_fail = physically or electrically incompatible, no price makes "
        "it acceptable (different output signal, incompatible supply voltage, "
        "insufficient IP rating, wrong thread or mounting).\n"
        "soft_diff = a real difference that is a trade-off, not a blocker "
        "(tighter or looser accuracy, different cable length, shorter warranty).\n"
        "unknown = the candidate does not state this parameter. Never guess it."
    )
    note: str = Field(description="One short line explaining the verdict.")


class Comparison(BaseModel):
    verdicts: list[SpecVerdict]
    summary: str = Field(description="Two sentences a buyer can act on.")


SYSTEM = """You compare industrial component specifications for a buyer \
deciding whether an alternative part can substitute for a reference part.

Be conservative. A wrong 'match' costs real money and a returned shipment.

Hard failures include, but are not limited to:
- different output signal (RS485 vs 4-20 mA vs 0-10 V are NOT interchangeable)
- supply voltage ranges that do not overlap the available supply
- IP rating below what the application requires
- different thread, mounting or probe material
- a communication protocol the controller cannot speak

If the candidate does not state a parameter, the verdict is 'unknown'. Never
infer a value from the product name, the price, or a similar model."""


def specs_for(mpn: str) -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT ps.spec_key, ps.spec_value, ps.unit, ps.is_mandatory, ps.source
            FROM part_specs ps JOIN parts p ON p.id = ps.part_id
            WHERE p.mpn = ?
            ORDER BY ps.is_mandatory DESC, ps.spec_key
            """,
            (mpn,),
        ).fetchall()
    return [dict(row) for row in rows]


def mandatory_keys(mpn: str) -> set[str]:
    return {
        spec["spec_key"].lower()
        for spec in specs_for(mpn)
        if spec["is_mandatory"]
    }


def _format(specs: list[dict[str, Any]]) -> str:
    if not specs:
        return "(none recorded)"
    return "\n".join(
        f"- {s['spec_key']}: {s['spec_value']}{' ' + s['unit'] if s['unit'] else ''}"
        f"{'  [MANDATORY]' if s['is_mandatory'] else ''}"
        for s in specs
    )


def compare(
    reference_mpn: str,
    candidate_mpn: str,
    *,
    candidate_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare a candidate against a reference, parameter by parameter."""
    reference = specs_for(reference_mpn)
    candidate = candidate_specs if candidate_specs is not None else specs_for(candidate_mpn)

    if not reference:
        return {"error": f"no specifications recorded for {reference_mpn}"}
    if not candidate:
        return {"error": f"no specifications recorded for {candidate_mpn}"}

    result = llm.structured(
        f"Reference part {reference_mpn}:\n{_format(reference)}\n\n"
        f"Candidate part {candidate_mpn}:\n{_format(candidate)}\n\n"
        "Compare every parameter of the reference against the candidate.",
        Comparison,
        system=SYSTEM,
        role="reasoning",
    )

    required = mandatory_keys(reference_mpn)
    hard, soft, unknown, matched = [], [], [], []

    for verdict in result.verdicts:
        row = verdict.model_dump()
        row["is_mandatory"] = verdict.spec_key.lower() in required
        bucket = {
            "hard_fail": hard, "soft_diff": soft, "unknown": unknown,
        }.get(verdict.verdict, matched)
        bucket.append(row)

    # Disqualification is decided here, in code. A model that returns
    # 'hard_fail' on a mandatory parameter cannot then talk itself out of it.
    blockers = [row for row in hard if row["is_mandatory"]]
    unknown_mandatory = [row for row in unknown if row["is_mandatory"]]

    return {
        "reference": reference_mpn,
        "candidate": candidate_mpn,
        "compatible": not blockers,
        "blockers": blockers,
        "hard_failures": hard,
        "soft_differences": soft,
        "unknowns": unknown,
        "matches": matched,
        # An unstated mandatory parameter is not a pass -- it is a question
        # for the supplier, and it must be asked before ordering.
        "questions_for_supplier": [
            f"Please confirm {row['spec_key']} -- we require "
            f"{row['required']} and your listing does not state it."
            for row in unknown_mandatory
        ],
        "summary": result.summary,
    }

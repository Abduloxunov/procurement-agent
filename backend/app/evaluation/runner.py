"""Run the golden set and score it.

Deterministic cases run first and free. If they fail, nothing else is worth
measuring -- the arithmetic underneath the whole product is wrong.
"""

from __future__ import annotations

import time
from typing import Any

from app.analysis import landed
from app.evaluation.cases import (
    ANSWER_CASES,
    COST_CASES,
    ROUTING_CASES,
    AnswerCase,
    CostCase,
    RoutingCase,
)
from app.graph import build
from app.graph.supervisor import supervisor_node


# ------------------------------------------------------------ invariants ---

def _check_invariant(name: str, result: dict[str, Any], case: CostCase) -> tuple[bool, str]:
    if name == "under_usd_60_goods":
        # 260 CNY is roughly $36-40. If this is near 260, no conversion happened.
        usd = result["unit_price_usd"]
        ok = 30 < usd < 50
        return ok, f"unit_price_usd={usd:.2f} (expected 30-50, i.e. converted)"

    if name == "exw_dearer_than_ddp":
        ddp = landed.compute(**{**case.args, "incoterm": "DDP"})
        ok = result["per_unit"] > ddp["per_unit"]
        return ok, f"EXW {result['per_unit']:.2f} vs DDP {ddp['per_unit']:.2f}"

    if name == "amortises_with_quantity":
        bigger = landed.compute(**{**case.args, "quantity": case.args["quantity"] * 10})
        ok = bigger["per_unit"] < result["per_unit"]
        return ok, (
            f"qty {case.args['quantity']}: {result['per_unit']:.2f} -> "
            f"qty {case.args['quantity'] * 10}: {bigger['per_unit']:.2f}"
        )

    if name == "flags_assumed_incoterm":
        ok = result["incoterm_assumed"] and any("assumed" in f for f in result["flags"])
        return ok, f"flags={result['flags']}"

    if name == "flags_unverified_duty":
        ok = any("unverified" in f for f in result["flags"])
        return ok, f"flags={result['flags']}"

    if name == "flags_estimated_weight":
        ok = any("weight estimated" in f for f in result["flags"])
        return ok, f"flags={result['flags']}"

    return False, f"unknown invariant {name}"


def run_cost_cases() -> list[dict[str, Any]]:
    results = []
    for case in COST_CASES:
        try:
            computed = landed.compute(**case.args)
        except Exception as exc:  # noqa: BLE001
            results.append({"name": case.name, "passed": False,
                            "detail": f"raised {type(exc).__name__}: {exc}"})
            continue

        checks, details = [], []

        if case.expect_per_unit is not None:
            delta = abs(computed["per_unit"] - case.expect_per_unit)
            ok = delta <= case.tolerance
            checks.append(ok)
            details.append(
                f"per_unit {computed['per_unit']:.2f} "
                f"(expected {case.expect_per_unit:.2f})"
            )

        for invariant in case.invariants:
            ok, detail = _check_invariant(invariant, computed, case)
            checks.append(ok)
            details.append(f"{invariant}: {detail}")

        results.append({
            "name": case.name,
            "passed": all(checks) if checks else False,
            "detail": " | ".join(details),
        })
    return results


# --------------------------------------------------------------- routing ---

def run_routing_cases() -> list[dict[str, Any]]:
    results = []
    for case in ROUTING_CASES:
        state = {"question": case.question, "steps": [], "evidence": [],
                 "hops": 0, "revisions": 0}
        try:
            update = supervisor_node(state)  # type: ignore[arg-type]
            chosen = update.get("plan", "?")
        except Exception as exc:  # noqa: BLE001
            results.append({"name": case.name, "passed": False,
                            "detail": f"raised {type(exc).__name__}"})
            continue

        acceptable = {case.expect, *case.also_acceptable}
        results.append({
            "name": case.name,
            "passed": chosen in acceptable,
            "detail": f"chose '{chosen}', wanted {sorted(acceptable)}",
        })
    return results


# ---------------------------------------------------------------- answers ---

def run_answer_cases() -> list[dict[str, Any]]:
    results = []
    for index, case in enumerate(ANSWER_CASES):
        started = time.time()
        try:
            state = build.ask(
                case.question,
                request_id=case.request_id,
                quantity=case.quantity,
                thread=f"eval-{index}",
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"name": case.name, "passed": False,
                            "detail": f"raised {type(exc).__name__}: {exc}",
                            "seconds": round(time.time() - started, 1)})
            continue

        answer = (state.get("answer") or "").lower()
        problems = []

        for needle in case.must_contain:
            if needle.lower() not in answer:
                problems.append(f"missing '{needle}'")
        for needle in case.must_not_contain:
            if needle.lower() in answer:
                problems.append(f"contains forbidden '{needle}'")
        if case.must_be_grounded and state.get("verdict") != "approved":
            problems.append(f"verdict={state.get('verdict')}")

        results.append({
            "name": case.name,
            "passed": not problems,
            "detail": "; ".join(problems) or "ok",
            "seconds": round(time.time() - started, 1),
            "hops": state.get("hops", 0),
            "revisions": state.get("revisions", 0),
            "answer": (state.get("answer") or "")[:160],
        })
    return results


# ------------------------------------------------------------------ report ---

def summarise(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    lines = {}
    for group, results in groups.items():
        passed = sum(1 for r in results if r["passed"])
        lines[group] = {
            "passed": passed,
            "total": len(results),
            "rate": round(passed / len(results), 3) if results else 0.0,
        }
    total = sum(v["total"] for v in lines.values())
    passed = sum(v["passed"] for v in lines.values())
    lines["overall"] = {
        "passed": passed,
        "total": total,
        "rate": round(passed / total, 3) if total else 0.0,
    }
    return lines

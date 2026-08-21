"""Run the golden set.

    python scripts/evaluate.py               # everything
    python scripts/evaluate.py --cost        # deterministic only, free
    python scripts/evaluate.py --routing     # supervisor accuracy
    python scripts/evaluate.py --answers     # end-to-end

Deterministic cases cost nothing and must always pass. Routing and answer
cases each cost a handful of model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.config import get_settings  # noqa: E402
from app.evaluation import runner  # noqa: E402

WIDTH = 84


def report(title: str, results: list[dict]) -> None:
    print(f"\n{title}")
    print("-" * WIDTH)
    for row in results:
        mark = "PASS" if row["passed"] else "FAIL"
        timing = f"  {row['seconds']:>4.1f}s" if "seconds" in row else ""
        print(f"  [{mark}] {row['name'][:52]:<54}{timing}")
        if not row["passed"] or row.get("detail") not in (None, "ok"):
            print(f"         {row['detail'][:100]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", action="store_true")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--answers", action="store_true")
    parser.add_argument("--json", help="Write full results to this file")
    args = parser.parse_args()

    run_all = not (args.cost or args.routing or args.answers)
    settings = get_settings()

    print("=" * WIDTH)
    print("SOURCING DESK -- GOLDEN SET")
    print(f"default   {settings.model_default}")
    print(f"reasoning {settings.model_reasoning}")
    print("=" * WIDTH)

    groups: dict[str, list[dict]] = {}
    started = time.time()

    if run_all or args.cost:
        groups["costing"] = runner.run_cost_cases()
        report("COSTING -- deterministic, no model", groups["costing"])

        if any(not r["passed"] for r in groups["costing"]):
            print("\n! deterministic cases failed -- the arithmetic under the "
                  "whole product is wrong. Fix before measuring anything else.")

    if run_all or args.routing:
        groups["routing"] = runner.run_routing_cases()
        report("ROUTING -- does the supervisor pick the right specialist?",
               groups["routing"])

    if run_all or args.answers:
        groups["answers"] = runner.run_answer_cases()
        report("ANSWERS -- end to end through the graph", groups["answers"])

    summary = runner.summarise(groups)
    print("\n" + "=" * WIDTH)
    print(f"{'group':<14} {'passed':>8} {'total':>7} {'rate':>8}")
    print("-" * WIDTH)
    for group, stats in summary.items():
        divider = "-" * WIDTH if group == "overall" else ""
        if divider:
            print(divider)
        print(f"{group:<14} {stats['passed']:>8} {stats['total']:>7} "
              f"{stats['rate']:>7.0%}")
    print(f"\nelapsed {time.time() - started:.0f}s")

    if "answers" in groups:
        timed = [r for r in groups["answers"] if "seconds" in r]
        if timed:
            avg = sum(r["seconds"] for r in timed) / len(timed)
            revisions = sum(r.get("revisions", 0) for r in timed)
            print(f"avg answer latency {avg:.1f}s · {revisions} critic revisions")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "groups": groups}, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")

    if summary["overall"]["rate"] < 1.0:
        sys.exit(1)


if __name__ == "__main__":
    main()

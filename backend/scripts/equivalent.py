"""Check whether a candidate part can substitute for a reference part.

    python scripts/equivalent.py FST100-2006A RS-ECTH-N01

Both parts need specs on record -- from a datasheet, a listing, or entered
by hand. Use --demo to run against a built-in incompatible candidate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.analysis import equivalence  # noqa: E402

# A realistic near-miss: same measurements, wrong output signal.
DEMO_CANDIDATE = [
    {"spec_key": "output_signal", "spec_value": "4-20 mA analog", "unit": None,
     "is_mandatory": 0, "source": "listing"},
    {"spec_key": "supply_voltage", "spec_value": "12-24", "unit": "VDC",
     "is_mandatory": 0, "source": "listing"},
    {"spec_key": "ip_rating", "spec_value": "IP68", "unit": None,
     "is_mandatory": 0, "source": "listing"},
    {"spec_key": "moisture_accuracy", "spec_value": "±2", "unit": "%",
     "is_mandatory": 0, "source": "listing"},
    {"spec_key": "temperature_range", "spec_value": "-40 to 80", "unit": "C",
     "is_mandatory": 0, "source": "listing"},
]


def show(rows: list[dict], title: str, marker: str) -> None:
    if not rows:
        return
    print(f"\n{title}")
    for row in rows:
        star = " *MANDATORY*" if row.get("is_mandatory") else ""
        print(f"  {marker} {row['spec_key']}{star}")
        print(f"      need: {row['required'] or '-'}")
        print(f"      has:  {row['candidate'] or '(not stated)'}")
        if row.get("note"):
            print(f"      {row['note']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference")
    parser.add_argument("candidate")
    parser.add_argument("--demo", action="store_true",
                        help="Use a built-in candidate with a 4-20 mA output")
    args = parser.parse_args()

    result = equivalence.compare(
        args.reference,
        args.candidate,
        candidate_specs=DEMO_CANDIDATE if args.demo else None,
    )

    if "error" in result:
        print(f"! {result['error']}")
        sys.exit(1)

    verdict = "COMPATIBLE" if result["compatible"] else "NOT A SUBSTITUTE"
    print(f"\n{args.candidate}  vs  {args.reference}")
    print("=" * 66)
    print(f"{verdict}")

    if result["blockers"]:
        print("\nblocked on:")
        for row in result["blockers"]:
            print(f"  {row['spec_key']}: need {row['required']}, "
                  f"has {row['candidate']}")

    show(result["hard_failures"], "hard failures -- no price makes these acceptable", "X")
    show(result["soft_differences"], "soft differences -- trade-offs to weigh", "~")
    show(result["unknowns"], "not stated -- ask, do not assume", "?")

    if result["matches"]:
        print(f"\nmatches ({len(result['matches'])})")
        for row in result["matches"]:
            print(f"  = {row['spec_key']}: {row['candidate']}")

    if result["questions_for_supplier"]:
        print("\nask the supplier before ordering:")
        for question in result["questions_for_supplier"]:
            print(f"  - {question}")

    print(f"\n{result['summary']}\n")


if __name__ == "__main__":
    main()

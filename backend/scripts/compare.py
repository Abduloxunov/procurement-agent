"""Show the comparison table for a request.

    python scripts/compare.py 1
    python scripts/compare.py 1 --qty 100
    python scripts/compare.py 1 --sensitivity 10,50,100,500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.analysis import compare  # noqa: E402

WIDTH = 92


def cell(value, width: int, align: str = "<") -> str:
    text = "-" if value is None else str(value)
    return f"{text[:width]:{align}{width}}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_id", type=int)
    parser.add_argument("--qty", type=int, help="Override the request quantity")
    parser.add_argument("--sensitivity", help="Comma-separated quantities, e.g. 10,50,100")
    args = parser.parse_args()

    table = compare.build(args.request_id, args.qty)

    print(f"\n{table['mpn']}  x{table['quantity']}   request #{table['request_id']}")
    print("=" * WIDTH)

    if table["priced"]:
        print(
            f"{'supplier':<24} {'listed':>13} {'incoterm':<9} "
            f"{'freight':>8} {'duty+vat':>9} {'LANDED/u':>10} {'uplift':>7}"
        )
        print("-" * WIDTH)
        for row in table["priced"]:
            listed = f"{row['unit_price_original']:g} {row['currency_original']}"
            term = row["incoterm"] + ("?" if row["incoterm_assumed"] else "")
            print(
                f"{cell(row['supplier'], 24)} {listed:>13} {term:<9} "
                f"{row['freight']:>8.2f} {row['duty'] + row['vat']:>9.2f} "
                f"{row['per_unit']:>10.2f} {row['uplift_pct']:>6.0f}%"
            )
            notes = []
            if row["below_moq"]:
                notes.append(f"MOQ {row['moq']} > order of {table['quantity']}")
            notes.extend(row["flags"])
            for note in notes:
                print(f"{'':<24}   ! {note}")

        best = table["priced"][0]
        print("-" * WIDTH)
        print(
            f"cheapest landed: {best['supplier']} at ${best['per_unit']:.2f}/unit "
            f"(${best['total']:.2f} total)"
        )
        cheapest_listed = min(table["priced"], key=lambda r: r["unit_price_usd"])
        if cheapest_listed["offer_id"] != best["offer_id"]:
            print(
                f"  note: {cheapest_listed['supplier']} has the lower listed price "
                f"but lands higher -- this is the inversion the table exists to catch."
            )
    else:
        print("no priced offers yet")

    if table["unpriced"]:
        print(f"\nno published price -- needs an RFQ ({len(table['unpriced'])})")
        for row in table["unpriced"]:
            print(f"  {cell(row['supplier'], 30)} {row['source']:<16} {row['state']}")

    if table["disqualified"]:
        print(f"\ndisqualified ({len(table['disqualified'])})")
        for row in table["disqualified"]:
            print(f"  {cell(row['supplier'], 30)} {row['reason']}")

    if args.sensitivity:
        quantities = [int(q) for q in args.sensitivity.split(",") if q.strip()]
        print(f"\nquantity sensitivity")
        print("-" * WIDTH)
        print(f"{'qty':>6}  {'winner':<28} {'landed/unit':>12}")
        for row in compare.sensitivity(args.request_id, quantities):
            flag = "  (below MOQ)" if row["below_moq"] else ""
            per_unit = f"{row['per_unit']:.2f}" if row["per_unit"] else "-"
            print(f"{row['quantity']:>6}  {cell(row['winner'], 28)} {per_unit:>12}{flag}")

    print()


if __name__ == "__main__":
    main()

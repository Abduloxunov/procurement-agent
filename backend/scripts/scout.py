"""Run the Scout on a part.

    # search Google + Baidu (needs SERPAPI_KEY)
    python scripts/scout.py FST100-2006A --qty 50

    # skip search, read given pages (needs only OPENROUTER_API_KEY)
    python scripts/scout.py FST100-2006A --qty 50 --url https://... --url https://...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.sources import scout  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Search and extract supplier offers.")
    parser.add_argument("mpn", help="Manufacturer part number, e.g. FST100-2006A")
    parser.add_argument("--qty", type=int, default=1, help="Quantity wanted")
    parser.add_argument(
        "--url", action="append", dest="urls",
        help="Read this page instead of searching. Repeatable.",
    )
    parser.add_argument("--max-pages", type=int, default=6)
    args = parser.parse_args()

    result = scout.run(
        args.mpn,
        args.qty,
        urls=args.urls,
        max_pages=args.max_pages,
    )

    print("\n" + "=" * 66)
    print(
        f"request #{result['request_id']}  "
        f"{result['candidates']} read  "
        f"{result['offers_saved']} offers  "
        f"{result['not_offers']} not offers  "
        f"{len(result['failed'])} failed"
    )

    offers = scout.offers_for(result["request_id"])
    if offers:
        print(
            f"\n{'supplier':<26} {'price':>12} {'moq':>5} {'lead':>6} "
            f"{'inc':<5} {'conf':>5}  state"
        )
        print("-" * 78)
        for offer in offers:
            price = (
                f"{offer['unit_price']:.2f} {offer['currency'] or ''}".strip()
                if offer["unit_price"] is not None else "-"
            )
            print(
                f"{(offer['supplier'] or '?')[:25]:<26} {price:>12} "
                f"{offer['moq'] or '-':>5} {str(offer['lead_days'] or '-'):>6} "
                f"{offer['incoterm'] or '-':<5} {offer['confidence'] or 0:>5.2f}"
                f"  {offer['state']}"
            )

    if result["failed"]:
        print("\nfailed pages")
        for failure in result["failed"]:
            print(f"  [{failure['rung']}] {failure['url'][:60]}")
            print(f"      {failure['error'][:90]}")

    print("\nnote: prices are in each listing's own currency.")
    print("      landed-cost conversion is P2.")


if __name__ == "__main__":
    main()

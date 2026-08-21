"""Log a purchase from one sentence.

    python scripts/log_purchase.py "bought 56 FST100-2006A from Firstrate, \\
        $41 each, came in 18 days"

The agent extracts what it can, asks for the rest in one batched message,
then saves. Add --yes to skip the follow-up and save what was understood.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.memory import purchase  # noqa: E402


def show(draft: purchase.PurchaseDraft) -> None:
    price = f"{draft.unit_price:g} {draft.currency or 'USD'}" if draft.unit_price >= 0 else "-"
    print(f"  part       {draft.part_number or '-'}")
    print(f"  supplier   {draft.supplier_name or '-'}")
    print(f"  quantity   {draft.quantity if draft.quantity > 0 else '-'}")
    print(f"  unit price {price} {draft.price_basis or ''}".rstrip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sentence", help="What you bought, in plain words")
    parser.add_argument("--yes", action="store_true", help="Skip the follow-up")
    args = parser.parse_args()

    print(f'\nyou: "{args.sentence}"\n')
    draft = purchase.parse(args.sentence)
    print("understood:")
    show(draft)

    gaps = purchase.missing(draft)
    if gaps and not args.yes:
        print()
        print(purchase.question_block(draft))
        print("\n(one line covering whatever you know; Enter to skip)")
        reply = input("> ").strip()
        if reply:
            draft = purchase.merge(draft, reply)
            print("\nupdated:")
            show(draft)

    try:
        saved = purchase.save(draft)
    except ValueError as exc:
        print(f"\n! {exc}")
        sys.exit(1)

    print(f"\nsaved purchase #{saved['purchase_id']}")
    if saved["landed_per_unit"]:
        print(f"  landed per unit   ${saved['landed_per_unit']:.2f}")
    if saved["total_landed"]:
        print(f"  total landed      ${saved['total_landed']:.2f}")
    if saved["lead_days_actual"]:
        print(f"  actual lead time  {saved['lead_days_actual']} days")

    rate = purchase.implied_freight_rate(draft, 0.4)
    if rate:
        print(f"  implied freight   ${rate:.2f}/kg -- beats the default estimate")

    print(f"\n{draft.supplier_name} is now a known supplier and ranks higher in future searches.")


if __name__ == "__main__":
    main()

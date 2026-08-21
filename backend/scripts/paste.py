"""Rung 3: paste a page the automated rungs cannot read.

For CAPTCHA-walled marketplaces (Alibaba, 1688) and anything else that
blocks. Open the page in your own browser, select all, copy, paste here.

    python scripts/paste.py FST100-2006A --qty 50 --url https://alibaba.com/...
    # paste, then Ctrl+Z + Enter on Windows (Ctrl+D on Linux/Mac)

    python scripts/paste.py FST100-2006A --qty 50 --file quote.txt

Extraction is identical to the scraped rungs -- only the delivery differs,
and the offer records that it came from a paste.
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
    parser = argparse.ArgumentParser()
    parser.add_argument("mpn")
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--url", default="", help="Where it came from, for the record")
    parser.add_argument("--file", help="Read from a file instead of stdin")
    parser.add_argument("--request", type=int, help="Add to an existing request")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        print("Paste the page text, then Ctrl+Z + Enter (Windows) or Ctrl+D:\n")
        text = sys.stdin.read()

    if len(text.strip()) < 100:
        print("! too little text to extract anything useful")
        sys.exit(1)

    print(f"\nextracting from {len(text)} chars...")
    result = scout.ingest_pasted(
        args.mpn, args.qty, text, url=args.url, request_id=args.request
    )

    if not result.get("offer_id"):
        print(f"! {result.get('error')}")
        sys.exit(1)

    price = (
        f"{result['unit_price']} {result['currency']}"
        if result["unit_price"] >= 0 else "no price stated"
    )
    print(
        f"+ offer #{result['offer_id']} on request #{result['request_id']}\n"
        f"  supplier   {result['supplier'] or '?'}\n"
        f"  price      {price}\n"
        f"  moq        {result['moq']}\n"
        f"  lead       {result['lead_days']} days\n"
        f"  confidence {result['confidence']:.2f}"
    )
    print(f"\nnow run:  python scripts/compare.py {result['request_id']} --qty {args.qty}")


if __name__ == "__main__":
    main()

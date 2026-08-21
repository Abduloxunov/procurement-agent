"""Ask the multi-agent graph a question.

    python scripts/chat.py "what did we pay for FST100 last time?"
    python scripts/chat.py "which supplier is cheapest landed?" --request 1 --qty 50
    python scripts/chat.py --graph      # print the graph as mermaid
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.graph import build  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", help="What to ask")
    parser.add_argument("--request", type=int, help="Sourcing request in context")
    parser.add_argument("--qty", type=int, help="Quantity for landed-cost questions")
    parser.add_argument("--graph", action="store_true", help="Print the graph")
    parser.add_argument("--evidence", action="store_true", help="Show every citation")
    args = parser.parse_args()

    if args.graph:
        print(build.mermaid())
        return

    if not args.question:
        parser.error("a question is required (or use --graph)")

    print(f"\nq: {args.question}")
    print("-" * 72)

    result = build.ask(
        args.question, request_id=args.request, quantity=args.qty
    )

    for step in result.get("steps", []):
        print(f"  {step}")

    print("-" * 72)
    print(f"\n{result.get('answer', '(no answer)')}\n")

    evidence = result.get("evidence", [])
    if args.evidence and evidence:
        print("evidence")
        for item in evidence:
            print(f"  [{item['agent']}] {item['ref']}")
            print(f"      {item['content'][:110].replace(chr(10), ' ')}...")
        print()

    print(
        f"({len(evidence)} evidence items · {result.get('hops', 0)} supervisor hops"
        f" · {result.get('revisions', 0)} revisions · {result.get('verdict', '?')})"
    )


if __name__ == "__main__":
    main()

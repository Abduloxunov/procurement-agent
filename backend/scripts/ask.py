"""Ask the History agent a question about what we have bought.

    python scripts/ask.py "what have we paid for soil sensors?"
    python scripts/ask.py "which suppliers delivered late?" --sql
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.agents import history  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--sql", action="store_true", help="Show the generated SQL")
    args = parser.parse_args()

    print(f'\nq: {args.question}')
    result = history.ask(args.question)

    if args.sql or not result["ok"]:
        print(f"\nsql:\n  {result['sql'] or '(none)'}")

    if result["rows"]:
        columns = list(result["rows"][0].keys())
        print()
        print("  " + " | ".join(f"{c[:18]:<18}" for c in columns))
        print("  " + "-+-".join("-" * 18 for _ in columns))
        for row in result["rows"][:15]:
            print("  " + " | ".join(f"{str(row[c])[:18]:<18}" for c in columns))
        if len(result["rows"]) > 15:
            print(f"  ... {len(result['rows']) - 15} more")

    print(f"\n{result['answer']}\n")


if __name__ == "__main__":
    main()

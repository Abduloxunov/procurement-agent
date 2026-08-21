"""Create the database and seed the source registry.

    python scripts/init_db.py

Safe to run repeatedly -- tables use IF NOT EXISTS and seeding skips rows
that already exist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app.config import get_settings  # noqa: E402
from app.db.database import init_db, session, table_counts  # noqa: E402
from app.db.seed import seed_sources  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete the database first. Destroys all offers and purchases.",
    )
    args = parser.parse_args()

    settings = get_settings()

    if args.reset and settings.db_path.exists():
        settings.db_path.unlink()
        print(f"deleted   {settings.db_path}")

    print(f"database  {settings.db_path}")
    init_db()
    print("schema    created")

    inserted = seed_sources()
    print(f"sources   {inserted} seeded")

    print("\ntables")
    for table, count in table_counts().items():
        print(f"  {table:<16} {count:>4}")

    print("\nsource registry")
    with session() as conn:
        rows = conn.execute(
            "SELECT name, kind, access, weight FROM sources ORDER BY weight DESC, name"
        ).fetchall()
    for row in rows:
        print(
            f"  {row['weight']:.1f}  {row['name']:<18} "
            f"{row['kind']:<13} {row['access']}"
        )


if __name__ == "__main__":
    main()

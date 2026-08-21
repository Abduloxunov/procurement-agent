"""SQLite connection and initialisation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Transactional connection -- commits on success, rolls back on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release. SQLite has no ADD COLUMN IF NOT
# EXISTS, so they are applied only when absent -- this keeps existing
# databases (and their purchase history) rather than requiring a reset.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("requests", "budget", "REAL"),
]


def _migrate(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, column, sql_type in MIGRATIONS:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> list[str]:
    """Create every table and apply pending migrations. Safe to re-run."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with session() as conn:
        conn.executescript(schema)
        return _migrate(conn)


def table_counts() -> dict[str, int]:
    with session() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        return {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in tables
        }

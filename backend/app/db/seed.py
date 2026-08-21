"""Seed the source registry.

These are starting weights, not a fixed list. Search never excludes anything
(design doc S04) -- weight only changes ranking order. Sources are promoted
automatically when a purchase is logged against them.
"""

from __future__ import annotations

from app.db.database import session

SEED_SOURCES: list[dict] = [
    # --- search engines: how we discover, not where we buy ---
    {
        "name": "Google",
        "domain": "google.com",
        "kind": "engine",
        "access": "api",
        "region": "global",
        "weight": 1.0,
        "notes": "English-language supplier web.",
    },
    {
        "name": "Baidu",
        "domain": "baidu.com",
        "kind": "engine",
        "access": "api",
        "region": "CN",
        "weight": 1.0,
        "notes": "Chinese industrial web. Surfaces manufacturers Google misses.",
    },
    # --- favourite marketplaces ---
    {
        "name": "Alibaba",
        "domain": "alibaba.com",
        "kind": "marketplace",
        "access": "browser",
        "region": "CN",
        "currency": "USD",
        "weight": 1.5,
        "typical_lead_days": 25,
        "notes": "Favourite. Moderate anti-bot, needs Playwright with stealth.",
    },
    {
        "name": "Made-in-China",
        "domain": "made-in-china.com",
        "kind": "marketplace",
        "access": "fetch",
        "region": "CN",
        "currency": "USD",
        "weight": 1.5,
        "typical_lead_days": 25,
        "notes": "Favourite. Cleanest structured data of the marketplaces.",
    },
    # --- known manufacturer, seeded from the first real part we sourced ---
    {
        "name": "Firstrate Sensor",
        "domain": "firstratesensor.com",
        "kind": "manufacturer",
        "access": "fetch",
        "region": "CN",
        "currency": "USD",
        "weight": 1.4,
        "typical_lead_days": 18,
        "notes": "Hunan Firstrate. Maker of the FST100 family.",
    },
    {
        "name": "Robu",
        "domain": "robu.in",
        "kind": "distributor",
        "access": "browser",
        "region": "IN",
        "currency": "USD",
        "weight": 1.1,
        "typical_lead_days": 10,
        "notes": "Indian distributor, carries FST100. Faster but pricier. "
                 "403s on plain fetch -- bot protection, needs the browser rung.",
    },
]


def seed_sources() -> int:
    """Insert seed sources. Existing rows are left alone, so this is re-runnable."""
    inserted = 0
    with session() as conn:
        for source in SEED_SOURCES:
            existing = conn.execute(
                "SELECT id FROM sources WHERE name = ?", (source["name"],)
            ).fetchone()
            if existing:
                continue
            columns = ", ".join(source.keys())
            placeholders = ", ".join("?" for _ in source)
            conn.execute(
                f"INSERT INTO sources ({columns}) VALUES ({placeholders})",
                tuple(source.values()),
            )
            inserted += 1
    return inserted

"""Search across Google and Baidu, ranked by registry weight.

Two rules from the design doc:
  S04  preference is a weight, not a filter -- nothing is ever excluded
  S13  SerpApi free tier is 250/month, so caching is mandatory
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.db.database import session
from app.sources.models import SearchResult

SERPAPI_URL = "https://serpapi.com/search.json"
ENGINES = ("google", "baidu")


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _cached(engine: str, query: str) -> list[dict] | None:
    with session() as conn:
        row = conn.execute(
            "SELECT results_json FROM search_cache WHERE engine = ? AND query = ?",
            (engine, query),
        ).fetchone()
    return json.loads(row["results_json"]) if row else None


def _cache(engine: str, query: str, results: list[dict]) -> None:
    with session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO search_cache (engine, query, results_json) "
            "VALUES (?, ?, ?)",
            (engine, query, json.dumps(results, ensure_ascii=False)),
        )


def raw_search(query: str, engine: str = "google", limit: int = 10) -> list[dict]:
    """One engine, cached. Returns SerpApi organic results."""
    hit = _cached(engine, query)
    if hit is not None:
        return hit[:limit]

    settings = get_settings()
    if not settings.serpapi_key:
        raise RuntimeError(
            "SERPAPI_KEY is not set. Add it to .env, or pass URLs directly "
            "with `scout --url` to skip search entirely."
        )

    response = httpx.get(
        SERPAPI_URL,
        params={
            "engine": engine,
            "q": query,
            "api_key": settings.serpapi_key,
            "num": limit,
        },
        timeout=45,
    )
    response.raise_for_status()
    results = response.json().get("organic_results", []) or []
    _cache(engine, query, results)
    return results[:limit]


def _source_weights() -> list[tuple[int, str, str, float]]:
    """(id, name, domain, weight) for every registry entry that has a domain."""
    with session() as conn:
        rows = conn.execute(
            "SELECT id, name, domain, weight FROM sources "
            "WHERE domain IS NOT NULL AND domain != '' AND kind != 'engine'"
        ).fetchall()
    return [(r["id"], r["name"], r["domain"].lower(), r["weight"]) for r in rows]


def _match_source(domain: str, registry: list[tuple[int, str, str, float]]):
    for source_id, name, source_domain, weight in registry:
        if domain == source_domain or domain.endswith("." + source_domain):
            return source_id, name, weight
    return None, "", 1.0


def search(query: str, limit_per_engine: int = 8) -> list[SearchResult]:
    """Both engines, deduplicated by URL, ranked by relevance x source weight."""
    registry = _source_weights()
    seen: dict[str, SearchResult] = {}

    for engine in ENGINES:
        try:
            results = raw_search(query, engine=engine, limit=limit_per_engine)
        except Exception as exc:  # noqa: BLE001 - one dead engine must not kill the run
            print(f"  ! {engine} search failed: {exc}")
            continue

        for index, item in enumerate(results, start=1):
            url = item.get("link") or ""
            if not url:
                continue
            domain = domain_of(url)
            source_id, source_name, weight = _match_source(domain, registry)

            # Position decays gently; a weighted favourite at #6 can still
            # outrank an unknown site at #1.
            relevance = 1.0 / (1.0 + 0.15 * (index - 1))
            candidate = SearchResult(
                title=item.get("title") or "",
                url=url,
                snippet=item.get("snippet") or "",
                engine=engine,
                domain=domain,
                position=index,
                source_id=source_id,
                source_name=source_name,
                source_weight=weight,
                score=round(relevance * weight, 4),
            )

            existing = seen.get(url)
            if existing is None or candidate.score > existing.score:
                seen[url] = candidate

    return sorted(seen.values(), key=lambda r: r.score, reverse=True)


def cache_stats() -> dict[str, int]:
    with session() as conn:
        rows = conn.execute(
            "SELECT engine, COUNT(*) AS n FROM search_cache GROUP BY engine"
        ).fetchall()
    return {row["engine"]: row["n"] for row in rows}

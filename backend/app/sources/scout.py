"""The Scout: query -> ranked candidates -> extraction ladder -> saved offers.

The load-bearing agent. Everything downstream refines what this produces.
"""

from __future__ import annotations

import json
from typing import Any

from app.db.database import session
from app.sources import browser, extract, fetch, search
from app.sources.models import ExtractedOffer, PageContent, SearchResult


# Commercial metadata that marketplaces present alongside real specifications.
# None of it says anything about whether one part can replace another.
COMMERCIAL_FIELDS = {
    "trademark", "brand", "transport_package", "packaging", "package",
    "production_capacity", "capacity", "supply_ability", "port",
    "payment_terms", "payment", "delivery_time", "min_order", "moq",
    "price", "unit_price", "origin", "place_of_origin", "warranty_period",
    "after_sales_service", "customization", "sample", "lead_time",
    "specification", "model_number", "product_name", "keyword",
}

COMMERCIAL_SUBSTRINGS = ("packag", "payment", "shipping", "delivery", "price")


def is_commercial_field(key: str) -> bool:
    key = key.strip().lower()
    if key in COMMERCIAL_FIELDS:
        return True
    return any(fragment in key for fragment in COMMERCIAL_SUBSTRINGS)


# ------------------------------------------------------------ persistence ---

def get_or_create_part(mpn: str, manufacturer: str = "") -> int:
    with session() as conn:
        row = conn.execute("SELECT id FROM parts WHERE mpn = ?", (mpn,)).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO parts (mpn, manufacturer, lifecycle) VALUES (?, ?, 'unknown')",
            (mpn, manufacturer or None),
        )
        return cursor.lastrowid


def create_request(mpn: str, quantity: int, raw: str) -> int:
    part_id = get_or_create_part(mpn)
    with session() as conn:
        cursor = conn.execute(
            "INSERT INTO requests (title, part_id, raw_request, quantity, status) "
            "VALUES (?, ?, ?, ?, 'searching')",
            (f"{mpn} x{quantity}", part_id, raw, quantity),
        )
        return cursor.lastrowid


def get_or_create_supplier(name: str, source_id: int | None, url: str) -> int:
    name = (name or "Unknown supplier").strip()[:120]
    with session() as conn:
        row = conn.execute(
            "SELECT id FROM suppliers WHERE name = ? AND source_id IS ?",
            (name, source_id),
        ).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO suppliers (name, source_id, profile_url) VALUES (?, ?, ?)",
            (name, source_id, url),
        )
        return cursor.lastrowid


def save_offer(
    request_id: int,
    part_id: int,
    candidate: SearchResult,
    page: PageContent,
    offer: ExtractedOffer,
) -> int:
    supplier_id = get_or_create_supplier(
        offer.supplier_name, candidate.source_id, candidate.url
    )

    # Sentinels back to NULL at the storage boundary.
    price = offer.unit_price if offer.unit_price >= 0 else None
    # Currency is meaningless without a price, so they are NULL together.
    currency = (offer.currency.upper() or None) if price is not None else None
    # A real supplier with no published price is the RFQ fallback's input --
    # it has not been asked yet, so it is needs_rfq, not rfq_sent.
    state = "listed" if price is not None else "needs_rfq"

    with session() as conn:
        cursor = conn.execute(
            """
            INSERT INTO offers (
                request_id, supplier_id, part_id,
                quantity, unit_price, currency,
                unit_price_original, currency_original,
                incoterm, moq, lead_days, warranty_months,
                url, page_title, source_kind, rung, confidence, state
            ) VALUES (?,?,?, ?,?,?, ?,?, ?,?,?,?, ?,?,?,?,?,?)
            """,
            (
                request_id, supplier_id, part_id,
                offer.moq if offer.moq > 0 else None,
                price, currency,
                price, currency,          # original kept verbatim; conversion is P2
                offer.incoterm or None,
                offer.moq if offer.moq > 0 else None,
                offer.lead_days if offer.lead_days > 0 else None,
                None,
                candidate.url, page.title or None,
                "listing", page.rung, offer.confidence, state,
            ),
        )
        offer_id = cursor.lastrowid

        # Weight is asked in the RFQ, but take it free if a page states it.
        if offer.weight_kg > 0:
            conn.execute(
                "UPDATE parts SET weight_kg = ?, weight_source = 'listing' "
                "WHERE id = ? AND weight_kg IS NULL",
                (offer.weight_kg, part_id),
            )

        for spec in offer.specs:
            key = spec.key.strip().lower()[:60]

            # HS code is a customs attribute of the part, not a technical
            # spec. It drives the duty rate, so it belongs on parts.
            if key in {"hs_code", "hs code", "hscode"}:
                conn.execute(
                    "UPDATE parts SET hs_code = ?, hs_code_source = 'proposed' "
                    "WHERE id = ? AND hs_code IS NULL",
                    (spec.value.strip()[:20], part_id),
                )
                continue

            # Marketplace listings mix commercial metadata in with real
            # specifications. Storing it pollutes equivalence checking, which
            # then solemnly compares trademarks and packaging.
            if is_commercial_field(key):
                continue

            conn.execute(
                "INSERT INTO part_specs (part_id, spec_key, spec_value, source) "
                "VALUES (?, ?, ?, 'listing')",
                (part_id, key, spec.value[:200]),
            )

        if candidate.source_id:
            conn.execute(
                "UPDATE sources SET times_used = times_used + 1, "
                "last_used_at = datetime('now') WHERE id = ?",
                (candidate.source_id,),
            )

    return offer_id


# ----------------------------------------------------------------- ladder ---

def read_page(candidate: SearchResult) -> PageContent:
    """Try each rung in order. Failure falls through, it does not raise.

    fetch -> browser -> manual paste. The last rung is a person, so it is
    not attempted here; the page is returned failed with a note.
    """
    if browser.is_captcha_walled(candidate.domain):
        return PageContent(
            url=candidate.url, ok=False, rung="manual",
            error="CAPTCHA-walled -- paste the page, or send an RFQ",
        )

    if browser.needs_browser(candidate.domain):
        return browser.fetch(candidate.url)

    page = fetch.fetch(candidate.url)
    if page.ok:
        return page

    if browser.IMPLEMENTED:
        upgraded = browser.fetch(candidate.url)
        if upgraded.ok:
            return upgraded
        return upgraded
    return page


# --------------------------------------------------------------------- run ---

def run(
    mpn: str,
    quantity: int = 1,
    *,
    urls: list[str] | None = None,
    max_pages: int = 6,
    raw_request: str = "",
    request_id: int | None = None,
) -> dict[str, Any]:
    """Search (or take given URLs), read, extract, save.

    Passing `urls` skips search entirely, so the extraction half is testable
    without a SerpApi key. Passing `request_id` adds offers to an existing
    request rather than creating another one.
    """
    if request_id is None:
        request_id = create_request(mpn, quantity, raw_request or mpn)
    part_id = get_or_create_part(mpn)

    if urls:
        candidates = [
            SearchResult(
                title="", url=url, engine="manual",
                domain=search.domain_of(url), position=index,
            )
            for index, url in enumerate(urls, start=1)
        ]
        print(f"using {len(candidates)} given url(s), skipping search")
    else:
        print(f"searching google + baidu for: {mpn}")
        candidates = search.search(mpn)
        print(f"  {len(candidates)} candidates after ranking")
        for candidate in candidates[:max_pages]:
            label = candidate.source_name or candidate.domain
            print(f"  {candidate.score:.3f}  [{candidate.engine}] {label}")

    candidates = candidates[:max_pages]
    saved, skipped, failed = [], [], []

    for candidate in candidates:
        print(f"\nreading {candidate.url[:90]}")
        page = read_page(candidate)
        if not page.ok:
            print(f"  ! {page.rung}: {page.error}")
            failed.append({"url": candidate.url, "rung": page.rung, "error": page.error})
            continue

        print(f"  {page.rung} ok, {len(page.text)} chars -- extracting")
        offer = extract.extract_offer(page, mpn, quantity)
        if offer is None:
            print("  - not an offer")
            skipped.append(candidate.url)
            continue

        offer_id = save_offer(request_id, part_id, candidate, page, offer)
        price = (
            f"{offer.unit_price} {offer.currency}"
            if offer.unit_price >= 0 else "no price listed"
        )
        print(
            f"  + offer #{offer_id}: {offer.supplier_name or '?'} "
            f"-- {price}, moq {offer.moq}, lead {offer.lead_days}d "
            f"(conf {offer.confidence:.2f})"
        )
        saved.append(offer_id)

    with session() as conn:
        conn.execute(
            "UPDATE requests SET status = 'extracting', updated_at = datetime('now') "
            "WHERE id = ?",
            (request_id,),
        )

    return {
        "request_id": request_id,
        "part_id": part_id,
        "candidates": len(candidates),
        "offers_saved": len(saved),
        "not_offers": len(skipped),
        "failed": failed,
    }


def ingest_pasted(
    mpn: str,
    quantity: int,
    text: str,
    *,
    url: str = "",
    request_id: int | None = None,
) -> dict[str, Any]:
    """Rung 3: he pasted the page himself.

    The path for CAPTCHA-walled marketplaces. Extraction is identical -- only
    how the text arrived differs, and the offer records that in `rung`.
    """
    if request_id is None:
        request_id = create_request(mpn, quantity, f"pasted: {mpn}")
    part_id = get_or_create_part(mpn)

    page = PageContent(
        url=url or "pasted", title="", text=text[:12_000], rung="manual", ok=True
    )
    candidate = SearchResult(
        title="", url=url or "pasted", engine="manual",
        domain=search.domain_of(url) if url else "pasted", position=1,
    )

    offer = extract.extract_offer(page, mpn, quantity)
    if offer is None:
        return {"request_id": request_id, "offer_id": None,
                "error": "no offer found in the pasted text"}

    offer_id = save_offer(request_id, part_id, candidate, page, offer)
    return {
        "request_id": request_id,
        "offer_id": offer_id,
        "supplier": offer.supplier_name,
        "unit_price": offer.unit_price,
        "currency": offer.currency,
        "moq": offer.moq,
        "lead_days": offer.lead_days,
        "confidence": offer.confidence,
    }


def offers_for(request_id: int) -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT o.id, s.name AS supplier, o.unit_price, o.currency,
                   o.moq, o.lead_days, o.incoterm, o.rung,
                   o.confidence, o.state, o.url
            FROM offers o
            LEFT JOIN suppliers s ON s.id = o.supplier_id
            WHERE o.request_id = ?
            ORDER BY (o.unit_price IS NULL), o.unit_price
            """,
            (request_id,),
        ).fetchall()
    return [dict(row) for row in rows]

"""Turn a fetched page into a structured offer.

Works on Chinese pages directly -- the model reads them natively, so prices
come out in the listing's own currency and conversion happens later, with
the original preserved so a bad conversion stays traceable (design doc S05).
"""

from __future__ import annotations

from app import llm
from app.sources.models import ExtractedOffer, PageContent

SYSTEM = """You extract supplier offers from product pages for an industrial \
parts buyer. Pages may be in any language, commonly English or Chinese.

Rules:
- Report prices in the currency the page states. Never convert.
- Report only what the page says. Do not infer a price from a similar variant.
- If the page is not selling this part -- a blog post, a forum thread, a \
category listing, a datasheet with no price -- set is_offer to false.
- A page can be a real offer with no price ("contact supplier"). Then \
is_offer is true and unit_price is -1.
- Use -1 for unknown numbers and "" for unknown text. Never guess.
- Set confidence honestly. A clearly stated price at a stated quantity is \
high. An inferred or ambiguous one is low."""

PROMPT = """Part being sourced: {mpn}
Quantity wanted: {quantity}

Page URL: {url}
Page title: {title}

--- page content ---
{text}
--- end ---

Extract the offer."""


def extract_offer(
    page: PageContent,
    mpn: str,
    quantity: int,
) -> ExtractedOffer | None:
    """Returns None if the page could not be read or is not an offer."""
    if not page.ok or not page.text:
        return None

    try:
        offer = llm.structured(
            PROMPT.format(
                mpn=mpn,
                quantity=quantity,
                url=page.url,
                title=page.title or "(none)",
                text=page.text,
            ),
            ExtractedOffer,
            system=SYSTEM,
        )
    except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
        print(f"  ! extraction failed for {page.url}: {str(exc)[:120]}")
        return None

    return offer if offer.is_offer else None

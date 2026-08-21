"""Shapes that flow through the Scout pipeline.

Note the sentinel convention: extraction fields use -1 / "" for "not stated
on the page" rather than Optional. Strict JSON-schema support varies across
providers, and required-with-sentinel is portable where nullable is not.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

UNKNOWN_NUM = -1.0
UNKNOWN_INT = -1
UNKNOWN_STR = ""


class SearchResult(BaseModel):
    """One ranked candidate page."""

    title: str
    url: str
    snippet: str = ""
    engine: str                      # 'google' | 'baidu'
    domain: str
    position: int                    # 1-based rank within its engine
    source_id: int | None = None     # matched row in the sources table
    source_name: str = ""
    source_weight: float = 1.0
    score: float = 0.0               # relevance * source_weight


class PageContent(BaseModel):
    """Result of trying to read a candidate page."""

    url: str
    title: str = ""
    text: str = ""
    rung: str = "fetch"              # 'fetch' | 'browser' | 'manual'
    ok: bool = True
    error: str = ""


class SpecItem(BaseModel):
    key: str = Field(description="Specification name, e.g. output_signal")
    value: str = Field(description="Value with unit, e.g. RS485 or 12-24 VDC")


class ExtractedOffer(BaseModel):
    """What the model pulls off a supplier page.

    Every field is required so strict JSON schema works everywhere; absence
    is expressed with the sentinels above, never by omitting the key.
    """

    is_offer: bool = Field(
        description="True only if this page actually sells the part. "
        "False for blog posts, forum threads, catalogues without prices."
    )
    supplier_name: str = Field(description="Selling company. '' if unclear.")
    part_number: str = Field(description="Part number on the page. '' if absent.")

    unit_price: float = Field(description="Price per unit. -1 if not stated.")
    currency: str = Field(description="ISO code as listed, e.g. USD or CNY. '' if unclear.")
    moq: int = Field(description="Minimum order quantity. -1 if not stated.")
    lead_days: int = Field(description="Lead time in days. -1 if not stated.")
    incoterm: str = Field(description="FOB, EXW, DDP, CIF. '' if not stated.")
    weight_kg: float = Field(description="Shipping weight per unit. -1 if not stated.")

    specs: list[SpecItem] = Field(description="Technical specs stated on the page.")
    confidence: float = Field(description="0 to 1. Be honest; guesses score low.")
    notes: str = Field(description="Anything a buyer should know. '' if nothing.")

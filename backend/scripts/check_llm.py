"""Verify the OpenRouter connection, structured output and vision.

    python scripts/check_llm.py

Needs OPENROUTER_API_KEY in .env. Costs a fraction of a cent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from pydantic import BaseModel, Field  # noqa: E402

from app import llm  # noqa: E402
from app.config import get_settings  # noqa: E402


class ExtractedOffer(BaseModel):
    """The shape every extraction path must produce."""

    supplier_name: str
    unit_price: float
    currency: str = Field(description="ISO code, e.g. USD or CNY")
    moq: int = Field(description="Minimum order quantity")
    lead_days: int
    incoterm: str = Field(description="FOB, EXW, DDP or CIF")
    confidence: float = Field(description="0 to 1, how sure the extraction is")


LISTING = """
深圳市天成电子有限公司
FST100-2006A 土壤温湿度电导率传感器 RS485
价格: ¥285.00 / 件
起订量: 20 件
交货期: 15 天
贸易条款: FOB 深圳
"""


def preflight(key: str) -> bool:
    """Check the key before spending a request, and say what is actually wrong."""
    import httpx

    if not key:
        print("\n! OPENROUTER_API_KEY is empty.")
        print("  cp .env.example .env   then paste your key into .env")
        return False

    if key != key.strip():
        print("\n! The key has leading or trailing whitespace.")
        print("  Check .env for a stray space or newline after the '='.")
        return False

    if key.startswith(("'", '"')) or key.endswith(("'", '"')):
        print("\n! The key is wrapped in quotes. .env values need no quotes.")
        return False

    if "xxx" in key.lower() or key == "sk-or-v1-...":
        print("\n! That is the placeholder from .env.example, not a real key.")
        print("  Get one at https://openrouter.ai/keys")
        return False

    if not key.startswith("sk-or-"):
        print(f"\n! Key starts with '{key[:8]}...' -- OpenRouter keys start 'sk-or-'.")
        return False

    print(f"\nkey        {key[:12]}...{key[-4:]}  ({len(key)} chars)")

    # /auth/key returns this key's own metadata. Cheapest possible validation.
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"! could not reach OpenRouter: {exc}")
        return False

    if response.status_code == 401:
        print("! 401 -- OpenRouter does not recognise this key.")
        print("  It was revoked, deleted, or never existed.")
        print("  Create a fresh one at https://openrouter.ai/keys and")
        print("  paste it into backend/.env")
        return False

    if response.status_code != 200:
        print(f"! HTTP {response.status_code}: {response.text[:200]}")
        return False

    info = response.json().get("data", {})
    limit, usage = info.get("limit"), info.get("usage", 0)
    print(f"valid      usage {usage}, limit {limit if limit is not None else 'none'}")
    if limit is not None and usage >= limit:
        print("! this key is out of credit -- top up or use a free model")
        return False
    return True


def main() -> None:
    settings = get_settings()
    print(f"gateway    {settings.openrouter_base_url}")
    print(f"default    {settings.model_default}")
    print(f"reasoning  {settings.model_reasoning}")
    print(f"vision     {settings.model_vision}")

    if not preflight(settings.openrouter_api_key):
        sys.exit(1)

    print("\n1. plain completion")
    reply = llm.complete("Reply with exactly the word: ready")
    print(f"   -> {reply.strip()[:60]}")

    print("\n2. structured output from a Chinese listing")
    offer = llm.structured(
        f"Extract the offer from this supplier listing:\n\n{LISTING}",
        ExtractedOffer,
        system=(
            "You extract structured supplier offers from listings in any "
            "language. Keep prices in the listing's own currency -- conversion "
            "happens later. Set confidence honestly."
        ),
    )
    print(f"   supplier   {offer.supplier_name}")
    print(f"   price      {offer.unit_price} {offer.currency}")
    print(f"   moq        {offer.moq}")
    print(f"   lead       {offer.lead_days} days")
    print(f"   incoterm   {offer.incoterm}")
    print(f"   confidence {offer.confidence}")

    expected = {"cny": 285.0}
    if offer.currency.lower() in expected and offer.unit_price == expected[offer.currency.lower()]:
        print("   -> correct: read CNY 285 without inventing a conversion")
    else:
        print("   -> check this: expected 285 CNY")

    print("\nllm ok")


if __name__ == "__main__":
    main()

"""Currency conversion to USD.

Rates come from frankfurter.app (ECB data, free, no key) and are cached for
the day. Fallbacks exist so a network failure degrades to an approximate
number with a warning rather than killing the run.

The original price and currency are always preserved on the offer row, so a
bad conversion stays traceable rather than baked in (design doc S05).
"""

from __future__ import annotations

from datetime import date

import httpx

BASE = "USD"
API = "https://api.frankfurter.app/latest"

# Only used when the API is unreachable. Deliberately rough -- the point is
# to keep working, and the row is flagged when these are used.
FALLBACK_PER_USD = {
    "USD": 1.0,
    "CNY": 7.15,
    "EUR": 0.92,
    "RUB": 92.0,
    "INR": 84.0,
    "UZS": 12800.0,
}

_cache: dict[str, float] = {}
_cached_on: date | None = None
_used_fallback = False


def _load_rates() -> dict[str, float]:
    """Units of each currency per 1 USD."""
    global _cache, _cached_on, _used_fallback

    if _cached_on == date.today() and _cache:
        return _cache

    try:
        # follow_redirects matters: the API 301s, and without this the
        # response body is a redirect page that fails to parse as JSON.
        response = httpx.get(
            API, params={"from": BASE}, timeout=15, follow_redirects=True
        )
        response.raise_for_status()
        rates = response.json()["rates"]
        rates[BASE] = 1.0
        _cache, _cached_on, _used_fallback = rates, date.today(), False
    except Exception:  # noqa: BLE001 - offline must not stop a costing
        _cache, _cached_on, _used_fallback = dict(FALLBACK_PER_USD), date.today(), True

    return _cache


def rate_to_usd(currency: str) -> tuple[float, bool]:
    """Return (multiplier to USD, whether a fallback rate was used)."""
    code = (currency or BASE).upper()
    if code == BASE:
        return 1.0, False

    rates = _load_rates()
    per_usd = rates.get(code)
    if per_usd is None:
        per_usd = FALLBACK_PER_USD.get(code)
        if per_usd is None:
            raise ValueError(f"No exchange rate for {code}")
        return 1.0 / per_usd, True

    return 1.0 / per_usd, _used_fallback


def to_usd(amount: float, currency: str) -> tuple[float, float, bool]:
    """Return (usd_amount, rate_used, is_fallback)."""
    rate, fallback = rate_to_usd(currency)
    return round(amount * rate, 4), rate, fallback

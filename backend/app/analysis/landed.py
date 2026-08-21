"""Landed cost: what a part actually costs delivered to Tashkent.

    CIF      = goods + freight + insurance
    duty     = CIF * duty_rate(hs_code)
    vat      = (CIF + duty) * vat_rate
    total    = CIF + duty + vat + fees
    per_unit = total / quantity

The interesting part is not the arithmetic, it is the incoterm. An EXW price
and a DDP price are not comparable numbers -- one excludes freight, duty and
VAT entirely. Ranking on unit price without this is the mistake the whole
product exists to prevent.

Every result carries its assumptions so a wrong one is visible on the row
rather than buried in a total.
"""

from __future__ import annotations

from app.analysis import currency, freight
from app.config import get_settings

# What the quoted price already includes.
#   add_freight  we must pay main carriage on top
#   add_import   we must pay duty and VAT on top
#   origin_fee   we must pay origin handling on top (ex-works only)
INCOTERMS = {
    "EXW": {"add_freight": True,  "add_import": True,  "origin_fee": True},
    "FCA": {"add_freight": True,  "add_import": True,  "origin_fee": False},
    "FOB": {"add_freight": True,  "add_import": True,  "origin_fee": False},
    "CFR": {"add_freight": False, "add_import": True,  "origin_fee": False},
    "CIF": {"add_freight": False, "add_import": True,  "origin_fee": False},
    "CIP": {"add_freight": False, "add_import": True,  "origin_fee": False},
    "DAP": {"add_freight": False, "add_import": True,  "origin_fee": False},
    "DDP": {"add_freight": False, "add_import": False, "origin_fee": False},
}

# When a listing does not state one. EXW is the most expensive reading, so
# assuming it avoids flattering a quote -- but the row is flagged either way.
ASSUMED_INCOTERM = "EXW"

ORIGIN_HANDLING_USD = 40.0   # export docs, inland to port; flat estimate
BANK_FEE_RATE = 0.01         # T/T transfer and FX spread

# Duty rate by HS prefix.
#
# Deliberately near-empty. Uzbek tariff lines run 5-70% and inventing a
# plausible-looking rate here is exactly the failure mode this project is
# built to prevent. Add entries ONLY from the official tariff, and until
# then every costing reports duty as 'unverified' and asks for confirmation.
DUTY_BY_HS_PREFIX: dict[str, float] = {}


def duty_rate_for(hs_code: str | None) -> tuple[float, str]:
    """Return (rate, provenance). Provenance is shown on every row."""
    settings = get_settings()
    if hs_code:
        digits = "".join(c for c in hs_code if c.isdigit())
        for length in (6, 4, 2):
            rate = DUTY_BY_HS_PREFIX.get(digits[:length])
            if rate is not None:
                return rate, f"tariff line {digits[:length]}"
    return settings.default_duty_rate, "default, unverified"


def compute(
    *,
    unit_price: float,
    currency_code: str,
    quantity: int,
    incoterm: str | None = None,
    hs_code: str | None = None,
    weight_kg_per_unit: float | None = None,
    freight_mode: str | None = None,
    freight_override: float | None = None,
    need_days: int | None = None,
) -> dict:
    """Landed cost for one offer at one quantity."""
    settings = get_settings()

    # --- goods, converted to USD -------------------------------------------
    unit_usd, fx_rate, fx_fallback = currency.to_usd(unit_price, currency_code)
    goods = round(unit_usd * quantity, 2)

    # --- what the incoterm already covers ----------------------------------
    term = (incoterm or "").strip().upper()
    assumed_term = term not in INCOTERMS
    if assumed_term:
        term = ASSUMED_INCOTERM
    rules = INCOTERMS[term]

    # --- freight ------------------------------------------------------------
    if not rules["add_freight"]:
        freight_cost = 0.0
        freight_info = {
            "cost": 0.0,
            "mode": "included",
            "mode_label": f"included in {term} price",
            "transit_days": None,
            "weight_source": "n/a",
            "source": "incoterm",
        }
    elif freight_override is not None:
        freight_cost = round(freight_override, 2)
        freight_info = {
            "cost": freight_cost, "mode": freight_mode or "quoted",
            "mode_label": "quoted", "transit_days": None,
            "weight_source": "n/a", "source": "quoted",
        }
    else:
        freight_info = freight.estimate(
            quantity, weight_kg_per_unit, freight_mode, need_days
        )
        freight_cost = freight_info["cost"]

    origin_fee = ORIGIN_HANDLING_USD if rules["origin_fee"] else 0.0

    # --- CIF ----------------------------------------------------------------
    insurance = 0.0
    cif = round(goods + freight_cost + origin_fee + insurance, 2)

    # --- import charges -----------------------------------------------------
    if rules["add_import"]:
        duty_rate, duty_provenance = duty_rate_for(hs_code)
        duty = round(cif * duty_rate, 2)
        vat_rate = settings.vat_rate
        vat = round((cif + duty) * vat_rate, 2)
    else:
        duty_rate, duty_provenance = 0.0, f"included in {term} price"
        duty = 0.0
        vat_rate, vat = 0.0, 0.0

    fees = round(goods * BANK_FEE_RATE, 2)
    total = round(cif + duty + vat + fees, 2)
    per_unit = round(total / quantity, 2) if quantity else 0.0

    # --- everything we assumed, printed on the row --------------------------
    flags: list[str] = []
    if assumed_term:
        flags.append(f"incoterm not stated, assumed {term}")
    if duty_provenance == "default, unverified":
        flags.append(f"duty {duty_rate:.0%} unverified -- confirm HS code")
    if freight_info.get("weight_source") == "estimated":
        flags.append(
            f"weight estimated at {freight_info['unit_weight_kg']} kg/unit"
        )
    if fx_fallback and currency_code.upper() != "USD":
        flags.append("offline FX fallback rate")

    return {
        "quantity": quantity,
        "unit_price_original": unit_price,
        "currency_original": currency_code.upper(),
        "fx_rate": round(fx_rate, 6),
        "unit_price_usd": round(unit_usd, 4),
        "goods": goods,
        "freight": freight_cost,
        "freight_mode": freight_info["mode"],
        "origin_fee": origin_fee,
        "insurance": insurance,
        "cif": cif,
        "incoterm": term,
        "incoterm_assumed": assumed_term,
        "hs_code": hs_code,
        "duty_rate": duty_rate,
        "duty": duty,
        "vat_rate": vat_rate,
        "vat": vat,
        "fees": fees,
        "total": total,
        "per_unit": per_unit,
        "uplift_pct": round((per_unit / unit_usd - 1) * 100, 1) if unit_usd else 0.0,
        "transit_days": freight_info.get("transit_days"),
        "assumptions": {
            "duty": duty_provenance,
            "freight": freight_info.get("mode_label"),
            "freight_source": freight_info.get("source"),
            "weight": freight_info.get("weight_source"),
        },
        "flags": flags,
    }

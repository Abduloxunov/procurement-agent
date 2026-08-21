"""Freight estimation, China to Tashkent.

There is no regular forwarder to model, so this is a generic per-kg estimate
with editable defaults (design doc S07). One fact shapes the whole table:
Uzbekistan is landlocked, so there is no sea option.

These rates are placeholders. The first real invoice logged through the
purchase log should replace them -- freight is the softest number in the
comparison and the table says so.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fallback when a part's shipping weight is unknown. Small industrial
# sensors cluster around here; the row is flagged when this is used.
DEFAULT_WEIGHT_KG = 0.35

# Below this, per-kg pricing stops applying and a minimum charge kicks in.
MIN_CHARGE = {
    "air_express": 35.0,
    "air": 60.0,
    "road": 90.0,
    "rail": 120.0,
}


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    usd_per_kg: float
    transit_days: int


MODES: dict[str, Mode] = {
    "air_express": Mode("air_express", "Air express (DHL/FedEx)", 10.0, 7),
    "air": Mode("air", "Air freight", 5.0, 12),
    "road": Mode("road", "Road via Kazakhstan", 2.5, 20),
    "rail": Mode("rail", "Rail, China-Central Asia", 2.0, 25),
}

DEFAULT_MODE = "air"


def choose_mode(total_weight_kg: float, need_days: int | None = None) -> str:
    """Pick a sensible default mode. Overridable at every call site."""
    if need_days is not None:
        affordable = [m for m in MODES.values() if m.transit_days <= need_days]
        if affordable:
            return min(affordable, key=lambda m: m.usd_per_kg).key
        return "air_express"  # nothing fits; fastest is the only honest answer

    if total_weight_kg <= 2:
        return "air_express"
    if total_weight_kg <= 50:
        return "air"
    return "road"


def estimate(
    quantity: int,
    weight_kg_per_unit: float | None = None,
    mode: str | None = None,
    need_days: int | None = None,
) -> dict:
    """Estimate freight for a shipment. Always returns its assumptions."""
    weight_known = weight_kg_per_unit is not None and weight_kg_per_unit > 0
    unit_weight = weight_kg_per_unit if weight_known else DEFAULT_WEIGHT_KG
    total_weight = round(unit_weight * quantity, 3)

    mode_key = mode or choose_mode(total_weight, need_days)
    chosen = MODES.get(mode_key, MODES[DEFAULT_MODE])

    cost = max(total_weight * chosen.usd_per_kg, MIN_CHARGE.get(chosen.key, 0.0))

    return {
        "cost": round(cost, 2),
        "mode": chosen.key,
        "mode_label": chosen.label,
        "usd_per_kg": chosen.usd_per_kg,
        "transit_days": chosen.transit_days,
        "total_weight_kg": total_weight,
        "unit_weight_kg": unit_weight,
        "weight_source": "known" if weight_known else "estimated",
        "source": "estimate",
    }

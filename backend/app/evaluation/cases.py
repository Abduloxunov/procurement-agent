"""The golden set.

Most projects cannot evaluate properly because they have no ground truth.
We do: the landed-cost maths is arithmetic we can verify by hand, the
datasheet answers are in the datasheet, and the purchase history is what we
logged. So these cases measure correctness, not plausibility.

Three kinds, deliberately separated because they fail differently:

  deterministic  no model involved. Must be exactly right, every run.
  routing        does the supervisor pick the right specialist?
  answer         does the whole graph produce a correct, grounded answer?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------- costing ---

@dataclass
class CostCase:
    """A landed-cost calculation with a hand-checkable expectation."""

    name: str
    args: dict[str, Any]
    expect_per_unit: float | None = None   # exact, to 2dp
    invariants: list[str] = field(default_factory=list)
    tolerance: float = 0.01


COST_CASES: list[CostCase] = [
    CostCase(
        name="DDP adds nothing but the bank fee",
        args=dict(unit_price=71.0, currency_code="USD", quantity=50,
                  incoterm="DDP"),
        # DDP already includes freight, duty and VAT. Only the T/T fee applies:
        # 71.00 * 1.01 = 71.71
        expect_per_unit=71.71,
    ),
    CostCase(
        name="FOB at qty 20 adds freight, duty and VAT",
        args=dict(unit_price=45.0, currency_code="USD", quantity=20,
                  incoterm="FOB"),
        # goods 900; weight unknown -> 0.35*20 = 7kg -> air, min charge 60
        # cif 960; duty 20% = 192; vat 12% of 1152 = 138.24; fee 9.00
        # total 1299.24 / 20 = 64.96
        expect_per_unit=64.96,
    ),
    CostCase(
        name="CNY is converted, not compared raw",
        args=dict(unit_price=260.0, currency_code="CNY", quantity=50,
                  incoterm="EXW"),
        invariants=["under_usd_60_goods"],
    ),
    CostCase(
        name="EXW costs more than DDP at the same nominal price",
        args=dict(unit_price=50.0, currency_code="USD", quantity=50,
                  incoterm="EXW"),
        invariants=["exw_dearer_than_ddp"],
    ),
    CostCase(
        name="unstated incoterm is assumed, not ignored",
        args=dict(unit_price=40.0, currency_code="USD", quantity=50,
                  incoterm=None),
        invariants=["flags_assumed_incoterm", "flags_unverified_duty"],
    ),
    CostCase(
        name="per-unit cost falls as quantity rises",
        args=dict(unit_price=40.0, currency_code="USD", quantity=10,
                  incoterm="FOB"),
        invariants=["amortises_with_quantity"],
    ),
    CostCase(
        name="estimated weight is flagged",
        args=dict(unit_price=40.0, currency_code="USD", quantity=50,
                  incoterm="FOB"),
        invariants=["flags_estimated_weight"],
    ),
]


# ---------------------------------------------------------------- routing ---

@dataclass
class RoutingCase:
    name: str
    question: str
    expect: str                       # the agent the supervisor should pick
    also_acceptable: list[str] = field(default_factory=list)


ROUTING_CASES: list[RoutingCase] = [
    RoutingCase("spec question goes to datasheets",
                "what output signal does the FST100-2006A use?", "part"),
    RoutingCase("IP rating goes to datasheets",
                "is the FST100 sealed to IP68?", "part"),
    RoutingCase("past price goes to history",
                "what did we pay for FST100 last time?", "history"),
    RoutingCase("delivery record goes to history",
                "which suppliers have delivered late?", "history"),
    RoutingCase("who sells it goes to the web",
                "who sells the FST100-2006A and at what price?", "scout"),
    RoutingCase("landed cost goes to the analyst",
                "what is the landed cost per unit in Tashkent at qty 50?",
                "analyst", also_acceptable=["scout"]),
    RoutingCase("comparison goes to the analyst",
                "rank these suppliers by total cost delivered",
                "analyst", also_acceptable=["scout"]),
]


# ----------------------------------------------------------------- answer ---

@dataclass
class AnswerCase:
    """End-to-end. `must_contain` are facts; `must_not_contain` are traps."""

    name: str
    question: str
    request_id: int | None = None
    quantity: int | None = None
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    must_be_grounded: bool = True


ANSWER_CASES: list[AnswerCase] = [
    AnswerCase(
        name="reads the output signal off the datasheet",
        question="what output signal does the FST100 soil sensor use?",
        must_contain=["RS485"],
    ),
    AnswerCase(
        name="reads IP rating from a captioned figure",
        # IP68 appears only in an image caption, never in the extracted text.
        question="what is the IP rating of the FST100?",
        must_contain=["IP68"],
    ),
    AnswerCase(
        name="recalls the real purchase, landed not listed",
        question="what did we pay per unit for FST100 last time, all in?",
        must_contain=["45.29"],
    ),
    AnswerCase(
        name="recalls the actual lead time",
        question="how long did the last FST100 order take to arrive?",
        must_contain=["18"],
    ),
    AnswerCase(
        name="cheapest landed is not the cheapest listed",
        question="which supplier is cheapest once landed, and is that the "
                 "same as the cheapest listed price?",
        request_id=1,
        quantity=50,
        must_contain=["60.15", "38.67"],
        # The trap: $40 looks cheapest only if you fail to convert CNY.
        must_not_contain=["cheapest listed price is $40"],
    ),
    AnswerCase(
        name="admits when it has nothing",
        question="what is the calibration drift of the FST100 over five years?",
        must_contain=[],
        must_not_contain=["drift is"],
    ),
]

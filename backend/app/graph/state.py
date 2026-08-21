"""Shared state.

Every node reads and writes this one object. The reducers matter: `steps`
and `evidence` accumulate across the run so the supervisor can see what has
already been collected -- without that it routes to the same agent forever,
which is the classic multi-agent failure.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

# Who the supervisor can hand off to.
Agent = Literal["part", "scout", "history", "analyst", "finish"]

AGENT_DESCRIPTIONS = {
    "part": "Datasheet retrieval. Technical specifications, output signals, "
            "voltage, IP rating, dimensions, what a part is and does.",
    "scout": "Web research. Who sells a part, at what listed price, MOQ and "
             "lead time. Costs a paid search credit, so only when needed.",
    "history": "Our own purchase records. What we paid before, from whom, "
               "how long delivery actually took, which suppliers we trust.",
    "analyst": "Landed cost and comparison. Converts currency, applies "
               "incoterms, adds freight, duty and VAT, ranks by cost per unit.",
    "finish": "Enough evidence has been gathered. Write the answer.",
}


class Evidence(TypedDict):
    """One piece of support for the final answer.

    `ref` is what the critic checks against -- a URL, a table name, an SQL
    query, a datasheet page. Evidence without a ref cannot ground a claim.
    """

    agent: str
    ref: str
    content: str


class AskState(TypedDict, total=False):
    question: str
    request_id: int | None
    quantity: int | None

    plan: Agent
    steps: Annotated[list[str], operator.add]
    evidence: Annotated[list[Evidence], operator.add]

    answer: str
    verdict: str          # 'approved' | 'revise'
    critique: str
    revisions: int
    hops: int             # supervisor visits, capped so the graph terminates

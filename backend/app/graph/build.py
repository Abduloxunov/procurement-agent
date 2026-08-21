"""Wire the ask graph.

    question -> supervisor -> [part|scout|history|analyst] -> supervisor
                     |
                  finish -> generate -> critic -> END
                                 ^          |
                                 +--- revise+

Every specialist returns to the supervisor -- a star topology. Specialists
never talk to each other, which keeps the trace readable and means a
mis-routing shows up in one place.

Termination is guaranteed three ways: a hop budget in the supervisor, a
revision cap in the critic, and LangGraph's own recursion limit.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.graph import nodes
from app.graph.critic import critic_node, route_after_critic
from app.graph.state import AskState
from app.graph.supervisor import route_after_supervisor, supervisor_node

SPECIALISTS = {
    "part": nodes.part_node,
    "scout": nodes.scout_node,
    "history": nodes.history_node,
    "analyst": nodes.analyst_node,
}

RECURSION_LIMIT = 25

_compiled = None


def build():
    graph = StateGraph(AskState)

    graph.add_node("supervisor", supervisor_node)
    for name, fn in SPECIALISTS.items():
        graph.add_node(name, fn)
    graph.add_node("generate", nodes.generate_node)
    graph.add_node("critic", critic_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {**{name: name for name in SPECIALISTS}, "generate": "generate"},
    )
    for name in SPECIALISTS:
        graph.add_edge(name, "supervisor")

    graph.add_edge("generate", "critic")
    graph.add_conditional_edges(
        "critic", route_after_critic, {"generate": "generate", "done": END}
    )

    # Checkpointing is what will let the workflow graph pause at a human
    # approval gate and resume days later. The ask graph does not need that
    # yet, but sharing the pattern keeps the two graphs alike.
    return graph.compile(checkpointer=MemorySaver())


def app():
    global _compiled
    if _compiled is None:
        _compiled = build()
    return _compiled


def ask(
    question: str,
    *,
    request_id: int | None = None,
    quantity: int | None = None,
    thread: str = "default",
) -> dict[str, Any]:
    initial: AskState = {
        "question": question,
        "request_id": request_id,
        "quantity": quantity,
        "steps": [],
        "evidence": [],
        "revisions": 0,
        "hops": 0,
    }
    return app().invoke(
        initial,
        config={
            "configurable": {"thread_id": thread},
            "recursion_limit": RECURSION_LIMIT,
        },
    )


def mermaid() -> str:
    """The graph as a diagram -- useful for the README and the defence."""
    return app().get_graph().draw_mermaid()

"""FastAPI backend.

/chat streams the agent trace as server-sent events. That is the point of
F13: watching the supervisor delegate, the critic reject and the table fill
is a far better demonstration than tokens appearing in a bubble.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agents import clarify, intake
from app.analysis import compare, equivalence
from app.db.database import session
from app.graph import build
from app.graph.state import AskState
from app.memory import purchase
from app.rfq import draft as rfq_draft
from app.rfq import mailer
from app.sources import scout

# One copy of the frontend, two ways to serve it: FastAPI mounts it for local
# development, Vercel serves the same directory in production.
STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="Sourcing Desk", version="1.0")

# The frontend is on Vercel and the API is on a VM, so they are different
# origins. ALLOWED_ORIGINS should name the Vercel deployment in production --
# "*" is fine while nothing here is authenticated, but it stops being fine
# the moment it is.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- models ---

class ChatIn(BaseModel):
    question: str
    request_id: int | None = None
    quantity: int | None = None


class PurchaseIn(BaseModel):
    sentence: str
    reply: str | None = None


class EquivalenceIn(BaseModel):
    reference: str
    candidate: str


# ----------------------------------------------------------------- basics ---

@app.get("/health")
def health() -> dict[str, Any]:
    with session() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in ("parts", "offers", "purchases", "sources", "rfqs")
        }
    return {"status": "ok", "counts": counts}


@app.get("/requests")
def list_requests() -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.title, r.quantity, r.status, p.mpn,
                   COUNT(o.id) AS offers
            FROM requests r
            LEFT JOIN parts p  ON p.id = r.part_id
            LEFT JOIN offers o ON o.request_id = r.id
            GROUP BY r.id ORDER BY r.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/compare/{request_id}")
def comparison(request_id: int, qty: int | None = None) -> dict[str, Any]:
    try:
        return compare.build(request_id, qty)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/sensitivity/{request_id}")
def sensitivity(request_id: int, quantities: str = "1,10,50,200") -> list[dict]:
    values = [int(q) for q in quantities.split(",") if q.strip()]
    return compare.sensitivity(request_id, values)


# ------------------------------------------------------------------- chat ---

def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


async def _stream(body: ChatIn) -> AsyncIterator[str]:
    initial: AskState = {
        "question": body.question,
        "request_id": body.request_id,
        "quantity": body.quantity,
        "steps": [], "evidence": [], "revisions": 0, "hops": 0,
    }
    config = {
        "configurable": {"thread_id": "web"},
        "recursion_limit": build.RECURSION_LIMIT,
    }

    final: dict[str, Any] = {}
    try:
        for chunk in build.app().stream(initial, config=config):
            for node, update in chunk.items():
                final.update(update or {})
                for step in (update or {}).get("steps", []):
                    yield _sse("step", {"node": node, "text": step})
                if (update or {}).get("evidence"):
                    yield _sse("evidence", {
                        "count": len(update["evidence"]),
                        "items": [
                            {"agent": e["agent"], "ref": e["ref"]}
                            for e in update["evidence"]
                        ],
                    })
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", {"message": str(exc)[:300]})
        return

    yield _sse("answer", {
        "answer": final.get("answer", ""),
        "verdict": final.get("verdict"),
        "hops": final.get("hops", 0),
        "revisions": final.get("revisions", 0),
    })
    yield _sse("done", {})


@app.post("/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    return StreamingResponse(
        _stream(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/clarify")
def clarify_request(body: ChatIn) -> dict[str, Any]:
    return clarify.check(body.question).model_dump()


# ------------------------------------------------------------------ intake ---

class IntakeIn(BaseModel):
    text: str
    answer: str | None = None


class SearchIn(BaseModel):
    urls: list[str] | None = None
    max_pages: int = 5


@app.post("/request/preview")
def preview_request(body: IntakeIn) -> dict[str, Any]:
    """What we understood and what is still missing. Creates nothing."""
    return intake.preview(body.text)


@app.post("/request/new")
def new_request(body: IntakeIn) -> dict[str, Any]:
    """Create a request, or return the questions still blocking it."""
    return intake.create(body.text, body.answer)


@app.post("/request/{request_id}/search")
def run_search(request_id: int, body: SearchIn) -> dict[str, Any]:
    """Run the Scout for an existing request. Costs SerpApi credits."""
    with session() as conn:
        row = conn.execute(
            "SELECT r.quantity, p.mpn FROM requests r "
            "LEFT JOIN parts p ON p.id = r.part_id WHERE r.id = ?",
            (request_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"no request #{request_id}")
    if not row["mpn"]:
        raise HTTPException(
            400,
            "this request has no part number, so there is nothing to search "
            "for. Add one, or paste supplier pages instead.",
        )
    return scout.run(
        row["mpn"], row["quantity"] or 1,
        urls=body.urls, max_pages=body.max_pages,
        request_id=request_id,
    )


@app.get("/request/{request_id}/budget")
def check_budget(request_id: int, qty: int | None = None) -> dict[str, Any]:
    return intake.budget_check(request_id, qty)


# -------------------------------------------------------------- purchases ---

@app.post("/purchase/parse")
def parse_purchase(body: PurchaseIn) -> dict[str, Any]:
    draft = purchase.parse(body.sentence)
    return {
        "draft": draft.model_dump(),
        "missing": purchase.missing(draft),
        "questions": purchase.question_block(draft),
    }


@app.post("/purchase/save")
def save_purchase(body: PurchaseIn) -> dict[str, Any]:
    draft = purchase.parse(body.sentence)
    if body.reply:
        draft = purchase.merge(draft, body.reply)
    try:
        return {"saved": True, **purchase.save(draft), "record": draft.model_dump()}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------- equivalence ---

@app.post("/equivalence")
def check_equivalence(body: EquivalenceIn) -> dict[str, Any]:
    result = equivalence.compare(body.reference, body.candidate)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# --------------------------------------------------------------------- rfq ---

@app.post("/rfq/draft/{request_id}")
def draft_rfqs(request_id: int) -> list[dict[str, Any]]:
    return rfq_draft.draft_for_request(request_id)


@app.get("/rfq/pending")
def pending_rfqs(request_id: int | None = None) -> list[dict[str, Any]]:
    return rfq_draft.pending(request_id)


@app.post("/rfq/{rfq_id}/approve")
def approve_rfq(rfq_id: int) -> dict[str, Any]:
    rfq_draft.approve(rfq_id)
    return {"rfq_id": rfq_id, "status": "approved"}


@app.post("/rfq/{rfq_id}/drop")
def drop_rfq(rfq_id: int) -> dict[str, Any]:
    rfq_draft.drop(rfq_id)
    return {"rfq_id": rfq_id, "status": "dropped"}


@app.post("/rfq/{rfq_id}/send")
def send_rfq(rfq_id: int, to: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """dry_run defaults to True. Sending is opt-in, never the default."""
    try:
        return mailer.send(rfq_id, to, dry_run=dry_run)
    except mailer.NotApproved as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/rfq/fetch-replies")
def fetch_replies() -> list[dict[str, Any]]:
    try:
        return mailer.fetch_replies()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ static ---

if STATIC_DIR.exists():
    # Mounted at the root, and last, so every API route above wins the match
    # first. html=True serves index.html for "/" and config.js by name --
    # exactly what Vercel does with the same directory, so local and
    # production behave identically.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")

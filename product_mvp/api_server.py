"""CEL product API -- "AI agent causal-execution & trust console".

Three endpoints, the whole product:

    POST /api/v1/verify   judge one (check, witness) pair, charge trust, seal it
    GET  /api/v1/stats    the numbers the dashboard's top row shows
    GET  /api/v1/ledger   the last N entries of the tamper-evident chain

Plus ``GET /`` which serves the dashboard, and ``GET /healthz``.

Run::

    uvicorn api_server:app --reload --port 8000     # from product_mvp/
    python api_server.py                            # same thing, no reload

The front end is plain HTML + vanilla JS served from disk: no template engine,
no bundler, no ``aiofiles``. ``fastapi`` and ``uvicorn`` are the only two
dependencies, and that is the point of the MVP -- a customer can read the whole
service in one sitting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool

from cel_engine import CelGateway, VerifyError, _Refused

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = BASE_DIR / "templates"
STATIC = BASE_DIR / "static"

#: How many consecutive FAILs quarantine an agent. The core also quarantines on
#: a single INVALID, which is not configurable -- "nobody looked" is not a
#: severity judgement, it is an absence of evidence.
FAIL_LIMIT = int(os.environ.get("CEL_FAIL_LIMIT", "3"))

GATEWAY = CelGateway(
    fail_limit=max(2, FAIL_LIMIT),
    ledger_log=os.environ.get("CEL_LEDGER_LOG") or None,
)

app = FastAPI(
    title="CEL -- Causal Execution Ledger",
    version="0.1.0",
    description="Intercept false completions, charge trust, keep a tamper-evident book.",
)

# The demo dashboard is served from this same origin, so CORS only matters for a
# customer's own agent harness calling the API from elsewhere.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    """One submission from a customer's agent.

    ``agent_id`` is the product's name for the core's ``agent``; ``agent`` is
    accepted too so the core's own vocabulary works unchanged.
    """

    agent_id: Optional[str] = None
    agent: Optional[str] = None
    intent: str
    #: ``StrictBool`` rather than ``bool``: the core refuses ``1`` and ``"ok"``
    #: as success, and a wire format that coerces them would reintroduce exactly
    #: the bug the ledger exists to catch. ``"yes"`` is a 422, not a PASS.
    check: StrictBool
    #: ``None`` (or omitting the field) means "nothing was inspected", which is
    #: INVALID -- not a failure, and never a pass.
    witness: Optional[StrictBool] = None
    #: Optional pointer to physical evidence. A real file path is sha256-hashed
    #: before being stored, so the entry is grounded in bytes, not a claim.
    artefact: str = ""
    cost: float = 0.0


class UnlockRequest(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    """Serve the console. Read from disk so editing the HTML needs no restart."""
    return HTMLResponse((TEMPLATES / "index.html").read_text(encoding="utf-8"))


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# the three endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/verify")
def verify(body: VerifyRequest) -> JSONResponse:
    """Judge one entry and book it.

    A FAIL is a **successful audit**, not an API error: it returns 200 with
    ``verdict: "FAIL"``. Only a closed book returns 423, because nothing was
    judged.
    """
    agent_id = (body.agent_id or body.agent or "").strip()
    try:
        result = GATEWAY.verify(
            agent_id=agent_id,
            intent=body.intent,
            check=body.check,
            witness=body.witness,
            artefact=body.artefact,
            cost=body.cost,
        )
    except _Refused as refused:
        return JSONResponse(status_code=423, content=GATEWAY.settle_refusal(refused))
    except VerifyError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from None
    return JSONResponse(status_code=200, content=result)


@app.get("/api/v1/stats")
def stats() -> Dict[str, Any]:
    """Global state: what was intercepted, how trusted, who is inflamed."""
    return GATEWAY.stats()


@app.get("/api/v1/ledger")
def ledger(limit: int = Query(default=50, ge=1, le=500)) -> Dict[str, Any]:
    """The newest ``limit`` entries of the hash chain, oldest-first."""
    return GATEWAY.ledger(limit=limit)


@app.get("/api/v1/ledger/verify")
def ledger_verify() -> Dict[str, Any]:
    """Re-walk the whole chain. A stored ``True`` would prove nothing."""
    return {
        "chain_ok": GATEWAY.verify_chain(),
        "chain_head": GATEWAY.ledger(limit=1)["chain_head"],
        "total_entries": GATEWAY.ledger(limit=1)["total_entries"],
    }


@app.post("/api/v1/agents/{agent_id}/unlock")
def unlock(agent_id: str, body: UnlockRequest) -> Dict[str, Any]:
    """Human-supervised release of a quarantined agent.

    Deliberately *not* reachable from ``verify``: an agent that could clear its
    own lock by filing a PASS would hold the very power the lock withholds.
    """
    try:
        return GATEWAY.unlock(agent_id, body.reason)
    except VerifyError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from None


def resolve_bind() -> tuple:
    """``(host, port)`` for the built-in runner.

    Two names per value, in this order, because hosted platforms disagree about
    who owns the port:

    * ``CEL_PORT`` / ``CEL_HOST`` — this service's own knobs, and what a laptop
      or ``docker compose`` uses.
    * ``PORT`` / ``HOST`` — what Render, Railway, Heroku and Fly inject. Ignoring
      them is the classic deploy failure: the app binds 8000, the platform
      probes something else, and the health check reports a dead service while
      the log says "running".

    A malformed value raises instead of falling back to 8000: silently listening
    somewhere other than where the operator asked is worse than not starting.
    """
    host = os.environ.get("CEL_HOST") or os.environ.get("HOST") or "0.0.0.0"
    raw_port = os.environ.get("CEL_PORT") or os.environ.get("PORT")
    if not raw_port:
        return host, 8000
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit(
            "CEL_PORT/PORT must be an integer, got {!r}".format(raw_port)
        ) from None
    if not 0 < port < 65536:
        raise SystemExit("port must be in 1..65535, got {}".format(port))
    return host, port


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    bind_host, bind_port = resolve_bind()
    uvicorn.run(app, host=bind_host, port=bind_port)

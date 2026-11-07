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

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool

from cel_engine import CelGateway, VerifyError, _Refused

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = BASE_DIR / "templates"
STATIC = BASE_DIR / "static"

log = logging.getLogger("cel.api")

# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------

#: Name of the variable only. The key itself is never in this file: it is read
#: from the environment at startup (and re-read by ``configure_auth()`` so the
#: tests can exercise both states without reloading the module).
API_KEY_ENV = "CEL_API_KEY"


class ApiKeyAuth:
    """A single shared bearer key, compared in constant time.

    Why a shared key and not users/tokens: this MVP writes to one process-local
    ledger, so there is exactly one "may write" decision to make. Anything more
    (per-tenant keys, rotation, scopes) is a feature with its own failure modes,
    and inventing it here would be the fake precision the rest of this package
    avoids.

    Why ``compare_digest``: a plain ``==`` returns early on the first differing
    byte, which leaks the key one byte at a time to anyone who can time the
    response. The attack is impractical over a network and costs nothing to
    close.

    **Unset means open.** That is a deliberate choice for a demo that must run
    from a bare ``docker compose up`` with no configuration, and it is a bad
    choice for a deployment. So it is made *loud* rather than quiet: the console
    shows a red banner, ``/api/v1/stats`` reports ``auth_enabled: false``, and
    startup logs a warning. An unprotected instance must be impossible to mistake
    for a protected one -- a silent default is how the open one ships.
    """

    def __init__(self) -> None:
        self._key = ""
        self.reload()

    def reload(self) -> None:
        """Re-read ``CEL_API_KEY``. Whitespace is stripped on both sides.

        Stripping is symmetric, so it cannot let a wrong key through: it only
        forgives the trailing newline that ``echo $KEY`` and copy-paste add.
        """
        self._key = (os.environ.get(API_KEY_ENV) or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    def check(self, presented: Optional[str]) -> bool:
        """True when the caller may write. True when no key is configured."""
        if not self._key:
            return True
        if not presented:
            return False
        candidate = presented.strip()
        if not candidate:
            return False
        return hmac.compare_digest(
            candidate.encode("utf-8"), self._key.encode("utf-8")
        )


AUTH = ApiKeyAuth()


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """FastAPI dependency for every endpoint that **writes** to the ledger.

    403 rather than 401 on purpose, as specified: the key is not a user
    credential, so there is no ``WWW-Authenticate`` challenge to offer and no
    browser login prompt to trigger. (Strictly, RFC 7235 reserves 401 for a
    missing credential and 403 for one that was presented and rejected; this
    endpoint returns 403 for both, because telling an anonymous prober which of
    the two it was is information it does not need.)
    """
    if not AUTH.check(x_api_key):
        raise HTTPException(
            status_code=403,
            detail=(
                "forbidden: missing or invalid X-API-Key. "
                "This endpoint writes to the trust ledger, so it requires the "
                "shared key configured in the server's {}. The dashboard's "
                "simulate button needs the same key pasted into its key field."
                .format(API_KEY_ENV)
            ),
        )


WRITE_ACCESS = [Depends(require_api_key)]


def configure_auth() -> ApiKeyAuth:
    """Re-read the key from the environment. Used by tests; safe to call twice."""
    AUTH.reload()
    return AUTH


def _log_line(message: str, level: int) -> None:
    """Emit on uvicorn's logger when it is configured.

    A warning logged to a logger with no handler is a warning written to a log
    nobody reads: ``cel.api`` propagates to a root logger that uvicorn does not
    attach a handler to, so ``INFO`` lines vanish (``WARNING``+ survives on
    ``logging.lastResort``, which is why the important one always shows up).

    The handler lives on ``uvicorn``, not ``uvicorn.error`` -- the child logger
    only inherits it by propagation. Checking the child would find no handlers
    and fall back, which is how the first version of this lost its own INFO line.
    """
    uvicorn_log = logging.getLogger("uvicorn")
    (uvicorn_log if uvicorn_log.handlers else log).log(level, message)


def _announce_auth() -> None:
    """Say out loud which mode the process started in.

    Called once at import. A warning that only exists in a README is a warning
    nobody sees on the instance that needs it.
    """
    if AUTH.enabled:
        _log_line(
            "auth: ENABLED -- POST /api/v1/verify and /api/v1/agents/{{id}}/unlock "
            "require X-API-Key (key from {}, {} chars)".format(
                API_KEY_ENV, len(AUTH._key)
            ),
            logging.INFO,
        )
    else:
        _log_line(
            "auth: DISABLED -- {} is not set. Anyone who can reach this port can "
            "charge an agent's trust. Fine for a local demo; set {} before "
            "exposing this instance.".format(API_KEY_ENV, API_KEY_ENV),
            logging.WARNING,
        )

#: How many consecutive FAILs quarantine an agent. The core also quarantines on
#: a single INVALID, which is not configurable -- "nobody looked" is not a
#: severity judgement, it is an absence of evidence.
FAIL_LIMIT = int(os.environ.get("CEL_FAIL_LIMIT", "3"))

def _build_gateway() -> CelGateway:
    """Build the gateway, turning an unwritable log path into a sentence.

    A mounted disk is the one thing in this deploy that behaves differently on a
    laptop than on a platform: the container runs as uid 10001, and a platform
    disk mounted at ``/data`` may be owned by root. The traceback that produces
    (``PermissionError`` inside ``mkdir``) names a path and nothing else, and it
    arrives at boot, in the least useful place. The common causes get named.
    """
    log_path = os.environ.get("CEL_LEDGER_LOG") or None
    try:
        return CelGateway(fail_limit=max(2, FAIL_LIMIT), ledger_log=log_path)
    except OSError as error:
        raise SystemExit(
            "CEL could not open its ledger log at {path!r}: {err}\n"
            "\n"
            "The write path must be on the persistent disk. Two usual causes:\n"
            "  1. CEL_LEDGER_LOG points somewhere the container cannot write. The\n"
            "     image runs as uid 10001, so mount the disk at /data and set\n"
            "     CEL_LEDGER_LOG=/data/ledger.jsonl.\n"
            "  2. The disk is mounted but owned by another user. Make /data\n"
            "     writable by uid 10001 (or run with --user \"$(id -u):$(id -g)\"\n"
            "     when you control the host).\n"
            "\n"
            "Unset CEL_LEDGER_LOG to run with an in-memory ledger only."
            .format(path=log_path, err=error)
        )


GATEWAY = _build_gateway()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Announce the auth mode once the server is actually up.

    Deliberately here rather than at import: at import time uvicorn has not
    configured logging yet, so the ENABLED line would be written to a logger with
    no handler and silently dropped. Startup is the first moment a log line can
    be seen by the person who deployed this.
    """
    _announce_auth()
    yield


app = FastAPI(
    title="CEL -- Causal Execution Ledger",
    version="0.3.0",
    description="Intercept false completions, charge trust, keep a tamper-evident book.",
    lifespan=_lifespan,
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
    #: Pointer to the evidence, e.g. ``hello.txt``. Stored as given: the server
    #: does **not** open it, because a remote client's path cannot exist here.
    artefact: str = ""
    #: SHA-256 of the witnessed effect, bare or ``sha256:<hex>``. Required when
    #: ``witness`` is true: a success claim with no digest is refused (422) and
    #: the ledger does not move. Optional for an honest failure, because a
    #: negative result has no artefact to hash and punishing it for that would
    #: teach agents to stop reporting failures.
    witness_hash: Optional[str] = None
    #: Client-chosen unique id for this action. Required when ``witness`` is
    #: true; a repeat is refused as a replay, so one action cannot be booked
    #: twice. See ``cel_engine.WitnessHashLimits`` for what the digest proves.
    trace_id: Optional[str] = None
    cost: float = 0.0


class UnlockRequest(BaseModel):
    reason: str = ""


class FalsifyRequest(BaseModel):
    """An observer's report that a booked commitment was false.

    Deliberately minimal: the server cannot go and look, so everything it can
    record is what the observer says. ``evidence`` is required rather than
    optional -- see the endpoint docstring for why an empty one is rejected.
    """

    trace_id: str
    #: ``file_not_found`` or ``hash_mismatch``. An enum rather than free text so
    #: the ledger can be counted by reason later, and so a typo is a 422 instead
    #: of a silently different finding.
    reason: str
    #: What was checked and what was seen, in the observer's words. This is the
    #: only material a later reviewer has.
    evidence: str = ""
    #: The digest actually computed from the artefact, for ``hash_mismatch``.
    observed_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def _asset_version() -> str:
    """A short digest over the static assets, used to bust caches.

    The console sits behind a CDN that caches ``/static/*`` for hours. A deploy
    that replaces ``app.js`` without changing its URL therefore ships nothing
    visible: the edge keeps serving the old file, and the operator sees a
    dashboard that disagrees with the API it is talking to. That happened once
    already, which is why the version is derived from the bytes instead of from
    a hand-bumped constant -- forgetting to bump it is the failure mode.

    Computed once per process: the files cannot change without a restart, since
    they are baked into the image.
    """
    digest = hashlib.sha256()
    for name in ("app.js", "style.css"):
        path = STATIC / name
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(name.encode("utf-8"))
    return digest.hexdigest()[:10]


_ASSET_VERSION = _asset_version()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    """Serve the console. Read from disk so editing the HTML needs no restart."""
    html = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__ASSET_V__", _ASSET_VERSION))


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# the three endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/verify", dependencies=WRITE_ACCESS)
def verify(body: VerifyRequest) -> JSONResponse:
    """Judge one entry and book it.

    Requires ``X-API-Key`` when the server has ``CEL_API_KEY`` set: this is the
    endpoint that spends trust, so it is the one an attacker would use.

    A FAIL is a **successful audit**, not an API error: it returns 200 with
    ``verdict: "FAIL"``. Only a closed book returns 423, because nothing was
    judged.

    A success claim without a usable digest -- or one that replays an already
    used ``trace_id`` -- returns 422 or 409 with ``verdict: "INVALID"`` and
    ``ledger_written: false``. Nothing is booked: a malformed submission is not
    a judgement about an agent, and putting a permanent INVALID row on the chain
    for a client bug would punish the wrong party.
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
            witness_hash=body.witness_hash,
            trace_id=body.trace_id,
        )
    except _Refused as refused:
        return JSONResponse(status_code=423, content=GATEWAY.settle_refusal(refused))
    except VerifyError as error:
        # Refused at the door. The body names the ledger state explicitly so a
        # client cannot read "no error object" as "probably recorded".
        return JSONResponse(
            status_code=error.status_code,
            content={
                "verdict": "INVALID",
                "detail": error.message,
                "ledger_written": False,
                "agent_id": agent_id,
                "trace_id": body.trace_id or "",
                "witness": body.witness,
                "witness_hash": body.witness_hash or "",
            },
        )
    return JSONResponse(status_code=200, content=result)


@app.post("/api/v1/falsify", dependencies=WRITE_ACCESS)
def falsify(body: FalsifyRequest) -> JSONResponse:
    """Overturn a booked PASS after checking the physical artefact.

    The server could not check the artefact when the claim arrived -- it cannot
    reach a client's disk -- so the claim was booked as a *commitment*. This
    endpoint is the other half of that bargain, and the reason a commitment is
    worth recording at all: an observer who later looks can report what they
    found, and the agent's trust goes to zero.

    Requires the same key as ``/verify``. Not because it writes (it does, an
    appended ``REFUTED`` entry), but because it *destroys* trust: an anonymous
    falsify endpoint would be a one-request way to ruin any agent, which is a
    worse hole than the lie it corrects. "Any observer can refute" therefore
    means "any observer holding the audit key".

    What this endpoint does not establish: that the refuter is truthful. A
    falsification is a claim in the opposite direction, made with no more
    ability to inspect than the original. It is *attributable* though -- the
    evidence string is sealed on the chain -- so a false accusation is itself on
    the record and can be rebutted.

    The original PASS is never rewritten; a ``REFUTED`` entry is appended that
    points back at it (``refutes_seq`` / ``refutes_hash``). Rewriting the
    original would change its body, and the body is covered by its chain hash --
    so "correcting" the record in place would make ``verify_chain()`` fail and
    erase the very thing being audited: that the system said PASS.
    """
    try:
        result = GATEWAY.falsify(
            trace_id=body.trace_id,
            reason=body.reason,
            evidence=body.evidence,
            observed_hash=body.observed_hash,
        )
    except VerifyError as error:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "verdict": "INVALID",
                "detail": error.message,
                "ledger_written": False,
                "trace_id": body.trace_id,
            },
        )
    return JSONResponse(status_code=200, content=result)


@app.get("/api/v1/stats")
def stats() -> Dict[str, Any]:
    """Global state: what was intercepted, how trusted, who is inflamed.

    Anonymous by design: it is what the dashboard polls, and a monitoring page
    that demands a key to render is a monitoring page that is blank when someone
    needs it. It exposes counts and trust scores, never key material.

    ``auth_enabled`` is included so a client can tell an unprotected instance
    from a protected one instead of assuming.
    """
    data = GATEWAY.stats()
    data["auth_enabled"] = AUTH.enabled
    return data


@app.get("/api/v1/ledger")
def ledger(limit: int = Query(default=50, ge=1, le=500)) -> Dict[str, Any]:
    """The newest ``limit`` entries of the hash chain, oldest-first.

    Anonymous, like ``/stats``. This is the audit trail an operator may be
    looking at *because* something went wrong, so it must not be behind the same
    credential as the thing that went wrong.
    """
    return GATEWAY.ledger(limit=limit)


@app.get("/api/v1/ledger/verify")
def ledger_verify() -> Dict[str, Any]:
    """Re-walk the whole chain. A stored ``True`` would prove nothing."""
    return {
        "chain_ok": GATEWAY.verify_chain(),
        "chain_head": GATEWAY.ledger(limit=1)["chain_head"],
        "total_entries": GATEWAY.ledger(limit=1)["total_entries"],
    }


@app.post("/api/v1/agents/{agent_id}/unlock", dependencies=WRITE_ACCESS)
def unlock(agent_id: str, body: UnlockRequest) -> Dict[str, Any]:
    """Human-supervised release of a quarantined agent.

    Guarded by the same key as ``/verify``, and not because it writes an entry:
    an unauthenticated unlock would let anyone permanently void the one penalty
    that actually stops a misbehaving agent, which is a worse hole than a forged
    verdict. "Requires a human" has to mean a specific human, or it means no one.

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

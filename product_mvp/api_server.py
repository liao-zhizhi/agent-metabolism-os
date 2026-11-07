"""CEL product API -- "AI agent causal-execution & trust console".

Three endpoints, the whole product:

    POST /api/v1/verify   judge one (check, witness) pair, charge trust, seal it
    GET  /api/v1/stats    the numbers the dashboard's top row shows
    GET  /api/v1/ledger   the last N entries of the tamper-evident chain

Plus ``GET /`` which serves the dashboard, and ``GET /healthz``.

Two gates stand in front of the write path, in this order: a per-client-IP
sliding-window budget (``CEL_RATE_LIMIT`` writes per ``CEL_RATE_WINDOW``
seconds, ``429`` with ``Retry-After`` when spent) and the identity check. Reads
are anonymous and never rate-limited: the dashboard polls them every two
seconds.

Identity has three outcomes, and the middle one is the product's funnel:

* ``X-API-Key`` valid -> the entry is **sealed into the production chain**;
* no credential at all -> the **anonymous trial** judges the same payload with
  the same engine against a disposable in-memory ledger, at
  ``CEL_ANON_LIMIT``/``CEL_ANON_WINDOW`` (default 1 per 300s). It cannot
  falsify or unlock, and it does not touch the chain;
* an invalid key -> ``403``, with no downgrade to the trial.

The trial never enters the production chain on purpose. Every entry there is a
claim with an author behind it; an anonymous row has none, so it is not a weaker
entry, it is a different kind of object.

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
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
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

#: Access tiers for a write. The tier is decided once per request by
#: :func:`require_verify_access` / :func:`require_keyed_access` and stashed on
#: ``request.state`` so the handler does not re-derive it (a second derivation
#: is a second place for the two answers to disagree).
TIER_OPEN = "open"            # no CEL_API_KEY configured: local demo, no gate
TIER_KEYED = "keyed"          # a valid X-API-Key was presented
TIER_ANONYMOUS = "anonymous"  # no credential: read-only trial, judged but unsealed

_ANON_FORBIDDEN = (
    "forbidden: no X-API-Key. The cloud trial may judge a submission, but it "
    "cannot falsify a committed record or release a quarantine -- both destroy "
    "state that only an identified operator may destroy. Set X-API-Key, or run "
    "the sidecar locally where your own audit trail is yours to write."
)

_INVALID_KEY = (
    "forbidden: invalid X-API-Key. This endpoint writes to the trust ledger, so "
    "it requires the shared key configured in the server's {}. A rejected key "
    "does not fall back to the anonymous trial: a caller that guessed once gets "
    "the same answer as a caller that guessed a thousand times."
    .format(API_KEY_ENV)
)


def _presented_key(request: Request) -> str:
    """The X-API-Key header, or ``""``. Whitespace-only counts as absent."""
    return (request.headers.get("x-api-key") or "").strip()


def _caller_of(request: Request) -> str:
    """The resolved client address, computed once by ``enforce_write_rate``.

    Falls back to recomputing, so a dependency wired without the rate limit
    (a test, or a future read path) still gets a usable identity instead of a
    ``KeyError`` at the exact moment it is refusing someone.
    """
    cached = getattr(request.state, "client_ip", None)
    return cached if cached else _client_ip(request)


def require_keyed_access(request: Request) -> str:
    """Gate for the writes that **destroy** state: falsify, unlock.

    No anonymous path, and no fallback: a wrong key is 403, never a downgrade to
    the trial tier. Downgrading on a bad credential would make "the key did not
    work" indistinguishable from "the key did not matter", and it would let a
    caller probe for a key while still doing useful work.
    """
    presented = _presented_key(request)
    if AUTH.check(presented or None):
        tier = TIER_KEYED if AUTH.enabled else TIER_OPEN
        request.state.write_tier = tier
        return tier
    # Distinguish "no credential" from "a credential that failed". Both are 403,
    # but only one of them is a guess, and the sentence a developer reads should
    # be different: "you need a key, here is why this endpoint is keyed" versus
    # "that key is wrong".
    raise HTTPException(
        status_code=403,
        detail=_ANON_FORBIDDEN if not presented else _INVALID_KEY,
    )


def require_verify_access(request: Request) -> str:
    """Gate for ``/api/v1/verify``: identified callers, or the anonymous trial.

    Three outcomes, and the middle one is the new product decision:

    * a valid key -> full budget, sealed into the production chain;
    * **no credential at all -> the anonymous trial**: judged by the same
      engine, but against a disposable in-memory ledger (see ``trial_gateway``),
      so a stranger's row never enters the chain whose whole claim is
      attribution. This is the funnel: see the verdict, then install locally to
      keep the record;
    * an invalid key -> 403. Not the trial -- a wrong key is a failed attempt,
      and rewarding it with a downgrade is how a gate becomes advisory.

    403 rather than 401 throughout, as before: the key is not a user credential,
    so there is no ``WWW-Authenticate`` challenge to offer.
    """
    presented = _presented_key(request)
    if AUTH.check(presented or None):
        tier = TIER_KEYED if AUTH.enabled else TIER_OPEN
        request.state.write_tier = tier
        return tier
    if presented:
        raise HTTPException(status_code=403, detail=_INVALID_KEY)
    if ANON_LIMITER.limit <= 0:
        raise HTTPException(
            status_code=403,
            detail=(
                "forbidden: the anonymous cloud trial is disabled on this "
                "instance ({}={}). Set X-API-Key, or run the sidecar locally."
                .format(ANON_LIMIT_ENV, ANON_LIMITER.limit)
            ),
        )
    allowed, retry_after = ANON_LIMITER.hit(_caller_of(request))
    if not allowed:
        retry = max(1, int(retry_after + 0.999))
        raise HTTPException(
            status_code=429,
            detail=(
                "the anonymous cloud trial allows {} write{} per {:g} seconds "
                "from this address, and you have used {} of them. Retry in {}s. "
                "To get more quota or use it in production, install the sidecar "
                "locally (see /api/v1/stats -> write_policy.install) -- your "
                "data never leaves your machine. Reads stay free."
                .format(
                    ANON_LIMITER.limit,
                    "" if ANON_LIMITER.limit == 1 else "s",
                    ANON_LIMITER.window,
                    ANON_LIMITER.limit,
                    retry,
                )
            ),
            headers={
                "Retry-After": str(retry),
                "X-Trial-Remaining": "0",
            },
        )
    request.state.write_tier = TIER_ANONYMOUS
    return TIER_ANONYMOUS


def configure_auth() -> ApiKeyAuth:
    """Re-read the key from the environment. Used by tests; safe to call twice."""
    AUTH.reload()
    return AUTH


# ---------------------------------------------------------------------------
# rate limiting (write path only)
# ---------------------------------------------------------------------------
#: The write budget, per client IP, per window. These are the two knobs a
#: deployment may want to change; everything else here is fixed policy.
RATE_LIMIT_ENV = "CEL_RATE_LIMIT"
RATE_WINDOW_ENV = "CEL_RATE_WINDOW"
#: Set to a non-empty value when the process sits behind a reverse proxy that
#: appends ``X-Forwarded-For``. Off by default on purpose -- see ``_client_ip``.
TRUST_PROXY_ENV = "CEL_TRUST_PROXY"

DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW = 60.0

#: The anonymous trial's budget, deliberately a different order of magnitude
#: from the keyed one. ``CEL_ANON_LIMIT=0`` refuses anonymous writes outright,
#: which is the setting a customer who wants no public writes at all will use.
ANON_LIMIT_ENV = "CEL_ANON_LIMIT"
ANON_WINDOW_ENV = "CEL_ANON_WINDOW"
DEFAULT_ANON_LIMIT = 1
DEFAULT_ANON_WINDOW = 300.0

#: Where the anonymous trial's rows go: nowhere the production chain can see
#: them. See ``trial_gateway`` for why this is a second gateway and not a flag.
TRIAL_MAX_ENTRIES = 500

#: Stop the per-key dictionary from growing without bound on a public instance.
RATE_SWEEP_AT = 4096


def _env_number(name: str, default: float, cast) -> float:
    """Read a non-negative number, refusing to start on nonsense.

    A typo'd ``CEL_RATE_LIMIT=abc`` must not silently become "no limit": the
    whole point of the knob is that the operator believes it is on. Failing at
    boot is the only outcome that cannot be mistaken for protection.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise SystemExit("{} must be a number, got {!r}".format(name, raw)) from None
    if value < 0:
        raise SystemExit("{} must not be negative, got {!r}".format(name, raw))
    return value


class RateLimiter:
    """A sliding-window request counter keyed by client IP.

    Sliding window, not a fixed bucket, because a fixed bucket lets a caller
    spend the whole budget at the end of one window and again at the start of
    the next -- ``2 * limit`` writes in a few milliseconds, which is exactly the
    burst the limit exists to stop.

    ``limit=0`` disables the check (the documented escape hatch for a load test
    or a single trusted caller); it is off by *configuration*, never by
    accident, because a limiter that fails open on a malformed setting is worse
    than no limiter at all.

    Every write is one entry in a deque of monotonic timestamps. Monotonic
    rather than wall-clock, so an NTP step cannot hand out a fresh budget.
    """

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT,
                 window: float = DEFAULT_RATE_WINDOW,
                 clock=None) -> None:
        self.limit = limit
        self.window = window
        # Injectable so the window can be tested without sleeping for a minute.
        self._clock = clock or time.monotonic
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Drop every recorded hit. Tests call this; nothing else should."""
        with self._lock:
            self._hits.clear()

    def hit(self, key: str) -> Tuple[bool, float]:
        """Book one attempt. Returns ``(allowed, retry_after_seconds)``.

        The attempt is booked only when it is allowed, so a client that keeps
        hammering does not extend its own ban by doing so -- the window empties
        on schedule and the next allowed request is predictable.
        """
        if self.limit <= 0:
            return True, 0.0
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = self._hits[key] = deque()
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, max(bucket[0] + self.window - now, 0.0)
            bucket.append(now)
            if len(self._hits) > RATE_SWEEP_AT:
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return True, 0.0

    def peek(self, key: str) -> Tuple[int, float]:
        """``(remaining, retry_after)`` **without** spending an attempt.

        The console needs to say "N trial writes left" *before* the button is
        pressed. Asking that question must not consume the budget it reports,
        or the page would spend a write every time it polled its own counter.
        """
        if self.limit <= 0:
            return 0, 0.0
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return self.limit, 0.0
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            used = len(bucket)
            if used >= self.limit:
                return 0, max(bucket[0] + self.window - now, 0.0)
            return self.limit - used, 0.0


def _client_ip(request: Request) -> str:
    """The identity the budget is spent against.

    ``request.client.host`` by default, which is the only value the server can
    see for itself. ``X-Forwarded-For`` is *ignored* unless ``CEL_TRUST_PROXY``
    is set, because that header is attacker-controlled: trusting it unasked
    turns the limiter into a no-op (send a new fake IP per request and the
    budget never fills) and, worse, lets anyone burn a specific victim's
    budget. Behind a proxy the operator must say so.

    When it is trusted, the **right-most** entry is used, not the left-most.
    ``X-Forwarded-For`` is a list that each hop *appends* to, so everything left
    of the last comma arrived from the client and is whatever they chose to
    type: ``X-Forwarded-For: 1.2.3.4`` sent through Cloudflare arrives as
    ``1.2.3.4, <real client>``. Reading the left-most entry would therefore let
    anyone mint an unlimited budget by inventing an address per request --
    through the very proxy this setting exists to support. The right-most entry
    is the one the last hop (our proxy) wrote, and that hop is the only party
    here that cannot lie to itself.

    Caveat, stated because it is a real boundary: this assumes exactly one proxy
    hop. With two chained proxies the right-most entry is the second proxy; list
    both in the deployment instead of pretending otherwise.
    """
    if (os.environ.get(TRUST_PROXY_ENV) or "").strip():
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # rsplit, so a trailing comma or whitespace yields nothing rather
            # than silently promoting the client's own value.
            last = forwarded.rsplit(",", 1)[-1].strip()
            if last:
                return last
    client = request.client
    return client.host if client and client.host else "unknown"


def _rate_knobs() -> Tuple[int, float]:
    """``(limit, window)`` from the environment, refusing inconsistent pairs.

    A window of zero with a non-zero limit would mean "the budget is always
    empty", i.e. no limit at all, while the boot log claims one is on. The only
    supported way to turn this off is ``CEL_RATE_LIMIT=0``.
    """
    limit = int(_env_number(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT, int))
    window = _env_number(RATE_WINDOW_ENV, DEFAULT_RATE_WINDOW, float)
    if limit > 0 and window <= 0:
        raise SystemExit(
            "{}={} needs {} > 0; use {}={} to disable the limit"
            .format(RATE_LIMIT_ENV, limit, RATE_WINDOW_ENV, RATE_LIMIT_ENV, 0)
        )
    return limit, window


_initial_limit, _initial_window = _rate_knobs()

WRITE_LIMITER = RateLimiter(limit=_initial_limit, window=_initial_window)


def configure_rate_limit() -> RateLimiter:
    """Re-read both knobs and clear the counters. Used by tests."""
    WRITE_LIMITER.limit, WRITE_LIMITER.window = _rate_knobs()
    WRITE_LIMITER.reset()
    return WRITE_LIMITER


def enforce_write_rate(request: Request) -> None:
    """FastAPI dependency: spend one unit of the caller's write budget.

    Runs **before** the key check on purpose. Brute-forcing ``X-API-Key`` is the
    cheapest attack on this service -- every guess costs the attacker one
    request and costs the server one HMAC comparison -- so the budget has to be
    spent before the comparison, not after it. The visible consequence is that a
    caller with the wrong key sees 429 rather than 403 once it is out of budget;
    that is the correct trade, and the 403 tests reset the budget before use.
    """
    caller = _client_ip(request)
    request.state.client_ip = caller
    allowed, retry_after = WRITE_LIMITER.hit(caller)
    if allowed:
        return
    retry = max(1, int(retry_after + 0.999))
    # Uvicorn's access line names the *peer*, which behind docker-proxy is
    # always 172.17.0.1 -- so the one log an operator reads cannot answer "who".
    # This line carries the address the budget was actually spent against, which
    # is the resolved X-Forwarded-For entry on a trusted-proxy deploy.
    #
    # INFO, not WARNING: a client that keeps hammering gets one of these per
    # request on top of uvicorn's own access line, and a limiter should not be
    # able to amplify an attack into a disk-full incident. INFO still shows up
    # in production (uvicorn owns the handler) and stays quiet under `unittest`,
    # where no handler is configured.
    _log_line(
        "rate limit: refused {} (budget {} / {:g}s)".format(
            caller, WRITE_LIMITER.limit, WRITE_LIMITER.window
        ),
        logging.INFO,
    )
    # Name the address in the 429 body too. It is the only way a client can tell
    # "the limiter is working" from "everyone is behind one proxy and shares one
    # budget", and it is not a secret: the caller already knows where it is.
    raise HTTPException(
        status_code=429,
        detail=(
            "too many writes from {}: the limit is {} per {} seconds. "
            "Retry in {}s. Reads (/api/v1/stats, /api/v1/ledger) are not "
            "limited, so the console keeps rendering."
            .format(caller, WRITE_LIMITER.limit, int(WRITE_LIMITER.window), retry)
        ),
        headers={"Retry-After": str(retry)},
    )


#: Every endpoint that writes to the **production** chain and destroys state.
#: Rate limit first (cheap, and it gates key guessing), then the key.
WRITE_ACCESS = [Depends(enforce_write_rate), Depends(require_keyed_access)]

#: ``/verify`` additionally admits the anonymous trial. Same global budget
#: first, then the tier decision, which may spend the much smaller trial
#: budget. Ordering is what keeps key guessing capped at the keyed rate.
VERIFY_ACCESS = [Depends(enforce_write_rate), Depends(require_verify_access)]


def _anon_knobs() -> Tuple[int, float]:
    """``(limit, window)`` for the anonymous trial; ``0`` means "refuse"."""
    limit = int(_env_number(ANON_LIMIT_ENV, DEFAULT_ANON_LIMIT, int))
    window = _env_number(ANON_WINDOW_ENV, DEFAULT_ANON_WINDOW, float)
    if limit > 0 and window <= 0:
        raise SystemExit(
            "{}={} needs {} > 0; use {}={} to disable the anonymous trial"
            .format(ANON_LIMIT_ENV, limit, ANON_WINDOW_ENV, ANON_LIMIT_ENV, 0)
        )
    return limit, window


_initial_anon_limit, _initial_anon_window = _anon_knobs()

ANON_LIMITER = RateLimiter(
    limit=_initial_anon_limit, window=_initial_anon_window
)


def configure_anon_limit() -> RateLimiter:
    """Re-read the trial knobs and clear the counters. Used by tests."""
    ANON_LIMITER.limit, ANON_LIMITER.window = _anon_knobs()
    ANON_LIMITER.reset()
    return ANON_LIMITER


# ---------------------------------------------------------------------------
# the anonymous trial's ledger
# ---------------------------------------------------------------------------
#: A second gateway, not a flag on the first one.
#:
#: Why not let a trial row into the production chain, namespaced or otherwise:
#: every entry in that chain is a claim with an author behind it -- that is the
#: product. An anonymous row has no author, so it is not a weaker entry, it is a
#: different kind of object. Putting one on the chain would mean the audit trail
#: can be written by anyone who can reach the port, and "tamper-evident" would
#: then be describing a log that strangers can append to at will.
#:
#: Why a separate gateway rather than "judge but do not book": the engine books
#: as it judges (trust and penalty ledgers move, the chain is sealed). A
#: judge-only path would mean threading a dry-run flag through the audited core,
#: and every such flag is a new way for the two modes to disagree. A second
#: instance costs one object and keeps the judged semantics identical -- same
#: class, same code, different book.
#:
#: ``fail_limit`` is effectively infinite so the trial cannot reach the
#: quarantine state. A visitor who fails three times should not be met with a
#: 423 they cannot clear without a key they do not have: that is a dead end
#: dressed as a lesson, and the lesson (quarantine) is available locally.
_trial_lock = threading.Lock()
_trial_gateway: Optional[CelGateway] = None
_trial_dropped = 0


def trial_gateway() -> CelGateway:
    """The disposable in-memory gateway the anonymous trial writes to."""
    global _trial_gateway, _trial_dropped
    with _trial_lock:
        if _trial_gateway is None:
            _trial_gateway = CelGateway(fail_limit=10**9, ledger_log=None)
            return _trial_gateway
        # Bound the memory a public endpoint can cost. Resetting is honest --
        # nobody was ever promised a durable record here -- and it is the only
        # cap that holds when the input is "one stranger per address".
        if _trial_gateway.ledger(limit=1)["total_entries"] >= TRIAL_MAX_ENTRIES:
            _trial_dropped += 1
            _trial_gateway = CelGateway(fail_limit=10**9, ledger_log=None)
        return _trial_gateway


def reset_trial() -> None:
    """Drop the trial ledger. Tests call this; a deploy does not need to."""
    global _trial_gateway
    with _trial_lock:
        _trial_gateway = None


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
        if ANON_LIMITER.limit > 0:
            _log_line(
                "trial: anonymous /api/v1/verify is OPEN at {} write(s) per "
                "{:g}s per IP, judged but NOT sealed into the chain; falsify and "
                "unlock still need the key. Disable with {}={}.".format(
                    ANON_LIMITER.limit,
                    ANON_LIMITER.window,
                    ANON_LIMIT_ENV,
                    0,
                ),
                logging.INFO,
            )
        else:
            _log_line(
                "trial: anonymous writes DISABLED ({}={}); every write needs the "
                "key.".format(ANON_LIMIT_ENV, ANON_LIMITER.limit),
                logging.INFO,
            )
    else:
        _log_line(
            "auth: DISABLED -- {} is not set. Anyone who can reach this port can "
            "charge an agent's trust. Fine for a local demo; set {} before "
            "exposing this instance.".format(API_KEY_ENV, API_KEY_ENV),
            logging.WARNING,
        )
    if WRITE_LIMITER.limit > 0:
        _log_line(
            "rate limit: writes are capped at {} per {:g}s per client IP "
            "(proxy header {})".format(
                WRITE_LIMITER.limit,
                WRITE_LIMITER.window,
                "TRUSTED via " + TRUST_PROXY_ENV
                if (os.environ.get(TRUST_PROXY_ENV) or "").strip()
                else "not trusted; X-Forwarded-For ignored",
            ),
            logging.INFO,
        )
    else:
        _log_line(
            "rate limit: DISABLED via {}={}. Only acceptable for a load test or "
            "a single trusted caller.".format(RATE_LIMIT_ENV,
                                              os.environ.get(RATE_LIMIT_ENV)),
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


#: ``@app.get`` registers GET only, so ``HEAD /`` was a 405 with ``Allow: GET``.
#: RFC 9110 says a server that answers GET must answer HEAD, and every uptime
#: monitor and ``curl -I`` reaches for HEAD first -- a 405 there reads as "the
#: demo is down" to exactly the people being shown it. The body of a HEAD reply
#: is discarded by the client anyway, so this answers without building the page.
@app.head("/", include_in_schema=False)
def dashboard_head() -> Response:
    return Response(status_code=200, media_type="text/html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.head("/healthz", include_in_schema=False)
def healthz_head() -> Response:
    """Same reason as ``dashboard_head``: platform health checks probe with HEAD."""
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# the three endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/verify", dependencies=VERIFY_ACCESS)
def verify(body: VerifyRequest, request: Request) -> JSONResponse:
    """Judge one entry and book it.

    Two ways in, and the difference is *which book* the result lands in:

    * with ``X-API-Key`` (when the server has ``CEL_API_KEY`` set) the entry is
      sealed into the production chain;
    * with no credential at all the **anonymous trial** judges the same payload
      with the same engine but books it to a disposable in-memory ledger, and
      the response says ``sealed: false`` / ``channel: "anonymous_trial"``. The
      production chain is not touched, because a row with no author behind it is
      not evidence -- see ``trial_gateway``.

    A FAIL is a **successful audit**, not an API error: it returns 200 with
    ``verdict: "FAIL"``. Only a closed book returns 423, because nothing was
    judged -- and a trial book never closes, so the funnel has no dead end.

    A success claim without a usable digest -- or one that replays an already
    used ``trace_id`` -- returns 422 or 409 with ``verdict: "INVALID"`` and
    ``ledger_written: false``. Nothing is booked: a malformed submission is not
    a judgement about an agent, and putting a permanent INVALID row on the chain
    for a client bug would punish the wrong party.
    """
    agent_id = (body.agent_id or body.agent or "").strip()
    tier = getattr(request.state, "write_tier", TIER_KEYED)
    anonymous = tier == TIER_ANONYMOUS
    book = trial_gateway() if anonymous else GATEWAY
    try:
        result = book.verify(
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
        return JSONResponse(status_code=423, content=book.settle_refusal(refused))
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
                "channel": tier,
                "sealed": False,
            },
        )
    if not anonymous:
        # ``sealed`` on the keyed path too. A client should be able to read
        # "this landed in the chain" from the response instead of inferring it
        # from which credential it happened to send.
        result["sealed"] = True
        result["channel"] = tier
        return JSONResponse(status_code=200, content=result)

    # The trial's verdict is real; its *record* is not. Stripping the chain
    # fields is the difference between "here is a judgement" and "here is a
    # receipt for a sealed entry", and a client that screenshots the response
    # must not be able to mistake one for the other. ``seq``/``chain_hash`` are
    # the trial ledger's own coordinates, which mean nothing outside it.
    trial = {
        k: v
        for k, v in result.items()
        if k not in ("seq", "chain_hash", "prev_hash")
    }
    trial["ledger_written"] = False
    trial["sealed"] = False
    trial["channel"] = "anonymous_trial"
    trial["detail"] = (
        "judged by the same engine, not sealed: no credential, so this row has "
        "no author and does not enter the public chain. Run the sidecar locally "
        "to keep an audit trail that is yours."
    )
    remaining, _ = ANON_LIMITER.peek(_caller_of(request))
    return JSONResponse(
        status_code=200,
        content=trial,
        headers={"X-Trial-Remaining": str(remaining)},
    )


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
def stats(request: Request) -> Dict[str, Any]:
    """Global state: what was intercepted, how trusted, who is inflamed.

    Anonymous by design: it is what the dashboard polls, and a monitoring page
    that demands a key to render is a monitoring page that is blank when someone
    needs it. It exposes counts and trust scores, never key material.

    ``auth_enabled`` is included so a client can tell an unprotected instance
    from a protected one instead of assuming. ``rate_limit`` reports the write
    budget for the same reason: a console that cannot see the ceiling shows a
    429 as a mystery rather than as policy.

    ``write_policy`` is the machine-readable version of the funnel copy, and
    ``trial_remaining`` is this caller's own count. The console renders both
    rather than hard-coding "1 per 5 minutes" into the page: a number in the
    HTML is a number that goes stale the first time the environment changes,
    and it would be the page contradicting the limiter it is advertising.
    """
    data = GATEWAY.stats()
    data["auth_enabled"] = AUTH.enabled
    data["rate_limit"] = {
        "limit": WRITE_LIMITER.limit,
        "window_seconds": WRITE_LIMITER.window,
        "scope": "writes only, per client IP",
    }
    data["write_policy"] = {
        "anonymous": {
            "enabled": ANON_LIMITER.limit > 0,
            "limit": ANON_LIMITER.limit,
            "window_seconds": ANON_LIMITER.window,
            "endpoints": ["/api/v1/verify"],
            "seals": False,
            "can_falsify": False,
            "can_unlock": False,
            "detail": "judged by the same engine against a disposable ledger; "
                      "nothing is sealed, so a row with no author never enters "
                      "the public chain",
        },
        "keyed": {
            "limit": WRITE_LIMITER.limit,
            "window_seconds": WRITE_LIMITER.window,
            "seals": True,
            "can_falsify": True,
            "can_unlock": True,
        },
        "install": {
            "pip": "pip install harness-audit-sidecar",
            "mcp_server": "harness-audit-server",
            "repo": "https://github.com/liao-zhizhi/harness-audit-sidecar",
            "spec_repo": "https://github.com/liao-zhizhi/causal-evolution-paper",
        },
    }
    # This caller's own remaining trial writes. Per-caller by construction --
    # it is computed from the address the budget is spent against, so it reveals
    # nothing about anyone else.
    if ANON_LIMITER.limit > 0:
        remaining, retry = ANON_LIMITER.peek(_client_ip(request))
        data["write_policy"]["anonymous"]["remaining"] = remaining
        data["write_policy"]["anonymous"]["retry_after"] = int(retry + 0.999)
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

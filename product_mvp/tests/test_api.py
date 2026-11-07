"""HTTP-level tests, including the one that matters: does the page render.

Requires ``fastapi``'s test client, which needs ``httpx``. Skipped -- not
failed -- when the web stack is not installed, so the engine suite still runs
on a bare interpreter.

    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient

    import api_server
    from api_server import AUTH, GATEWAY, app

    HAVE_WEB = True
except Exception as exc:  # pragma: no cover - exercised on a bare interpreter
    HAVE_WEB = False
    IMPORT_ERROR = exc


def _reset() -> None:
    """Each test starts from empty books and a closed-by-default key.

    ``configure_auth()`` re-reads ``CEL_API_KEY`` into the module-level holder,
    so the two auth states can be exercised without reloading the module (which
    would build a second gateway and quietly detach the routes from it).

    The write budget is reset too, and deliberately set out of the way: these
    tests are about the ledger and the key, and a shared 5-per-minute budget
    would make them fail by arithmetic rather than by behaviour (one test books
    seven entries). ``RateLimitTests`` below puts the shipping default back and
    tests the limiter itself.
    """
    GATEWAY.__init__(fail_limit=GATEWAY.fail_limit, ledger_log=None)
    configure_auth("")
    os.environ["CEL_RATE_LIMIT"] = str(UNLIMITED_FOR_TESTS)
    api_server.configure_rate_limit()
    os.environ.pop("CEL_RATE_LIMIT", None)
    # The trial counters and the trial ledger are process-global too. Leaving
    # them dirty would make "the second anonymous request is 429" depend on
    # which test ran first, which is the kind of failure that looks like a
    # limiter bug for an afternoon.
    api_server.reset_trial()
    api_server.configure_anon_limit()


#: Large enough that no behavioural test can spend it by accident.
UNLIMITED_FOR_TESTS = 10_000


def configure_auth(key: str) -> None:
    """Point the running server at ``key`` (empty string = no auth)."""
    if key:
        os.environ["CEL_API_KEY"] = key
    else:
        os.environ.pop("CEL_API_KEY", None)
    api_server.configure_auth()


#: A syntactically valid digest for tests that only care about the *shape* of a
#: grounded pass. It is deliberately the digest of a string nobody hashed a file
#: to produce: this layer validates form, and pretending otherwise in a fixture
#: would hide the boundary that ``WitnessHashLimits`` documents.
TEST_HASH = "sha256:" + "a" * 64

_trace_counter = [0]


def grounded(trace: str = "", **overrides) -> dict:
    """A success claim that satisfies the digest requirement."""
    if not trace:
        _trace_counter[0] += 1
        trace = "trace-{}".format(_trace_counter[0])
    body = {
        "intent": "x",
        "check": True,
        "witness": True,
        "witness_hash": TEST_HASH,
        "trace_id": trace,
    }
    body.update(overrides)
    return body


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    # -- page ------------------------------------------------------------

    def test_dashboard_renders_and_references_its_assets(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Causal Execution Ledger", html)
        self.assertIn("成功拦截假完成数", html)
        self.assertIn("/static/app.js", html)
        self.assertIn("/static/style.css", html)

    def test_static_assets_are_served(self) -> None:
        for path in ("/static/app.js", "/static/style.css", "/favicon.svg"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_asset_urls_are_versioned(self) -> None:
        """The CDN caches /static/* for hours, so the URL must change on deploy.

        This is a regression test for a real deploy: app.js was replaced but the
        edge kept serving the old file for four hours, and the console showed a
        verdict with no evidence tag while the API it polled already had the
        field. A hand-bumped constant invites the same failure the next time
        someone forgets, so the version is a digest over the bytes.
        """
        html = self.client.get("/").text
        self.assertNotIn("__ASSET_V__", html, "template placeholder was not substituted")
        self.assertRegex(html, r'/static/app\.js\?v=[0-9a-f]{10}')
        self.assertRegex(html, r'/static/style\.css\?v=[0-9a-f]{10}')
        # Same digest on both, or one asset can go stale alone.
        versions = set(re.findall(r'\?v=([0-9a-f]{10})', html))
        self.assertEqual(len(versions), 1, "assets disagree about the version")

    def test_healthz(self) -> None:
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})

    def test_head_works_where_get_does(self) -> None:
        """``curl -I`` and uptime monitors use HEAD; a 405 there reads as an outage.

        ``@app.get`` alone registers GET only, so both paths answered HEAD with
        405 and ``Allow: GET``. RFC 9110 requires HEAD wherever GET is served.
        """
        for path in ("/", "/healthz"):
            with self.subTest(path=path):
                response = self.client.head(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"", "a HEAD reply must carry no body")

    # -- verify ----------------------------------------------------------

    def test_verify_false_completion(self) -> None:
        response = self.client.post(
            "/api/v1/verify",
            json={
                "agent_id": "robot_arm_01",
                "intent": "write to database",
                "check": True,
                "witness": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["verdict"], "FAIL")
        self.assertEqual(body["trust_score"], 75.0)
        self.assertEqual(body["charged_as"], "FALSE_COMPLETION")
        self.assertTrue(body["inflamed"])
        self.assertEqual(len(body["chain_hash"]), 64)

    def test_verify_accepts_the_core_vocabulary_agent_key(self) -> None:
        response = self.client.post(
            "/api/v1/verify", json=grounded(agent="legacy_agent")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agent_id"], "legacy_agent")

    def test_verify_rejects_missing_agent(self) -> None:
        response = self.client.post(
            "/api/v1/verify", json=grounded()
        )
        self.assertEqual(response.status_code, 400)

    def test_verify_rejects_a_non_boolean_check(self) -> None:
        response = self.client.post(
            "/api/v1/verify",
            json={"agent_id": "a", "intent": "x", "check": "yes", "witness": False},
        )
        self.assertEqual(response.status_code, 422)

    def test_omitted_witness_is_treated_as_no_witness(self) -> None:
        response = self.client.post(
            "/api/v1/verify", json={"agent_id": "a", "intent": "x", "check": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "INVALID")

    def test_locked_agent_gets_423_not_500(self) -> None:
        payload = {"agent_id": "liar", "intent": "x", "check": True, "witness": False}
        for _ in range(3):
            self.client.post("/api/v1/verify", json=payload)

        response = self.client.post("/api/v1/verify", json=payload)
        self.assertEqual(response.status_code, 423)
        body = response.json()
        self.assertEqual(body["verdict"], "REFUSED")
        self.assertTrue(body["locked"])

    # -- stats / ledger --------------------------------------------------

    def test_stats_shape_and_arithmetic(self) -> None:
        self.client.post(
            "/api/v1/verify",
            json={"agent_id": "a", "intent": "x", "check": True, "witness": False},
        )
        self.client.post(
            "/api/v1/verify",
            json=grounded(agent_id="b", intent="y"),
        )
        stats = self.client.get("/api/v1/stats").json()

        self.assertEqual(stats["intercepted_false_completions"], 1)
        self.assertEqual(stats["avg_trust_score"], 87.5)
        self.assertEqual(stats["inflamed_agents"], 1)
        self.assertEqual(stats["agents_total"], 2)
        self.assertEqual(stats["total_entries"], 2)
        self.assertEqual(stats["verdicts"], {"pass": 1, "fail": 1, "invalid": 0})
        self.assertEqual(len(stats["agents"]), 2)

    def test_ledger_is_ordered_newest_last(self) -> None:
        for i in range(4):
            self.client.post(
                "/api/v1/verify",
                json=grounded(agent_id="a", intent=f"t{i}"),
            )
        ledger = self.client.get("/api/v1/ledger?limit=3").json()

        self.assertEqual(ledger["count"], 3)
        self.assertTrue(ledger["chain_ok"])
        seqs = [e["seq"] for e in ledger["entries"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(seqs[-1], 4)

    def test_ledger_verify_endpoint(self) -> None:
        self.client.post(
            "/api/v1/verify",
            json=grounded(agent_id="a"),
        )
        body = self.client.get("/api/v1/ledger/verify").json()
        self.assertTrue(body["chain_ok"])
        self.assertEqual(body["total_entries"], 1)

    def test_ledger_limit_is_validated(self) -> None:
        self.assertEqual(self.client.get("/api/v1/ledger?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/ledger?limit=99999").status_code, 422)

    # -- unlock ----------------------------------------------------------

    def test_unlock_flow(self) -> None:
        payload = {"agent_id": "liar", "intent": "x", "check": True, "witness": False}
        for _ in range(3):
            self.client.post("/api/v1/verify", json=payload)

        response = self.client.post(
            "/api/v1/agents/liar/unlock", json={"reason": "operator reviewed"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["locked"])

        retry = self.client.post("/api/v1/verify", json=payload)
        self.assertEqual(retry.status_code, 200)

    def test_unlock_of_an_unlocked_agent_is_a_conflict(self) -> None:
        self.client.post(
            "/api/v1/verify",
            json=grounded(agent_id="ok_agent"),
        )
        response = self.client.post("/api/v1/agents/ok_agent/unlock", json={})
        self.assertEqual(response.status_code, 409)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
def _six_agents(client, tokens: str) -> None:
    """Book six verified passes and declare one lineage token each.

    ``tokens`` is a format string: ``"prompt:v{0}"`` gives every agent a distinct
    lineage (independent), ``"prompt:shared"`` gives all six the same one
    (clones). Written once because the engine tests and the render tests must be
    driving the *same* fleet -- two copies drifting apart would let the HTTP
    tests pass while the screen rendered something else.
    """
    for i in range(6):
        name = "agent-%d" % i
        client.post("/api/v1/verify", json=grounded(agent_id=name))
        body = {"agent_id": name, "tokens": [tokens.format(i)]}
        assert client.post("/api/v1/lineage", json=body).status_code == 200


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class IndependenceApiTests(unittest.TestCase):
    """D5 must be in the *response*, not just in the engine.

    A statistic the endpoint does not return is a statistic nobody can act on,
    so these tests go through the wire format rather than calling ``stats()``.
    """

    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    def test_stats_exposes_both_counts(self) -> None:
        _six_agents(self.client, "prompt:shared")
        stats = self.client.get("/api/v1/stats").json()
        self.assertEqual(stats["n_raw"], 6)
        self.assertEqual(stats["n_effective"], 1.0)
        self.assertEqual(stats["independence"]["status"], "declared")

    def test_six_independent_agents_are_worth_six(self) -> None:
        _six_agents(self.client, "prompt:v{0}")
        stats = self.client.get("/api/v1/stats").json()
        self.assertEqual(stats["n_raw"], 6)
        self.assertEqual(stats["n_effective"], 6.0)

    def test_the_fields_exist_before_anyone_declares_anything(self) -> None:
        """The field must be present and null, not missing: a missing key and a
        null read the same to a human and differently to a client."""
        for i in range(3):
            self.client.post("/api/v1/verify", json=grounded(agent_id="g%d" % i))
        stats = self.client.get("/api/v1/stats").json()
        self.assertIn("n_raw", stats)
        self.assertIn("n_effective", stats)
        self.assertEqual(stats["n_raw"], 3)
        self.assertIsNone(stats["n_effective"])
        self.assertEqual(stats["independence"]["status"], "uncalibrated")

    def test_lineage_endpoint_reports_the_new_counts(self) -> None:
        for i in range(2):
            self.client.post("/api/v1/verify", json=grounded(agent_id="d%d" % i))
        first = self.client.post(
            "/api/v1/lineage", json={"agent_id": "d0", "tokens": ["p"]}
        )
        self.assertIsNone(first.json()["n_effective"])  # d1 still silent
        second = self.client.post(
            "/api/v1/lineage", json={"agent_id": "d1", "tokens": ["p"]}
        )
        self.assertEqual(second.json()["n_effective"], 1.0)

    def test_an_empty_declaration_is_a_400(self) -> None:
        self.client.post("/api/v1/verify", json=grounded(agent_id="e0"))
        for tokens in ([], ["   "]):
            with self.subTest(tokens=tokens):
                response = self.client.post(
                    "/api/v1/lineage", json={"agent_id": "e0", "tokens": tokens}
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["declared"])

    def test_an_empty_agent_id_is_refused(self) -> None:
        response = self.client.post("/api/v1/lineage", json={"tokens": ["p"]})
        self.assertEqual(response.status_code, 400)


#: The disclaimer that must appear both as a tooltip and as visible small print.
#: Module-level so the render tests and the static-page tests cannot disagree
#: about it -- which is the drift this string is most likely to suffer.
INDEPENDENCE_TOOLTIP = (
    "This is a dashboard metric, not an enforcement gate. "
    "No request is rejected based on this value."
)

#: Executing the renderer needs node; the static page tests do not.
HAVE_NODE = shutil.which("node") is not None


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
@unittest.skipUnless(HAVE_NODE, "node is required to execute the renderer")
class IndependenceRenderTests(unittest.TestCase):
    """The D5 line on the dashboard, rendered -- not pattern-matched.

    ``tests/render_probe.js`` loads the real ``static/app.js`` into a ``vm``
    context and calls ``independenceText`` on a payload shaped exactly like
    ``stats.independence``. Searching the source for a literal would pass on a
    branch nothing calls; running the function cannot.

    The end-to-end cases below feed the payload that ``GET /api/v1/stats``
    actually returned, so the probe cannot drift from the wire format either.
    """

    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    def _render(self, independence):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "render_probe.js"), json.dumps(independence)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            proc.returncode, 0,
            "render probe failed: {}\n{}".format(proc.returncode, proc.stderr),
        )
        return json.loads(proc.stdout)

    def test_six_clones_render_as_one_of_six(self) -> None:
        _six_agents(self.client, "prompt:shared")
        stats = self.client.get("/api/v1/stats").json()
        view = self._render(stats["independence"])
        self.assertEqual(view["text"], "1.0 / 6 (ρ=1.00)")
        self.assertEqual(view["tone"], "calibrated")

    def test_six_independent_agents_render_as_six_of_six(self) -> None:
        _six_agents(self.client, "prompt:v{0}")
        stats = self.client.get("/api/v1/stats").json()
        view = self._render(stats["independence"])
        self.assertEqual(view["text"], "6.0 / 6 (ρ=0.00)")
        self.assertEqual(view["tone"], "calibrated")

    def test_null_renders_as_uncalibrated_not_as_a_blank(self) -> None:
        """The case that matters: no number must not become a number.

        An em-dash or a blank would let a reader supply the headcount, which is
        the six-fold overstatement D5 exists to name. The word has to be there.
        """
        for i in range(3):
            self.client.post("/api/v1/verify", json=grounded(agent_id="s%d" % i))
        stats = self.client.get("/api/v1/stats").json()
        self.assertIsNone(stats["n_effective"])
        view = self._render(stats["independence"])
        self.assertEqual(view["text"], "n_effective: uncalibrated")
        self.assertEqual(view["tone"], "uncalibrated")
        self.assertNotIn("3", view["text"])

    def test_a_partly_declared_fleet_also_renders_uncalibrated(self) -> None:
        _six_agents(self.client, "prompt:shared")
        # A seventh agent that never declared a lineage makes the fleet partial.
        self.client.post("/api/v1/verify", json=grounded(agent_id="silent"))
        stats = self.client.get("/api/v1/stats").json()
        self.assertIsNone(stats["n_effective"])
        view = self._render(stats["independence"])
        self.assertEqual(view["text"], "n_effective: uncalibrated")
        self.assertIn("部分", view["detail"])

    def test_an_empty_fleet_renders_a_dash_not_a_zero(self) -> None:
        # "0.0 / 0" would read as a measured result about a fleet with no agents.
        view = self._render(self.client.get("/api/v1/stats").json()["independence"])
        self.assertEqual(view["text"], "—")
        self.assertEqual(view["tone"], "unknown")

    def test_the_render_is_not_an_alarm(self) -> None:
        """It reports; it does not reject. So it must not look like a rejection.

        Checked on the rendered strings rather than the source: the values an
        operator can read are the ones that could mislead.
        """
        _six_agents(self.client, "prompt:shared")
        calibrated = self._render(
            self.client.get("/api/v1/stats").json()["independence"]
        )
        _reset()
        self.client = TestClient(app)
        self.client.post("/api/v1/verify", json=grounded(agent_id="solo"))
        uncalibrated = self._render(
            self.client.get("/api/v1/stats").json()["independence"]
        )
        banned = ("已隔离", "已拒绝", "已拦截", "阻断", "警告", "ALERT", "BLOCKED")
        for view in (calibrated, uncalibrated):
            for word in banned:
                with self.subTest(word=word, text=view["text"]):
                    self.assertNotIn(word, view["text"] + view["detail"])

    def test_the_tooltip_is_the_required_sentence(self) -> None:
        view = self._render({"n_raw": 6, "n_effective": 1.0, "rho": 1.0})
        self.assertEqual(view["tooltip"], INDEPENDENCE_TOOLTIP)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class IndependencePresentationTests(unittest.TestCase):
    """The static half: what the page and the stylesheet declare.

    Split from the render tests so a machine without node still checks that the
    row exists and that its styling is neutral -- the two failures a restyle is
    actually likely to cause.
    """

    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    def test_the_visible_note_and_the_tooltip_say_the_same_thing(self) -> None:
        """Two places carry the disclaimer; a restyle must not edit one of them."""
        page = self.client.get("/").text
        self.assertIn(INDEPENDENCE_TOOLTIP, page)
        self.assertEqual(page.count(INDEPENDENCE_TOOLTIP), 2,
                         "the tooltip and the visible note have drifted apart")

    def test_the_page_carries_the_row_and_its_label(self) -> None:
        page = self.client.get("/").text
        self.assertIn('id="independence-row"', page)
        self.assertIn("有效审计人数（基于谱系声明）", page)

    def test_the_independence_styles_use_no_alarm_colour(self) -> None:
        """A restyle is where "just make it stand out" quietly becomes a lie.

        The rules are read out of ``style.css`` and checked for the alert
        palette, so turning this row red fails a test instead of shipping.
        """
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        start = css.index("independence (D5)")
        end = css.index(".agents-list {", start)
        block = css[start:end]
        for token in ("--red", "#ff3b57", "--amber", "#ffb020", "--red-dim",
                      "#8c1d2b", "alert", "danger", "warning"):
            with self.subTest(token=token):
                self.assertNotIn(token, block)
        # And it does use the two neutral tones it claims to.
        self.assertIn("var(--slate)", block)
        self.assertIn("var(--text-faint)", block)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class BindTests(unittest.TestCase):
    """The port a host injects wins over the default, and a bad value is fatal."""

    def setUp(self) -> None:
        import os

        self._saved = {k: os.environ.get(k) for k in ("CEL_PORT", "PORT", "CEL_HOST", "HOST")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        import os

        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_is_all_interfaces_8000(self) -> None:
        from api_server import resolve_bind

        self.assertEqual(resolve_bind(), ("0.0.0.0", 8000))

    def test_platform_port_and_host_are_honoured(self) -> None:
        import os

        from api_server import resolve_bind

        os.environ["PORT"] = "10000"
        os.environ["HOST"] = "0.0.0.0"
        self.assertEqual(resolve_bind(), ("0.0.0.0", 10000))

    def test_cel_port_wins_over_platform_port(self) -> None:
        import os

        from api_server import resolve_bind

        os.environ["PORT"] = "10000"
        os.environ["CEL_PORT"] = "8123"
        self.assertEqual(resolve_bind()[1], 8123)

    def test_a_malformed_port_refuses_to_start(self) -> None:
        import os

        from api_server import resolve_bind

        os.environ["CEL_PORT"] = "http"
        with self.assertRaises(SystemExit):
            resolve_bind()

    def test_out_of_range_port_refuses_to_start(self) -> None:
        import os

        from api_server import resolve_bind

        os.environ["PORT"] = "99999"
        with self.assertRaises(SystemExit):
            resolve_bind()


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class AuthTests(unittest.TestCase):
    """The key gate: it must let a writer in, keep a stranger out, and do it
    *before* anything is booked.

    Every key here is fake. A test that ships a real credential is a credential
    in a public repository -- and the point of the feature is that secrets arrive
    through the environment, not through source.
    """

    KEY = "test-key-not-a-real-secret-0123456789"

    def setUp(self) -> None:
        self._saved = os.environ.get("CEL_API_KEY")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("CEL_API_KEY", None)
        else:
            os.environ["CEL_API_KEY"] = self._saved
        api_server.configure_auth()

    def _body(self) -> dict:
        return {
            "agent_id": "robot_arm_01",
            "intent": "write to database",
            "check": True,
            "witness": False,
        }

    # -- no key configured: the local demo path stays open ------------------

    def test_unset_key_leaves_writes_open(self) -> None:
        _reset()
        response = self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "FAIL")

    def test_stats_says_so_when_auth_is_off(self) -> None:
        _reset()
        self.assertFalse(self.client.get("/api/v1/stats").json()["auth_enabled"])

    def test_a_few_whitespace_chars_do_not_count_as_a_key(self) -> None:
        """``CEL_API_KEY="  "`` must not read as "configured".

        A key that is nothing but whitespace would otherwise enable the gate and
        then reject every writer, which looks exactly like a broken client.
        """
        _reset()
        os.environ["CEL_API_KEY"] = "   "
        api_server.configure_auth()
        self.assertFalse(AUTH.enabled)
        self.assertEqual(self.client.post("/api/v1/verify", json=self._body()).status_code, 200)

    # -- key configured: the gate ------------------------------------------

    def test_correct_key_is_accepted(self) -> None:
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": self.KEY}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "FAIL")

    def test_wrong_key_is_403(self) -> None:
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": "wrong-key"}
        )
        self.assertEqual(response.status_code, 403)

    def test_missing_header_is_the_anonymous_trial_not_a_403(self) -> None:
        """No credential is not a rejected credential.

        The contract changed with the developer-portal funnel: a caller with no
        key at all gets the anonymous trial (judged, unsealed), while a caller
        that presented a *wrong* key still gets 403. Collapsing the two would
        make "I forgot the header" and "I guessed the key" the same event.
        """
        _reset()
        configure_auth(self.KEY)
        response = self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["channel"], "anonymous_trial")
        self.assertFalse(body["sealed"])
        self.assertEqual(body["verdict"], "FAIL")

    def test_empty_header_is_the_anonymous_trial_too(self) -> None:
        """``X-API-Key:`` with no value is a missing credential, not a wrong one.

        Browsers and proxies emit the empty header routinely; treating it as a
        failed guess would 403 a visitor whose only sin was a default header.
        """
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channel"], "anonymous_trial")

    def test_a_prefix_of_the_key_is_403(self) -> None:
        """Off-by-one on the comparison is how a "secret" leaks one char at a time."""
        _reset()
        configure_auth(self.KEY)
        for candidate in (self.KEY[:-1], self.KEY[:8], self.KEY + "x", self.KEY.upper()):
            with self.subTest(candidate=candidate):
                response = self.client.post(
                    "/api/v1/verify", json=self._body(), headers={"X-API-Key": candidate}
                )
                self.assertEqual(response.status_code, 403)

    def test_surrounding_whitespace_is_forgiven(self) -> None:
        """A trailing newline from ``echo $KEY`` must not read as a wrong key."""
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": f"  {self.KEY}\n"}
        )
        self.assertEqual(response.status_code, 200)

    # -- the gate must run before the ledger is touched ---------------------

    def test_a_rejected_write_books_nothing(self) -> None:
        """403 means the claim was never judged, so no entry and no trust lost."""
        _reset()
        configure_auth(self.KEY)
        for _ in range(3):
            self.client.post(
                "/api/v1/verify", json=self._body(), headers={"X-API-Key": "wrong"}
            )
        ledger = self.client.get("/api/v1/ledger").json()
        self.assertEqual(ledger["total_entries"], 0)
        self.assertEqual(self.client.get("/api/v1/stats").json()["avg_trust_score"], 100.0)

    def test_the_403_body_does_not_echo_the_key(self) -> None:
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": self.KEY + "-typo"}
        )
        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertNotIn(self.KEY, detail)
        self.assertIn("X-API-Key", detail)

    # -- reads stay anonymous ---------------------------------------------

    def test_reads_stay_anonymous_when_auth_is_on(self) -> None:
        """The dashboard polls these unauthenticated; it must keep working."""
        _reset()
        configure_auth(self.KEY)
        self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": self.KEY}
        )
        for path in ("/api/v1/stats", "/api/v1/ledger", "/api/v1/ledger/verify", "/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_stats_reports_auth_enabled(self) -> None:
        _reset()
        configure_auth(self.KEY)
        self.assertTrue(self.client.get("/api/v1/stats").json()["auth_enabled"])

    # -- unlock is a write too -------------------------------------------

    def test_unlock_requires_the_key(self) -> None:
        """An open unlock would let anyone void the one penalty that stops an agent."""
        _reset()
        configure_auth(self.KEY)
        payload = self._body()
        for _ in range(3):
            self.client.post(
                "/api/v1/verify", json=payload, headers={"X-API-Key": self.KEY}
            )
        self.assertEqual(
            self.client.post("/api/v1/agents/robot_arm_01/unlock", json={}).status_code,
            403,
        )
        unlocked = self.client.post(
            "/api/v1/agents/robot_arm_01/unlock",
            json={"reason": "operator reviewed"},
            headers={"X-API-Key": self.KEY},
        )
        self.assertEqual(unlocked.status_code, 200)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class FalsificationTests(unittest.TestCase):
    """The a posteriori loop: the server cannot check at request time, so a
    later physical observation is the only thing that can overturn a PASS.

    These tests look at what is *on the chain* afterwards, not just at the
    response body, because the load-bearing claim is that nothing was rewritten.
    """

    KEY = "test-key-not-a-real-secret-0123456789"

    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    def _book_a_commitment(self, agent: str = "honest", trace: str = "") -> dict:
        """Book one PASS with a digest and return the verify response body."""
        response = self.client.post(
            "/api/v1/verify",
            json=grounded(agent_id=agent, trace=trace or "falsify-{}".format(agent)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "PASS")
        return response.json()

    def test_falsify_wipes_trust_and_appends_a_refutation(self) -> None:
        """The headline behaviour: claim is cheap, refutation is terminal."""
        booked = self._book_a_commitment()
        self.assertEqual(booked["trust_score"], 100.0)
        self.assertIsNone(booked["grounded"])
        before = self.client.get("/api/v1/ledger").json()
        self.assertEqual(before["total_entries"], 1)
        self.assertFalse(before["entries"][0]["refuted"])

        response = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "file_not_found",
                "evidence": "ls on the share returned ENOENT; no vial file exists",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["verdict"], "REFUTED")
        self.assertTrue(body["refuted"])
        self.assertEqual(body["trust_score"], 0.0)
        self.assertEqual(body["charged_as"], "FALSIFIED_COMMITMENT")
        self.assertEqual(body["penalty_state"], "CRITICAL")
        self.assertEqual(body["refutes_seq"], booked["seq"])

        # A refutation is an appended entry, not an edit.
        after = self.client.get("/api/v1/ledger").json()
        self.assertEqual(after["total_entries"], 2)
        self.assertEqual(after["refuted_commitments"], 1)
        self.assertTrue(after["chain_ok"], "appending must not break the chain")

        original = [e for e in after["entries"] if e["seq"] == booked["seq"]][0]
        self.assertEqual(original["verdict"], "PASS", "history is not rewritten")
        self.assertTrue(original["refuted"])
        self.assertEqual(original["refuted_reason"], "file_not_found")
        self.assertEqual(original["hash"], booked["chain_hash"])

        refutation = [e for e in after["entries"] if e["verdict"] == "REFUTED"][0]
        self.assertEqual(refutation["refutes_seq"], booked["seq"])
        self.assertEqual(refutation["refutes_hash"], booked["chain_hash"])
        # The refutation is the finding, not another refuted commitment. Marking
        # it would claim the finding is also a finding against itself, and made
        # the dashboard count every refutation twice.
        self.assertFalse(refutation["refuted"])
        self.assertNotIn("refuted_at_seq", refutation)
        self.assertEqual(
            [e["seq"] for e in after["entries"] if e["refuted"]], [booked["seq"]]
        )

    def test_falsify_requires_evidence(self) -> None:
        """An empty refutation is an assertion, and the record has to audit it."""
        booked = self._book_a_commitment()
        response = self.client.post(
            "/api/v1/falsify",
            json={"trace_id": booked["trace_id"], "reason": "file_not_found"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            self.client.get("/api/v1/ledger").json()["total_entries"],
            1,
            "a refused refutation must not book anything",
        )

    def test_falsify_unknown_trace_is_404(self) -> None:
        response = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": "never-happened",
                "reason": "file_not_found",
                "evidence": "checked",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_falsify_twice_is_409(self) -> None:
        """A second observer with no new evidence adds nothing and must not stack."""
        booked = self._book_a_commitment()
        payload = {
            "trace_id": booked["trace_id"],
            "reason": "file_not_found",
            "evidence": "no file",
        }
        self.assertEqual(
            self.client.post("/api/v1/falsify", json=payload).status_code, 200
        )
        second = self.client.post("/api/v1/falsify", json=payload)
        self.assertEqual(second.status_code, 409)
        self.assertFalse(second.json()["ledger_written"])

    def test_hash_mismatch_requires_a_different_observed_hash(self) -> None:
        booked = self._book_a_commitment()
        # Missing observed_hash: the finding is incomplete.
        missing = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "hash_mismatch",
                "evidence": "recomputed from the real file",
            },
        )
        self.assertEqual(missing.status_code, 422)

        # Same digest as committed: that confirms, it does not refute.
        same = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "hash_mismatch",
                "evidence": "recomputed from the real file",
                "observed_hash": TEST_HASH,
            },
        )
        self.assertEqual(same.status_code, 422)

        # A genuinely different digest refutes.
        different = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "hash_mismatch",
                "evidence": "recomputed from the real file",
                "observed_hash": "sha256:" + "b" * 64,
            },
        )
        self.assertEqual(different.status_code, 200)
        self.assertEqual(different.json()["trust_score"], 0.0)

    def test_falsify_requires_the_key(self) -> None:
        """An open falsify would be a one-request way to ruin any agent's record."""
        _reset()
        configure_auth(self.KEY)
        booked = self.client.post(
            "/api/v1/verify",
            json=grounded(agent_id="victim", trace="falsify-victim"),
            headers={"X-API-Key": self.KEY},
        ).json()
        response = self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "file_not_found",
                "evidence": "anonymous accusation",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get("/api/v1/ledger").json()["total_entries"], 1
        )

    def test_refuted_agent_cannot_recover_by_passing_later(self) -> None:
        """R2: the upslope out of a refutation is a wall, not a staircase."""
        booked = self._book_a_commitment(agent="liar")
        self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "file_not_found",
                "evidence": "the vial does not exist",
            },
        )
        for i in range(5):
            later = self.client.post(
                "/api/v1/verify",
                json=grounded(agent_id="liar", trace="liar-after-{}".format(i)),
            )
            self.assertEqual(later.status_code, 200)
            self.assertEqual(later.json()["verdict"], "PASS")
            self.assertEqual(
                later.json()["trust_score"],
                0.0,
                "clean passes must not buy back refuted trust",
            )

    def test_stats_survive_a_refutation(self) -> None:
        """The headline count stays a count of PASSes; refutations are reported."""
        booked = self._book_a_commitment()
        self.client.post(
            "/api/v1/falsify",
            json={
                "trace_id": booked["trace_id"],
                "reason": "file_not_found",
                "evidence": "gone",
            },
        )
        stats = self.client.get("/api/v1/stats").json()
        self.assertEqual(stats["verified_passes"], 1)
        self.assertEqual(stats["refuted_commitments"], 1)
        self.assertEqual(stats["standing_passes"], 0)
        self.assertEqual(stats["avg_trust_score"], 0.0)
        agent = [a for a in stats["agents"] if a["agent_id"] == "honest"][0]
        self.assertEqual(agent["falsified"], 1)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class RateLimitTests(unittest.TestCase):
    """The write budget: ``CEL_RATE_LIMIT`` writes per IP per window.

    The point of these tests is not that a counter counts. It is that the
    limiter cannot be sidestepped with a header, that it never applies to the
    reads the console polls, and that a rejected request books nothing -- a 429
    that still wrote a ledger row would be a request that was refused and
    charged at once.
    """

    LIMIT = 5

    def setUp(self) -> None:
        GATEWAY.__init__(fail_limit=GATEWAY.fail_limit, ledger_log=None)
        configure_auth("")
        os.environ["CEL_RATE_LIMIT"] = str(self.LIMIT)
        os.environ["CEL_RATE_WINDOW"] = "60"
        os.environ.pop("CEL_TRUST_PROXY", None)
        api_server.configure_rate_limit()
        api_server.reset_trial()
        api_server.configure_anon_limit()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for name in ("CEL_RATE_LIMIT", "CEL_RATE_WINDOW", "CEL_TRUST_PROXY",
                     "CEL_ANON_LIMIT", "CEL_ANON_WINDOW"):
            os.environ.pop(name, None)
        api_server.configure_rate_limit()
        api_server.configure_anon_limit()

    # -- helpers ---------------------------------------------------------

    def _write(self, client=None, trace: str = "", headers=None, **overrides):
        client = client or self.client
        overrides.setdefault("agent_id", "rate_limited_agent")
        return client.post(
            "/api/v1/verify",
            json=grounded(trace=trace, **overrides),
            headers=headers or {},
        )

    def _spend(self, client=None, n: int = None) -> None:
        for i in range(self.LIMIT if n is None else n):
            self.assertEqual(self._write(client, trace="budget-{}".format(i)).status_code, 200)

    # -- the limit ------------------------------------------------------

    def test_the_sixth_write_in_a_window_is_429(self) -> None:
        self._spend()
        response = self._write(trace="over-budget")
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertIn("5 per 60 seconds", body["detail"])
        retry = response.headers.get("Retry-After")
        self.assertIsNotNone(retry, "429 without Retry-After is a dead end for a client")
        self.assertTrue(1 <= int(retry) <= 60, retry)

    def test_a_refused_write_books_nothing(self) -> None:
        """A 429 must be a request that did not happen, not a charged one."""
        self._spend()
        before = self.client.get("/api/v1/stats").json()["total_entries"]
        self.assertEqual(self._write(trace="over-budget").status_code, 429)
        after = self.client.get("/api/v1/stats").json()["total_entries"]
        self.assertEqual(before, after)
        traces = [e.get("trace_id") for e in self.client.get("/api/v1/ledger").json()["entries"]]
        self.assertNotIn("over-budget", traces)

    def test_reads_are_never_limited(self) -> None:
        """The dashboard polls /stats every 2s; limiting that would blank it."""
        self._spend()
        self.assertEqual(self._write(trace="over-budget").status_code, 429)
        for path in ("/api/v1/stats", "/api/v1/ledger", "/api/v1/ledger/verify", "/", "/healthz"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_the_budget_is_per_client_address(self) -> None:
        first = TestClient(app, client=("10.0.0.1", 50000))
        second = TestClient(app, client=("10.0.0.2", 50000))
        self._spend(first)
        self.assertEqual(self._write(first, trace="a").status_code, 429)
        self.assertEqual(self._write(second, trace="b").status_code, 200)

    # -- cannot be sidestepped ------------------------------------------

    def test_a_spoofed_forwarded_for_header_does_not_buy_a_budget(self) -> None:
        """``X-Forwarded-For`` is attacker-controlled; trusting it unasked is
        the whole limiter in one header. Off by default, so a fresh fake IP per
        request must still be refused."""
        self._spend()
        response = self._write(
            trace="spoof",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        self.assertEqual(response.status_code, 429)

    def test_a_trusted_proxy_header_is_honoured(self) -> None:
        """Behind a real proxy every request otherwise shares one address, and
        one client would spend everyone's budget.

        The **right-most** entry is the one our proxy appended; the left-most is
        whatever the caller typed, so it must never be the identity. This test
        sends both to pin down which one wins.
        """
        self._spend()
        self.assertEqual(self._write(trace="x").status_code, 429)
        os.environ["CEL_TRUST_PROXY"] = "1"
        try:
            behind_proxy = self._write(
                trace="proxied",
                headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"},
            )
            self.assertEqual(behind_proxy.status_code, 200)
            # Spend the rest of 10.0.0.1's budget, each time with a different
            # spoofed prefix. If the left-most entry were the identity, every one
            # of these would look like a brand new client and none would be
            # refused -- which is exactly how the limiter became a no-op.
            for i in range(self.LIMIT - 1):
                self.assertEqual(
                    self._write(
                        trace="proxied-more-{}".format(i),
                        headers={"X-Forwarded-For": "198.51.100.{} , 10.0.0.1".format(i)},
                    ).status_code,
                    200,
                )
            again = self._write(
                trace="proxied-over",
                headers={"X-Forwarded-For": "198.51.100.99, 10.0.0.1"},
            )
            self.assertEqual(again.status_code, 429, again.text)
        finally:
            os.environ.pop("CEL_TRUST_PROXY", None)

    def test_a_spoofed_prefix_through_a_trusted_proxy_does_not_refill_the_budget(
        self,
    ) -> None:
        """The attack the old left-most read allowed: a fresh fake IP per request.

        Through Cloudflare an ``X-Forwarded-For`` typed by the client arrives as
        ``<typed>, <real client>``. Reading left-most made the typed part the
        identity, so every request looked new and the limit never fired.
        """
        configure_auth("")
        os.environ["CEL_TRUST_PROXY"] = "1"
        try:
            statuses = [
                self._write(
                    trace="spoof-{}".format(i),
                    headers={"X-Forwarded-For": "203.0.113.{}, 10.0.0.1".format(i)},
                ).status_code
                for i in range(self.LIMIT + 1)
            ]
        finally:
            os.environ.pop("CEL_TRUST_PROXY", None)
        self.assertEqual(statuses[: self.LIMIT], [200] * self.LIMIT)
        self.assertEqual(statuses[-1], 429, statuses)

    def test_an_unknown_key_still_spends_the_budget(self) -> None:
        """429 before 403: guessing the key is the cheapest attack here, so the
        budget must be spent before the comparison it protects."""
        configure_auth("the-real-key")
        try:
            statuses = [
                self.client.post(
                    "/api/v1/verify",
                    json=grounded(trace="guess-{}".format(i)),
                    headers={"X-API-Key": "guess-{}".format(i)},
                ).status_code
                for i in range(self.LIMIT + 1)
            ]
        finally:
            configure_auth("")
        self.assertEqual(statuses[: self.LIMIT], [403] * self.LIMIT)
        self.assertEqual(statuses[-1], 429, statuses)

    # -- configuration ---------------------------------------------------

    def test_zero_disables_the_limiter(self) -> None:
        """The documented escape hatch for a load test -- by configuration, and
        announced at boot rather than silent."""
        os.environ["CEL_RATE_LIMIT"] = "0"
        api_server.configure_rate_limit()
        for i in range(self.LIMIT * 3):
            self.assertEqual(self._write(trace="free-{}".format(i)).status_code, 200)

    def test_a_malformed_limit_refuses_to_start(self) -> None:
        """A typo must not silently mean "no limit": that is the failure that
        looks like protection."""
        os.environ["CEL_RATE_LIMIT"] = "five"
        with self.assertRaises(SystemExit):
            api_server.configure_rate_limit()

    def test_a_zero_window_refuses_to_start(self) -> None:
        """limit>0 with window=0 is "no limit" wearing a limit's log line."""
        os.environ["CEL_RATE_WINDOW"] = "0"
        with self.assertRaises(SystemExit):
            api_server.configure_rate_limit()

    def test_stats_reports_the_budget(self) -> None:
        reported = self.client.get("/api/v1/stats").json()["rate_limit"]
        self.assertEqual(reported["limit"], self.LIMIT)
        self.assertEqual(reported["window_seconds"], 60.0)
        self.assertIn("writes only", reported["scope"])


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class RateLimiterWindowTests(unittest.TestCase):
    """The sliding window itself, on a fake clock (no sleeping in the suite).

    A fixed bucket would let a caller spend the budget at the end of one window
    and again at the start of the next; the sliding window is what stops that,
    so the boundary behaviour is worth pinning down.
    """

    def setUp(self) -> None:
        from api_server import RateLimiter

        self.now = 1000.0
        self.limiter = RateLimiter(limit=3, window=60.0, clock=lambda: self.now)

    def test_the_window_slides_rather_than_resetting(self) -> None:
        for _ in range(3):
            self.assertTrue(self.limiter.hit("ip")[0])
        allowed, retry = self.limiter.hit("ip")
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry, 60.0, places=3)

        self.now += 30.0
        self.assertFalse(self.limiter.hit("ip")[0], "half a window is not a window")

        self.now += 30.5  # the first three hits have now aged out
        self.assertTrue(self.limiter.hit("ip")[0])

    def test_a_refused_hit_does_not_extend_the_ban(self) -> None:
        for _ in range(3):
            self.limiter.hit("ip")
        self.now += 50.0
        self.assertFalse(self.limiter.hit("ip")[0])
        self.now += 10.5  # 60.5s after the first hit, 10.5s after the refused one
        self.assertTrue(
            self.limiter.hit("ip")[0],
            "the ban must expire on the original schedule, not the last attempt",
        )

    def test_keys_are_independent(self) -> None:
        for _ in range(3):
            self.limiter.hit("a")
        self.assertFalse(self.limiter.hit("a")[0])
        self.assertTrue(self.limiter.hit("b")[0])


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class AnonymousTrialTests(unittest.TestCase):
    """The developer-portal trial: judged by the same engine, sealed nowhere.

    The security boundary this class exists to hold: an anonymous caller may
    *see a verdict* and may not *change state*. No production chain entry, no
    trust movement, no refutation, no unlock.
    """

    KEY = "trial-suite-key-not-a-real-secret"

    def setUp(self) -> None:
        _reset()
        configure_auth(self.KEY)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        os.environ.pop("CEL_API_KEY", None)
        for name in ("CEL_ANON_LIMIT", "CEL_ANON_WINDOW"):
            os.environ.pop(name, None)
        api_server.configure_auth()
        api_server.configure_anon_limit()

    def _body(self, **overrides) -> dict:
        body = {
            "agent_id": "anonymous_probe",
            "intent": "write to database",
            "check": True,
            "witness": False,
        }
        body.update(overrides)
        return body

    # -- it really judges ---------------------------------------------------

    def test_anonymous_write_returns_a_real_verdict(self) -> None:
        response = self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["verdict"], "FAIL")
        self.assertEqual(body["charged_as"], "FALSE_COMPLETION")
        self.assertEqual(body["channel"], "anonymous_trial")
        self.assertFalse(body["sealed"])
        self.assertFalse(body["ledger_written"])

    def test_the_response_carries_no_chain_coordinates(self) -> None:
        """A trial row must not look like a receipt for a sealed entry.

        ``seq``/``chain_hash`` are the trial ledger's own coordinates and mean
        nothing in the public chain; a screenshot of them would read as proof
        that something was sealed.
        """
        body = self.client.post("/api/v1/verify", json=self._body()).json()
        for key in ("seq", "chain_hash", "prev_hash"):
            self.assertNotIn(key, body, key)
        self.assertIn("not sealed", body["detail"])

    def test_the_production_chain_is_untouched(self) -> None:
        for _ in range(2):
            self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(self.client.get("/api/v1/ledger").json()["total_entries"], 0)
        stats = self.client.get("/api/v1/stats").json()
        self.assertEqual(stats["agents_total"], 0)
        self.assertEqual(stats["avg_trust_score"], 100.0)

    def test_the_trial_never_locks_shut(self) -> None:
        """Three FAILs would quarantine an identified agent. The funnel must not
        dead-end on a 423 a visitor cannot clear without a key they do not have.
        """
        os.environ["CEL_ANON_LIMIT"] = "10"
        api_server.configure_anon_limit()
        codes = [
            self.client.post("/api/v1/verify", json=self._body()).status_code
            for _ in range(5)
        ]
        self.assertEqual(codes, [200] * 5, codes)

    # -- it cannot destroy state -------------------------------------------

    def test_anonymous_cannot_falsify(self) -> None:
        response = self.client.post(
            "/api/v1/falsify",
            json={"trace_id": "t", "reason": "file_not_found", "evidence": "x"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("cannot falsify", response.json()["detail"])

    def test_anonymous_cannot_unlock(self) -> None:
        response = self.client.post(
            "/api/v1/agents/some_agent/unlock", json={"reason": "please"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("cannot falsify", response.json()["detail"])

    # -- its budget ---------------------------------------------------------

    def test_the_second_write_in_the_window_is_429_with_the_funnel_cta(self) -> None:
        self.assertEqual(self.client.post("/api/v1/verify", json=self._body()).status_code, 200)
        response = self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(response.status_code, 429)
        detail = response.json()["detail"]
        self.assertIn("cloud trial", detail)
        self.assertIn("install the sidecar locally", detail)
        self.assertEqual(response.headers["X-Trial-Remaining"], "0")

    def test_a_keyed_caller_is_not_held_to_the_trial_budget(self) -> None:
        """The funnel must not throttle the customer it is converting."""
        self.assertEqual(self.client.post("/api/v1/verify", json=self._body()).status_code, 200)
        for i in range(3):
            response = self.client.post(
                "/api/v1/verify",
                json=self._body(agent_id="paying_{}".format(i)),
                headers={"X-API-Key": self.KEY},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["sealed"])

    def test_a_wrong_key_does_not_fall_back_to_the_trial(self) -> None:
        """Downgrading a bad credential would make a gate advisory."""
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": "guess"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("does not fall back", response.json()["detail"])

    def test_zero_disables_the_trial(self) -> None:
        os.environ["CEL_ANON_LIMIT"] = "0"
        api_server.configure_anon_limit()
        response = self.client.post("/api/v1/verify", json=self._body())
        self.assertEqual(response.status_code, 403)
        self.assertIn("trial is disabled", response.json()["detail"])

    # -- the page can read the policy instead of hard-coding it -------------

    def test_stats_publishes_the_policy_and_this_callers_remaining(self) -> None:
        policy = self.client.get("/api/v1/stats").json()["write_policy"]
        anon = policy["anonymous"]
        self.assertTrue(anon["enabled"])
        self.assertEqual(anon["limit"], 1)
        self.assertEqual(anon["window_seconds"], 300.0)
        self.assertEqual(anon["remaining"], 1)
        self.assertFalse(anon["seals"])
        self.assertFalse(anon["can_falsify"])
        self.assertTrue(policy["keyed"]["seals"])

        self.client.post("/api/v1/verify", json=self._body())
        after = self.client.get("/api/v1/stats").json()["write_policy"]["anonymous"]
        self.assertEqual(after["remaining"], 0)
        self.assertGreater(after["retry_after"], 0)

    def test_the_served_page_and_stats_advertise_the_same_install_command(self) -> None:
        """Pin the *agreement*, not the string.

        The install command lives in three places -- the landing page's code
        block, its explanatory note, and ``write_policy.install.pip`` -- and the
        only thing that keeps them honest is that they are edited together. A
        test that asserted today's literal would have to be rewritten on release
        day, which is exactly when a rewrite is most likely to be done in a
        hurry; a test that asserts they *match* passes before and after the
        switch and breaks on the drift.
        """
        install = self.client.get("/api/v1/stats").json()["write_policy"]["install"]
        page = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        block = re.search(r'<code id="install-cmd">(.*?)</code>', page, re.S)
        self.assertIsNotNone(block, "the landing page has no install-cmd block")
        command = block.group(1).strip()

        self.assertEqual(
            command, install["pip"],
            "the page and /api/v1/stats disagree about the install command",
        )
        self.assertIn("harness-audit-sidecar", command)
        self.assertEqual(install["mcp_server"], "harness-audit-server")
        self.assertIn("causal-evolution-paper", install["spec_repo"])

        # Local consistency, no network: a page must not advertise a bare PyPI
        # install while also saying the package is not on PyPI. That contradiction
        # is the pre-release fallback left half-removed.
        if "git+" not in command:
            self.assertNotIn(
                "尚未发布", page,
                "the page advertises a bare pip install but still says 尚未发布",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

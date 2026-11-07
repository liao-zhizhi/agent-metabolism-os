"""HTTP-level tests, including the one that matters: does the page render.

Requires ``fastapi``'s test client, which needs ``httpx``. Skipped -- not
failed -- when the web stack is not installed, so the engine suite still runs
on a bare interpreter.

    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import re
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
    """
    GATEWAY.__init__(fail_limit=GATEWAY.fail_limit, ledger_log=None)
    configure_auth("")


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

    def test_missing_header_is_403(self) -> None:
        _reset()
        configure_auth(self.KEY)
        self.assertEqual(self.client.post("/api/v1/verify", json=self._body()).status_code, 403)

    def test_empty_header_is_403(self) -> None:
        _reset()
        configure_auth(self.KEY)
        response = self.client.post(
            "/api/v1/verify", json=self._body(), headers={"X-API-Key": ""}
        )
        self.assertEqual(response.status_code, 403)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

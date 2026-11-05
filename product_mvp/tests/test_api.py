"""HTTP-level tests, including the one that matters: does the page render.

Requires ``fastapi``'s test client, which needs ``httpx``. Skipped -- not
failed -- when the web stack is not installed, so the engine suite still runs
on a bare interpreter.

    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient

    from api_server import app, GATEWAY

    HAVE_WEB = True
except Exception as exc:  # pragma: no cover - exercised on a bare interpreter
    HAVE_WEB = False
    IMPORT_ERROR = exc


def _reset() -> None:
    """Each test starts from empty books: the gateway is module-level state."""
    GATEWAY.__init__(fail_limit=GATEWAY.fail_limit, ledger_log=None)


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
            "/api/v1/verify",
            json={"agent": "legacy_agent", "intent": "x", "check": True, "witness": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agent_id"], "legacy_agent")

    def test_verify_rejects_missing_agent(self) -> None:
        response = self.client.post(
            "/api/v1/verify", json={"intent": "x", "check": True, "witness": True}
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
            json={"agent_id": "b", "intent": "y", "check": True, "witness": True},
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
                json={"agent_id": "a", "intent": f"t{i}", "check": True, "witness": True},
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
            json={"agent_id": "a", "intent": "x", "check": True, "witness": True},
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
            json={"agent_id": "ok_agent", "intent": "x", "check": True, "witness": True},
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

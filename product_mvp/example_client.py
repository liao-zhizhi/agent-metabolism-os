"""
example_client.py -- what a customer's agent actually writes.

The contract is one boolean and one answer: *"I claim this happened"* plus
*"here is what an inspection that was not me saw."* The gateway decides. Nothing
in this file is trusted by the server, which is the point.

Run the server first::

    cd product_mvp && python api_server.py

then::

    python example_client.py

Requires only ``requests`` (``pip install requests``). The server itself needs
nothing but ``fastapi`` and ``uvicorn``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("example_client.py needs `requests`:  pip install requests")

BASE_URL = os.environ.get("CEL_BASE_URL", "http://localhost:8000")

#: The shared write key. Read from the environment, never written into this
#: file: a client sample that ships a real key is a key that leaks the first time
#: someone pastes the sample into a repository. When the server has no key
#: configured, an absent one is accepted and this stays empty.
API_KEY = os.environ.get("CEL_API_KEY", "")


def write_headers(key: str = "") -> dict:
    """Headers for a write. Omit the key entirely when there is none.

    Sending ``X-API-Key: ""`` would be rejected outright by a server that *does*
    require a key, and the difference between "no key" and "an empty key" is
    exactly the distinction the server makes. So the header is added, not
    blanked.
    """
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    return headers


# ---------------------------------------------------------------------------
# 1. the snippet from the product spec, verbatim in spirit
# ---------------------------------------------------------------------------

def report_false_completion() -> dict:
    """The case the product exists for: the agent claims success, the witness
    does not back it up."""
    response = requests.post(
        f"{BASE_URL}/api/v1/verify",
        json={
            "agent_id": "robot_arm_01",
            "intent": "write to database",
            "check": True,
            "witness": False,  # the honest field the agent cannot fake
        },
        headers=write_headers(API_KEY),  # X-API-Key: the client's real key
        timeout=10,
    )
    return response.json()


def show_rejected_write() -> None:
    """What an unauthenticated write gets. Printable on purpose.

    A key check nobody has watched fail is a key check nobody knows works. This
    sends a deliberately wrong key and prints the 403, so the reader can see the
    difference between "the server refused me" and "the server judged my claim".
    """
    if not API_KEY:
        print("  (skipped: CEL_API_KEY not set in this shell, so there is no "
              "wrong key to try)")
        return

    response = requests.post(
        f"{BASE_URL}/api/v1/verify",
        json={"agent_id": "attacker", "intent": "charge someone else's trust",
              "check": True, "witness": False},
        headers=write_headers(API_KEY + "-wrong"),
        timeout=10,
    )
    print(f"  wrong key  -> HTTP {response.status_code}  {response.json().get('detail', '')[:64]}")

    anonymous = requests.post(
        f"{BASE_URL}/api/v1/verify",
        json={"agent_id": "attacker", "intent": "charge someone else's trust",
              "check": True, "witness": False},
        timeout=10,
    )
    print(f"  no key     -> HTTP {anonymous.status_code}")


# ---------------------------------------------------------------------------
# 2. the four verdicts, so a reader can see the whole rule in one place
# ---------------------------------------------------------------------------

SCENARIOS = [
    (
        "verified work",
        {"agent_id": "robot_arm_01", "intent": "seal the sample vial",
         "check": True, "witness": True, "artefact": "demo://vial/42"},
       "PASS",
    ),
    (
        "honest failure",
        {"agent_id": "robot_arm_01", "intent": "grasp the sample vial",
         "check": False, "witness": False},
       "PASS",
    ),
    (
        "false completion",
        {"agent_id": "robot_arm_01", "intent": "write to database",
         "check": True, "witness": False},
       "FAIL",
    ),
    (
        "under-claim",
        {"agent_id": "nav_stack_02", "intent": "report battery swap",
         "check": False, "witness": True},
       "FAIL",
    ),
    (
        "no witness at all",
        {"agent_id": "nav_stack_02", "intent": "report corridor cleared",
         "check": True, "witness": None},
       "INVALID",
    ),
]


def run_all() -> list:
    """The four-verdict rule, on throw-away agents.

    Each scenario gets its own agent id (with a per-run suffix) so the section is
    repeatable against a server that has already been clicked: an agent that has
    been quarantined would answer 423 to everything, which is correct behaviour
    and a useless demonstration.
    """
    suffix = uuid.uuid4().hex[:6]
    results = []
    for label, payload, expected in SCENARIOS:
        payload = dict(payload, agent_id="{}_{}".format(payload["agent_id"], suffix))
        response = requests.post(
            f"{BASE_URL}/api/v1/verify",
            json=payload,
            headers=write_headers(API_KEY),
            timeout=10,
        )
        body = response.json()

        # 403 is not a verdict: the write never reached the membrane. Saying so
        # is better than printing "got ?", which reads like a gate failure.
        if response.status_code == 403:
            print(f"!! {label:<20} HTTP 403 -- rejected by the key check, not judged. "
                  f"Set CEL_API_KEY to the server's key.")
            results.append((label, expected, "FORBIDDEN", 403, body))
            continue

        # A closed book returns 423: nothing was judged, so there is no verdict.
        verdict = body.get("verdict", "?")
        ok = verdict == expected
        results.append((label, expected, verdict, response.status_code, body))

        mark = "OK " if ok else "!! "
        print(f"{mark}{label:<20} expected {expected:<7} got {verdict:<8} "
              f"trust={body.get('trust_score')}  state={body.get('penalty_state')}")

    return results


# ---------------------------------------------------------------------------
# 3. the two read endpoints a customer's own dashboard would poll
# ---------------------------------------------------------------------------

def show_stats() -> None:
    stats = requests.get(f"{BASE_URL}/api/v1/stats", timeout=10).json()
    print("\n--- /api/v1/stats " + "-" * 48)
    print(f"  auth enabled                  : {stats.get('auth_enabled')}")
    print(f"  intercepted false completions : {stats['intercepted_false_completions']}")
    print(f"  blocked unwitnessed claims    : {stats['blocked_unwitnessed']}")
    print(f"  average trust score           : {stats['avg_trust_score']}")
    print(f"  inflamed / locked agents      : {stats['inflamed_agents']} / {stats['locked_agents']}")
    print(f"  health state                  : {stats['health_state']}")
    print(f"  chain head                    : {stats['chain_head'][:24]}…")


def show_ledger(limit: int = 8) -> None:
    ledger = requests.get(f"{BASE_URL}/api/v1/ledger", params={"limit": limit}, timeout=10).json()
    print("\n--- /api/v1/ledger " + "-" * 47)
    print(f"  chain_ok={ledger['chain_ok']}  entries={ledger['total_entries']}")
    for entry in ledger["entries"]:
        print("  #{:03d} {:<8} {:<22} {:<26} trust={}".format(
            entry["seq"], entry["verdict"], entry["agent_id"],
            entry["intent"][:26], entry["trust_after"],
        ))
        print("        hash={}… prev={}…".format(entry["hash"][:16], entry["prev_hash"][:16]))


def main() -> int:
    print(f"CEL gateway: {BASE_URL}")
    print(f"API key     : {'set (' + str(len(API_KEY)) + ' chars, X-API-Key)' if API_KEY else 'not set (anonymous writes)'}\n")

    print("--- single demonstration call " + "-" * 37)
    first = report_false_completion()
    print(json.dumps(first, indent=2, ensure_ascii=False))
    if first.get("detail"):
        print(
            "\n  note: this is a rejection, not a verdict -- the write never reached\n"
            "  the membrane. Export CEL_API_KEY to the server's key and re-run."
        )
    if first.get("verdict") == "REFUSED":
        print(
            "\n  note: robot_arm_01 is already quarantined on this server (a previous\n"
            "  run, or the dashboard's simulate button, charged it three times). The\n"
            "  refusal is the correct answer: nothing was judged, and the attempt is\n"
            "  still in the ledger. The section below uses fresh agents on purpose."
        )

    print("\n--- the four-verdict rule " + "-" * 41)
    run_all()

    print("\n--- what an unauthenticated write gets " + "-" * 28)
    show_rejected_write()

    show_stats()
    show_ledger()
    print("\ndone. Open " + BASE_URL + " to watch the same entries arrive live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

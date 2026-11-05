# v1.0.5 — The First Consumer, and the Two Things It Found

This is a patch release with no change to the verdict lattice, the charge table, or
the lock rules. The version moves because the membrane now has a **published
consumer**, and building one turned out to be the cheapest audit available: two
assumptions that held inside this repository failed the moment they were exercised
across an HTTP boundary. Both are recorded here rather than quietly fixed.

The consumer is `product_mvp/` in the workspace: a FastAPI service and a dashboard
that turns `record_transaction` into three endpoints — submit a claim, read the
numbers, read the chain. Source and the ten-minute walkthrough live there; the
product ships the same bytes this audit was produced with, and installs nothing for
the core, which stays standard-library only.

## What the consumer found

**1 — `Receipt.seq` is not an event id, and tailing on it loses entries.**

`seq` counts *judged* entries. A refusal (`AgentLocked`) judges nothing, and a manual
`unlock` produces no receipt, so neither advances the counter. A log that polls
"give me entries with `seq` greater than the last one I saw" therefore reuses the
previous number and **silently drops the refusal**. The failure is invisible from
inside: the books are correct, the balance sheet is correct, and the monitor's feed
just stops showing lines.

This one is documented on `Receipt.seq` now rather than fixed, because renumbering
receipts would change a published field to solve a problem that belongs to the
consumer. A chain built on top of the journal needs its own counter; the console
keeps one, and `product_mvp/tests/test_engine.py` pins the property
(`test_seq_is_monotonic_across_refusals_and_unlocks`).

**2 — the wire format reintroduced the coercion the ledger exists to refuse.**

`Transaction` rejects `check=1` and `check="ok"` on purpose — a truthy string that
quietly means "success" is the same class of bug as a witness that is really a hope.
The first HTTP schema in the consumer used `check: bool`, and Pydantic helpfully
accepted `"yes"` and sent `True`. The gate was correct; the serializer in front of it
was not.

The fix is `StrictBool`, and the general statement is worth keeping: **a type
annotation is a policy decision.** Any layer that coerces inputs before they reach
the membrane has to coerce the way the membrane does, or it is the hole.

## The documentation change in this release

- `agent_metabolism/double_entry.py` — `Receipt.seq` now states what it counts, that
  refusals and unlocks do not advance it, and that a consumer tailing on it must not
  treat it as an event id. No behaviour changed.

## Compatibility

No API removed, no verdict changed, no charge changed. Code written against v1.0.4
runs unchanged; the only difference a caller can observe is a docstring and the
version string.

The core suite still passes in full, and the consumer's suite (35 tests: verdicts,
quarantine, unlock, chain tampering, HTTP status semantics, page rendering) passes
against it. The one place the two disagree is deliberate and enforced by tests: the
core says `INVALID` locks a book on a single occurrence, and the API returns `423`
rather than a verdict for a locked agent — because nothing was judged, and inventing
a verdict for an entry that was never booked is the lie this whole package avoids.

# v1.0.7 — The Port Gets a Lock

v1.0.6 put the console in the repository. This release prepares it to be reachable
from the internet, which changes exactly one thing about the product: before this
version, anyone who could reach the port could charge an agent's trust.

Nothing in `agent_metabolism` changed. The verdict lattice, the charge table, the
lock rules and the hash chain are byte-for-byte what v1.0.6 shipped. The core suite
is unchanged at 1122 tests (9 skipped, as before). What changed is `product_mvp/`.

## Write endpoints now check `X-API-Key`

```bash
export CEL_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
cd product_mvp && python api_server.py
```

| Request | Result |
| --- | --- |
| `POST /api/v1/verify` with the right `X-API-Key` | judged, booked |
| `POST /api/v1/verify` with a wrong or missing key | **403**, nothing booked |
| `POST /api/v1/agents/{id}/unlock` with the right key | released |
| `POST /api/v1/agents/{id}/unlock` without it | **403** |
| `GET /api/v1/stats`, `/ledger`, `/ledger/verify`, `/` | anonymous |

The key is compared with `hmac.compare_digest`, not `==`, so the response time does
not depend on how many leading bytes were correct.

`unlock` was not in the original request and is guarded anyway: an unauthenticated
unlock lets anyone void the one penalty that actually stops a misbehaving agent,
which is a worse hole than a forged verdict. "Requires a human" has to mean a
specific human, or it means no one.

## Unset means open, and the open instance says so

An unconfigured `CEL_API_KEY` leaves writes open. That is a deliberate choice — the
demo has to survive a bare `docker compose up` — and it is a bad choice for a
deployment, so it is impossible to make silently:

- startup logs `auth: DISABLED` as a `WARNING` line (and `auth: ENABLED` with the
  key's length when it is set);
- `/api/v1/stats` reports `auth_enabled`;
- the dashboard replaces its key field with a red **鉴权未启用** banner.

A configurable gate whose default state is "off" and invisible is how an open port
gets demoed to the public. This one cannot be mistaken for a protected instance.

## The dashboard asks for the key instead of embedding it

The console's simulate button is an anonymous browser write, so it breaks the moment
the gate closes. The fix is not to inline the key into the page — that hands it to
anyone who views source — but to have a human paste it:

- stored in `sessionStorage`, so it dies with the tab;
- never written into the served HTML;
- a wrong key shows **这颗 key 被服务器拒绝（403）** and *keeps* showing it until a new
  one is entered.

That last part was a bug found by clicking, not by testing: the 2-second stats poll
re-rendered the panel and reset the rejection notice back to "saved" before it could
be read — a stale "all good" over a key that does not work. The notice is now state,
not a one-off DOM write.

## Deployment, written down

`product_mvp/DEPLOYMENT.md` covers Render, Railway and a plain Docker host: the disk
at `/data`, `CEL_LEDGER_LOG=/data/ledger.jsonl`, `CEL_API_KEY`, one replica, TLS, a
copy-paste verification script, a troubleshooting table, key rotation, and a ship
checklist.

Two things in it exist because they were measured, not assumed:

**The health check now follows the port.** The `HEALTHCHECK` was pinned to `8000`
while the entrypoint honours an injected `PORT`. On a platform that injects one,
the container would report unhealthy while logging "Uvicorn running on
0.0.0.0:10000". Verified both ways: `PORT=10000` binds 10000 and goes healthy;
`CEL_PORT` set alongside `PORT` overrides it and breaks platform routing.

**What a restart costs is stated plainly.** The ledger's live state is process
memory; the file on `/data` is append-only evidence, not a database that is reloaded.
After a redeploy, trust resets to 100, counters reset to 0, and the chain starts a
new segment anchored at 64 zeros — appended to the same file. So `seq` restarts at 1
and line 3 can have a lower `seq` than line 2. That is a real limitation, not a
rounding error, and the guide says so instead of implying the ledger resumes.

A boot that cannot write its log now exits with a sentence naming the two likely
causes (wrong path, or `/data` not writable by uid `10001`) instead of a
`PermissionError` traceback from inside `mkdir`.

## Tests

| Suite | Before | After |
| --- | --- | --- |
| core (`agent_metabolism`) | 1122 | 1122 |
| `product_mvp` engine | 23 | 23 |
| `product_mvp` HTTP | 20 | **34** |

The 14 new HTTP tests cover: correct key accepted; wrong key, missing header and
empty header each 403; a prefix, suffix or upper-cased key 403; surrounding
whitespace forgiven; a rejected write books **nothing** (no entry, no lost trust);
the 403 body does not echo the key; reads stay anonymous with the gate on; and
`unlock` requires the key. Both auth states are exercised in the same process via
`configure_auth()`, so the "open" path stays covered too.

Every key in the tests is a fake. A test that ships a real credential is a
credential in a public repository, which is the whole thing this release is about.

## Verified end to end

Built the image and ran it as a stranger would — named volume on `/data`, generated
key, no `CEL_PORT`:

```
auth: ENABLED -- ... require X-API-Key (key from CEL_API_KEY, 43 chars)
auth_enabled          True
anonymous POST /verify 403
keyed     POST /verify 200
/data/ledger.jsonl    1 line, written as uid 10001
health                healthy
```

## Files

```
product_mvp/
├── api_server.py          + ApiKeyAuth, require_api_key, lifespan announcement
├── static/app.js          + key field, sessionStorage, 403 handling
├── static/style.css       + banner and key-row styles
├── templates/index.html   + auth banner, key input
├── example_client.py      + X-API-Key, and a printed 403 demonstration
├── tests/test_api.py      + 14 auth tests
├── DEPLOYMENT.md          new
├── Dockerfile             health check follows PORT/CEL_PORT
├── docker-compose.yml     passes CEL_API_KEY through, image 0.2.0
└── README.md              + auth and deployment sections
```

## Known limits

- One shared key per instance. No per-client tokens, no scopes, no revocation other
  than rotation.
- Still one replica. The ledger is process memory; two instances are two ledgers.
- The file at `/data` is not replayed on startup. See the restart table above.
- The dashboard's key field is a text input, not a login. It keeps the key out of
  the HTML and out of logs; it does not defend against someone with the browser
  already open.

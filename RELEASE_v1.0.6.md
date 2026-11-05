# v1.0.6 — The Console Moves In

v1.0.5 shipped the first consumer of the membrane and left it outside this
repository, in a workspace that has no remote. That produced a release note telling
readers that a `product_mvp/` directory exists, next to a repository where it did
not. This release fixes the dangling reference the honest way: the console is now
**in the repository**, at `product_mvp/`, and the tag contains what the prose
promises.

Nothing in `agent_metabolism` changed. The verdict lattice, the charge table, the
lock rules and the `Receipt.seq` documentation are byte-for-byte what v1.0.5
shipped.

## What moved

```
product_mvp/
├── api_server.py        FastAPI: routes, request models, 423 vs 200
├── cel_engine.py        the membrane, the trust books, the SHA-256 chain
├── example_client.py    one `requests.post` a customer actually writes
├── templates/index.html one page, no template engine, read from disk per request
├── static/              app.js, style.css, favicon -- no build step
├── tests/               43 tests
├── Dockerfile           builds from this repository's root
├── docker-compose.yml   one command, one port
├── DEMO_GUIDE.md        ten minutes, starting from nothing
└── README.md            endpoints, verdict table, deployment, limits
```

Run it:

```
cd product_mvp && docker compose up --build     # http://localhost:8000
```

## The layout assumption that had to die

`cel_engine.py` located the core library by hard-coding `../agent-metabolism-os`.
That is true in exactly one of the two layouts — sibling directories in a workspace,
or a directory inside this repository — and the failure mode is an `ImportError` on
someone else's machine, after every test passed on mine.

It now tries `product_mvp/../agent-metabolism-os`, then `product_mvp/..`, and
accepts `CEL_CORE_PATH` as an override. A `CEL_CORE_PATH` that does not contain
`agent_metabolism/double_entry.py` is a startup error naming the variable, rather
than a missing-module message three imports later.
`tests/test_engine.py::LayoutTests` pins all three cases.

The Dockerfile moved with it: the build context is now this repository's root, and
`CEL_CORE_PATH=/app` points at the vendored `agent_metabolism/`. The image still
installs nothing for the core and still runs as uid 10001.

## Two deployment facts written down rather than solved

`product_mvp/README.md` now carries a deployment section. It leads with the two
things no environment variable fixes, because a checklist that starts with `PORT`
invites the belief that the port was the hard part:

- **the ledger lives in process memory.** Two replicas are two ledgers that have
  never met, so an agent's trust score depends on which instance answered. One
  replica, or externalise the state — and the second option is not a config change.
- **there is no authentication.** Anyone who can reach the port can charge an
  agent's trust. The dashboard has to sit behind something that knows who is
  calling; protecting `/api/*` while leaving `/` open does not work, because the
  page's own JavaScript calls those endpoints anonymously.

The environment variables that do matter are listed after those, including the one
that was a real defect: the container pinned `--port 8000`, and Render, Railway,
Heroku and Fly all inject `PORT`. The classic result is a service that logs
"running" while the platform's health check reports it dead. `resolve_bind()` reads
`CEL_PORT` then `PORT`, and refuses to start on a malformed value instead of
quietly listening somewhere else.

## Verification

- 1122 core tests, 43 console tests, all passing.
- The image builds from the new context, runs as uid 10001, reports `healthy`,
  and appends the chain to a mounted volume.
- `PORT=9123 python api_server.py` listens on 9123 — checked, not assumed.
- The console's own `chain_ok` is recomputed from the chain on every request; a
  single edited `intent` flips it to `false` (`tests/test_engine.py`).

One honest limit travels with the move: `product_mvp` is a consumer, not a
dependency. Installing `agent-metabolism-os` from PyPI will not install the
console, and the console's tests are not part of the core suite's contract.

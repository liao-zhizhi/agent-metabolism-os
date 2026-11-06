# Deploying CEL to a public URL

The primary target is **Hugging Face Spaces** (Docker SDK): free, no credit card,
and it builds the Dockerfile in this directory as-is. Render and Railway are kept
below for the case where you need a persistent disk, which the free Space tier
does not have.

If you only want to click the dashboard on your laptop, stop here and run
`docker compose up --build` from this directory. Everything below is about the
part that changes when a stranger can reach the port.

---

## 0. What you are deploying

| Thing | Value | Why it is not optional |
| --- | --- | --- |
| Containers | **1** | The ledger and every trust score live in this process's memory. Two replicas are two ledgers that disagree, and the dashboard shows whichever one the load balancer picked. |
| Port | **7860** | A Hugging Face Docker Space routes to 7860. The image sets it as a default (`ENV PORT=7860`), not as a hard-coded bind: `resolve_bind()` reads `PORT`, then `CEL_PORT`, then that default. |
| User | **uid 1000** | Spaces run containers as uid 1000. The image creates exactly that user; any other uid invites permission errors on directories the platform owns. |
| Secret | `CEL_API_KEY` | Without it, anyone on the internet can charge an agent's trust. On a **public** Space the code and URL are public, so this is the only thing between the demo and abuse. |
| Disk | **none on the free tier** | Free Spaces have an ephemeral filesystem. Do not promise an audit trail that a restart erases — see §5. |

Three environment variables:

```bash
CEL_API_KEY=<a long random string>        # required on the public internet
CEL_LEDGER_LOG=/data/ledger.jsonl         # optional; only meaningful with persistent storage
CEL_FAIL_LIMIT=3                          # optional; consecutive FAILs before quarantine
```

Generate a key rather than inventing one:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Never put the result in this repository, a Dockerfile, or a compose file. Every
place below passes it as a secret, which is the only reason the value is not in
the image.

---

## 1. Rehearse locally first (2 minutes)

Doing this before touching a platform means that if the deploy misbehaves, you
already know the image is fine and it is a platform setting.

```bash
cd product_mvp
export CEL_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
echo "key: $CEL_API_KEY"      # paste this into the dashboard's key field later
docker compose up --build     # or: docker build -t cel-console:0.3.0 . && docker run -p 8000:7860 ...
```

Open <http://localhost:8000>. The control panel now shows an `X-API-Key` field
and **no** warning banner, which means the gate is on. Paste the key, click
保存, then click 模拟一次假完成 — it should return a `FAIL` verdict.

If you leave `CEL_API_KEY` unset, the panel shows a red **鉴权未启用** banner
instead of the key field. That is the honest state of an unprotected instance;
it is not a bug, and it is not something to ship.

---

## 2. Hugging Face Spaces (the free path)

### 2.1 What the Space repo must contain

A Space is its own git repository, and it is built with **the repo root as the
Docker build context** — there is no "use the parent directory" option. So the
Space repo is the *contents* of `product_mvp/`, with `README.md` and `Dockerfile`
at the root:

```
<space repo>/
├── README.md            ← YAML front matter at the very top: this is what makes it a Space
├── Dockerfile           ← listens on 7860 via ENV PORT
├── api_server.py
├── cel_engine.py
├── requirements.txt
├── templates/ static/
└── tests/ assets/ ...   ← harmless, ignored by the build (see .dockerignore)
```

The core library is **not** copied in: the Dockerfile fetches the pinned release
tarball (`ARG CEL_CORE_REF=v1.0.9`) and unpacks it to `/app/core`, which
`CEL_CORE_PATH` points at. One Dockerfile works locally and on the Space, and the
bytes are the bytes of the tag.

### 2.2 Create the Space (web UI)

1. Sign in at <https://huggingface.co> and open <https://huggingface.co/new-space>.
2. **Space name**: `cel-dashboard` (the URL becomes
   `https://<your-username>-cel-dashboard.hf.space`).
3. **License**: pick one, or leave it blank for a demo.
4. **Select the Space SDK**: **Docker** → **Blank**.
5. **Space hardware**: **CPU basic · 2 vCPU · 16 GB · FREE**.
6. **Visibility**: **Public** works and is free. Choose **Private** if you do not
   want the code or the dashboard public (the URL is then only reachable with
   your account).
7. **Create Space**.

### 2.3 Upload the code

Three ways. Use whichever matches how you work; all end with the same repo.

**A. Web upload (no git needed)**

In the Space, open the **Files** tab → **Add file** → **Upload files**, then drag
in the **contents** of `product_mvp/` — not the folder itself, or everything ends
up nested one level down and the build cannot find `Dockerfile`.

> Do **not** upload `.venv/`, `__pycache__/`. The `.venv` alone is tens of MB and
> will make the upload crawl. `.gitignore` covers git-based uploads, but a
> drag-and-drop ignores it.

Uploading the same `README.md` that has the YAML block replaces the template one —
that is what you want. Then **Commit changes**.

**B. git push (recommended for repeat deploys)**

HF is a normal git remote; the password is a Hugging Face access token
(<https://huggingface.co/settings/tokens>, role **write**):

```bash
# 1. clone the empty-ish Space repo
git clone https://huggingface.co/spaces/<your-username>/cel-dashboard
cd cel-dashboard

# 2. copy this directory's contents in (README + Dockerfile included)
rsync -av --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  /path/to/agent-metabolism-os/product_mvp/ ./ 

# 3. push (username = your HF username, password = the token)
git add -A
git commit -m "CEL console: docker space on 7860"
git push
```

**C. `hf` CLI**

```bash
pip install -U huggingface_hub
hf auth login                                   # paste a write token
hf upload <your-username>/cel-dashboard /path/to/agent-metabolism-os/product_mvp . \
  --exclude ".venv/*" --exclude "**/__pycache__/*"
```

A push triggers a rebuild; watch the **Build logs** tab (2–4 minutes).

### 2.4 Set the secret (do this before sharing the URL)

Space → **Settings** → **Variables and secrets** → **New secret**:

| Name | Value | Type |
| --- | --- | --- |
| `CEL_API_KEY` | the string from `secrets.token_urlsafe(32)` | **Secret** |

Use **Secret**, not Variable. Variables are injected as Docker **build args** and
are readable in the Space settings/build logs; a write credential does not belong
in either. Secrets are exposed to the running container as environment variables,
which is exactly what `api_server.py` reads.

Optional **Variable** (not sensitive): `CEL_FAIL_LIMIT = 3`.

Changing a secret restarts the Space — expected, and it costs the in-memory
ledger (see §5).

### 2.5 Confirm it is live

The Space URL is `https://<your-username>-<space-name>.hf.space`. First load can
take 30–60 s after a build or a wake-up; a 502 in that window is the container
starting, not a failure.

Then run the verification in §4. `auth_enabled` must be `true`.

### 2.6 Free-tier facts you should not discover later

- **The filesystem is ephemeral.** Anything written at runtime — including
  `CEL_LEDGER_LOG` — is gone after a restart, a rebuild, or a wake-up from sleep.
  Live trust scores are process memory anyway, so the dashboard also resets to a
  clean slate.
- **A free Space sleeps** after a period of inactivity (currently 48 h) and wakes
  on the next request, with a cold start.
- **Public means public.** A public Space exposes the source and the dashboard
  URL to anyone. The write endpoints are keyed; `GET /api/v1/stats`,
  `/api/v1/ledger`, `/api/v1/ledger/verify` and the page are anonymous by design.
- **Persistent storage is not free.** Spaces offer Storage Buckets on paid
  hardware. If you attach one at `/data`, then `CEL_LEDGER_LOG=/data/ledger.jsonl`
  keeps the *file* across restarts — but still not the live trust state, because
  this version does not replay the log on startup (§5).

For a demo this is fine. For a system whose audit trail matters, use Railway or a
VPS (§3) where a real disk is available.

---

## 3. If you need a persistent disk

### Railway

1. **New Project → Deploy from GitHub repo** (the repository, not the Space).
2. Service **Settings → Build**: Dockerfile path `product_mvp/Dockerfile`, and set
   **Root Directory** to `product_mvp` so the build context matches what the
   Dockerfile expects.
3. **Variables**: `CEL_API_KEY=<generated>`, `CEL_LEDGER_LOG=/data/ledger.jsonl`,
   `CEL_FAIL_LIMIT=3`.
4. **Volumes → Add Volume**, mount path `/data`.
5. **Settings → Scale**: replicas **1**.
6. **Settings → Networking → Generate Domain**.

Railway injects `PORT`, which the entrypoint honours over the image's 7860.

### Any Docker host

```bash
cd product_mvp
docker build -t cel-console:0.3.0 .
docker run -d --name cel \
  -p 8000:7860 \
  -v cel-data:/data \
  -e CEL_LEDGER_LOG=/data/ledger.jsonl \
  -e CEL_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --restart unless-stopped \
  cel-console:0.3.0
```

The container runs as uid `1000` (`USER cel`). A named volume inherits the
ownership of the image's `/data` directory, so this works. A **bind mount**
(`-v /srv/cel:/data`) keeps the host's ownership: `chown 1000:1000 /srv/cel` first.
If the directory ends up unwritable, the container refuses to boot and names both
causes rather than failing on the first write.

Put a TLS-terminating proxy in front. `X-API-Key` is a header, and a header on an
unencrypted connection is a password read aloud. (Hugging Face and Railway
terminate TLS for you.)

---

## 4. Verify it is actually protected

```bash
BASE=https://your-space.hf.space      # or your own host
KEY=...                               # the same value as the CEL_API_KEY secret

# 1. it is alive
curl -s $BASE/healthz
# {"status":"ok"}

# 2. it knows the gate is on  (must print: true)
curl -s $BASE/api/v1/stats | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_enabled"])'

# 3. an anonymous write is refused  (must print: 403)
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE/api/v1/verify \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"probe","intent":"x","check":true,"witness":false}'

# 4. a keyed write is judged  (must print: 200)
curl -s -X POST $BASE/api/v1/verify \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
  -d '{"agent_id":"probe","intent":"x","check":true,"witness":false}'
# {"verdict":"FAIL", ...}   -- a FAIL is the system working, not an error

# 5. the book still balances
curl -s $BASE/api/v1/ledger/verify
# {"chain_ok":true, ...}
```

If step 2 prints `false`, the secret did not reach the process: check the Space's
**Settings → Variables and secrets**, and the startup log. If it prints `true` but
step 4 returns `403`, the key in your shell differs from the secret — a typo, or a
trailing space that got trimmed on one side only.

Note that `202`/`502` with no body during the first minute after a build is the
Space's cold start, not the API.

---

## 5. What survives a restart — read this before you promise an audit trail

The ledger's live state is process memory. The file named by `CEL_LEDGER_LOG` is an
**append-only log of evidence**, not a database that is reloaded. So:

| | survives a restart, redeploy, or wake-up |
| --- | --- |
| Trust scores and quarantine state | **no** — every agent returns as `HEALTHY`, trust 100 |
| Counters on the dashboard | **no** — they start at 0 |
| In-memory chain (`GET /api/v1/ledger`) | **no** — the first entry after restart is anchored at 64 zeros again |
| `GET /api/v1/ledger/verify` | **no** — it re-walks the current process's segment only |
| The file at `CEL_LEDGER_LOG` | only if the filesystem persists (a real volume, not a free Space) |

One consequence is worth stating plainly, because it is easy to misread the file
later: a restart starts a **new segment** appended to the same file. Both segments
anchor at the genesis hash, and `seq` restarts at 1, so line 3 of the file can have
a lower `seq` than line 2. The file is a series of sealed segments, not one
continuous chain.

On a free Space, prefer **not setting** `CEL_LEDGER_LOG` at all. A log that
vanishes on restart looks exactly like a log that never had a failure in it when
someone goes looking for evidence — better to have no file than a misleading one.
With a real volume, set it and rotate per boot:

```bash
CEL_LEDGER_LOG=/data/ledger-$(date -u +%Y%m%dT%H%M%SZ).jsonl
```

If you need live trust to survive restarts, that is a real feature (replay on
startup) and it is not in this version. Do not work around it by running two
replicas pointed at the same file — two processes appending to one log produce
interleaved writes and a chain that cannot be re-walked at all.

---

## 6. Using the dashboard once it is public

Only `POST` endpoints need the key. `GET /api/v1/stats`, `GET /api/v1/ledger`,
`GET /api/v1/ledger/verify` and the page itself stay anonymous, because a
monitoring screen that demands a credential is blank exactly when someone needs it.

The key is entered by a human, in the dashboard's key field:

- it is held in `sessionStorage`, so it dies with the browser tab;
- it is never written into the served HTML, so "view source" shows nothing;
- if it is wrong, the panel says **这颗 key 被服务器拒绝（403）** and keeps saying it
  until you enter a new one.

`POST /api/v1/agents/{id}/unlock` requires the same key. An unauthenticated unlock
would let anyone void the one penalty that actually stops a misbehaving agent — a
worse hole than a forged verdict.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Space build fails: `agent_metabolism` / `double_entry` not found | the core tarball step failed, usually a wrong `CEL_CORE_REF` or a repo that went private | set the `ARG CEL_CORE_REF` to an existing tag; the build prints `core <tag> unpacked at /app/core` when it worked |
| Space build fails at `COPY ...`: `file does not exist` | the repo root is one level too high (`product_mvp/` uploaded as a folder) | the Space repo root must *be* the contents of `product_mvp/` |
| Space runs but the URL 404s / "no application" | `README.md` front matter missing or not at the very top, or `sdk: docker` absent | restore the YAML block; `app_port: 7860` and `ENV PORT=7860` must agree |
| Red **鉴权未启用** banner; no key field | `CEL_API_KEY` is unset, empty, or whitespace | set the **secret** and let the Space restart. The startup log says `auth: ENABLED` or `auth: DISABLED`. |
| Simulate button returns 403 | key field empty, or the key differs from the server's | paste the server's key, click 保存. A trailing space is forgiven; a typo is not. |
| `curl` with the key still returns 403 | secret set on a different Space, or a stale build | check `auth_enabled`, and the build/run log of the Space you actually called |
| Container exits with "could not open its ledger log" | `CEL_LEDGER_LOG` points somewhere unwritable (`/data` is not mounted on free Spaces) | unset it on the free tier, or attach storage and use `/data/ledger.jsonl` |
| History is empty after a rebuild, and the disk was fine | expected — live state is memory | see §5 |
| Two instances disagree / counts flicker | replicas > 1 | scale to exactly 1 |
| 502 for ~60 s after a build | cold start | reload |
| Health check red, app logs "running" | a port pinned over the platform's | do not set `CEL_PORT`; let `PORT` win |

---

## 8. Rotating the key

```bash
# 1. generate
python -c 'import secrets; print(secrets.token_urlsafe(32))'
# 2. update the CEL_API_KEY secret (Space → Settings → Variables and secrets)
# 3. re-enter the new key in the dashboard's key field
```

The old key stops working the moment the new process accepts traffic, and every
dashboard tab holding the old one starts receiving 403 — which is the intended
signal, not a failure. There is one shared key per instance; there is no per-client
revocation, so rotating is the only revocation there is.

---

## 9. Ship checklist

- [ ] Space repo root = contents of `product_mvp/` (`README.md` + `Dockerfile` at the root)
- [ ] `README.md` starts with the YAML block (`sdk: docker`, `app_port: 7860`)
- [ ] `CEL_API_KEY` set as a **Secret**, and not present in any committed file
- [ ] Image listens on **7860** (image default), no `CEL_PORT` pinned
- [ ] Replicas = 1
- [ ] `GET /api/v1/stats` reports `auth_enabled: true`
- [ ] Anonymous `POST /api/v1/verify` returns `403`
- [ ] Keyed `POST /api/v1/verify` returns `200`
- [ ] `GET /api/v1/ledger/verify` returns `chain_ok: true`
- [ ] Startup log says `auth: ENABLED`
- [ ] You have told whoever reads the dashboard that a restart resets the counters (§5)

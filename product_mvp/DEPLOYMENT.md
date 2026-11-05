# Deploying CEL to a public URL

This is the 5-minute version. It assumes you have this repository and an account
on a platform that can build a Dockerfile and mount a disk (Render, Railway, Fly,
or any VPS with `docker` on it).

If you only want to click the dashboard on your laptop, stop here and run
`docker compose up --build` from `product_mvp/`. Everything below is about the
part that changes when a stranger can reach the port.

---

## 0. What you are deploying

| Thing | Value | Why it is not optional |
| --- | --- | --- |
| Containers | **1** | The ledger and every trust score live in this process's memory. Two replicas are two ledgers that disagree, and the dashboard will show whichever one the load balancer picked. |
| Disk | **1**, mounted at `/data` | Without it, the append-only hash chain is lost on every deploy and the audit trail is a log that ends at the last restart. |
| Secret | `CEL_API_KEY` | Without it, anyone on the internet can charge an agent's trust. This is the difference between a demo and a liability. |
| Port | whatever the platform injects via `PORT` | The entrypoint reads `PORT`, then `CEL_PORT`, then falls back to `8000`. Do not pin it. |

Three environment variables:

```bash
CEL_API_KEY=<a long random string>        # required on the public internet
CEL_LEDGER_LOG=/data/ledger.jsonl         # required if you mounted the disk
CEL_FAIL_LIMIT=3                          # optional; consecutive FAILs before quarantine
```

Generate a key rather than inventing one:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Never put the result in this repository, a Dockerfile, or a compose file. Every
place below passes it as an environment variable, which is the only reason the
value is not in the image.

---

## 1. Rehearse locally first (2 minutes)

Doing this before touching a platform means that if the deploy misbehaves, you
already know the image is fine and it is a platform setting.

```bash
cd product_mvp
export CEL_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
echo "key: $CEL_API_KEY"      # paste this into the dashboard's key field later
docker compose up --build
```

Open <http://localhost:8000>. The control panel now shows an `X-API-Key` field
and **no** warning banner, which means the gate is on. Paste the key, click
保存, then click 模拟一次假完成 — it should return a `FAIL` verdict.

If you leave `CEL_API_KEY` unset, the panel shows a red **鉴权未启用** banner
instead of the key field. That is the honest state of an unprotected instance;
it is not a bug, and it is not something to ship.

---

## 2. Render

### Option A — Blueprint (one file, one click)

Save as `render.yaml` in the repository root, commit, then **New → Blueprint**:

```yaml
services:
  - type: web
    name: cel-console
    env: docker
    dockerfilePath: ./product_mvp/Dockerfile
    dockerContext: .
    plan: starter                 # a free instance has no disks
    healthCheckPath: /healthz
    numInstances: 1               # do not scale: the ledger is in memory
    envVars:
      - key: CEL_LEDGER_LOG
        value: /data/ledger.jsonl
      - key: CEL_API_KEY
        generateValue: true       # Render generates it; read it from the dashboard
      - key: CEL_FAIL_LIMIT
        value: "3"
    disk:
      name: cel-data
      mountPath: /data
      sizeGB: 1
```

Render generates `CEL_API_KEY` for you — copy it from **Environment** and paste it
into the dashboard's key field. It is shown once and never rendered into the page.

### Option B — Manual

1. **New → Web Service**, connect the repo.
2. Runtime **Docker**; Dockerfile path `product_mvp/Dockerfile`; **Docker build
   context** `.` (the repository root — the image vendors `agent_metabolism` from
   there, and pointing the context at `product_mvp/` produces a build that cannot
   find the core).
3. **Instance type**: anything with a disk. Free instances cannot attach one.
4. **Advanced → Add Disk**: name `cel-data`, mount path `/data`, 1 GB.
5. **Environment**: add `CEL_LEDGER_LOG=/data/ledger.jsonl` and `CEL_API_KEY`
   (use "Generate" in the UI if it offers one).
6. Deploy.

Render injects `PORT`; the container binds it. Do not set `CEL_PORT` — it would
override the platform and the health check would fail against a port nothing is
listening on.

---

## 3. Railway

Railway has no separate disk setting in the service UI for Docker deploys, so use
a volume:

1. **New Project → Deploy from GitHub repo**. Railway detects the Dockerfile.
2. Service **Settings → Build**: Dockerfile path `product_mvp/Dockerfile`.
   If the build fails with `agent_metabolism not found`, set the build context to
   the repository root.
3. **Variables**: `CEL_LEDGER_LOG=/data/ledger.jsonl`, `CEL_API_KEY=<generated>`,
   `CEL_FAIL_LIMIT=3`.
4. **Volumes → Add Volume**, mount path `/data`.
5. **Settings → Scale**: replicas **1**.
6. **Settings → Networking → Generate Domain**.

Railway injects `PORT` and health-checks the container. Nothing else to do.

---

## 4. Any Docker host

```bash
docker build -f product_mvp/Dockerfile -t cel-console:0.2.0 .
docker run -d --name cel \
  -p 8000:8000 \
  -v cel-data:/data \
  -e CEL_LEDGER_LOG=/data/ledger.jsonl \
  -e CEL_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --restart unless-stopped \
  cel-console:0.2.0
```

The container runs as uid `10001`. A named volume inherits the ownership of the
image's `/data` directory, so this works. A **bind mount** (`-v /srv/cel:/data`)
keeps the host's ownership, so either `chown 10001:10001 /srv/cel` first or pass
`--user "$(id -u):$(id -g)"`. If the directory is not writable, the container
refuses to boot and says so — it does not start up and silently fail on the first
write.

Put a TLS-terminating proxy (Caddy, nginx, or the platform's) in front. Do not
serve this over plain HTTP on a public address: `X-API-Key` is a header, and a
header on an unencrypted connection is a password read aloud.

---

## 5. Verify it is actually protected

Run this against the deployed URL. It is the difference between "I set the
variable" and "the gate is closed."

```bash
BASE=https://your-service.onrender.com
KEY=...                       # the same value you put in CEL_API_KEY

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

If step 2 prints `false`, the variable did not reach the process — check for a
typo, a trailing space, or a variable set on a different service.

If step 4 returns `403`, the key in your shell is not the key in the service.

---

## 6. What survives a restart — read this before you promise an audit trail

The ledger's live state is process memory. The file on `/data` is an
**append-only log of evidence**, not a database that is reloaded. So:

| | survives a restart or redeploy |
| --- | --- |
| Trust scores and quarantine state | **no** — every agent returns as `HEALTHY`, trust 100 |
| Counters on the dashboard | **no** — they start at 0 |
| In-memory chain (`GET /api/v1/ledger`) | **no** — the first entry after restart is anchored at 64 zeros again |
| `GET /api/v1/ledger/verify` | **no** — it re-walks the current process's segment only |
| The file at `/data/ledger.jsonl` | **yes** — every sealed entry stays on disk |

One consequence is worth stating plainly, because it is easy to misread the file
later: a restart starts a **new segment** appended to the same file. Both
segments anchor at the genesis hash, and `seq` restarts at 1, so line 3 of the
file can have a lower `seq` than line 2. The file is a series of sealed
segments, not one continuous chain.

If you need one file to be one continuous chain, rotate per boot:

```bash
CEL_LEDGER_LOG=/data/ledger-$(date -u +%Y%m%dT%H%M%SZ).jsonl
```

If you need live trust to survive restarts, that is a real feature (replay on
startup) and it is not in this version. Do not work around it by running two
replicas pointed at the same file — two processes appending to one log produce
interleaved writes and a chain that cannot be re-walked at all.

---

## 7. Using the dashboard once it is public

Only `POST` endpoints need the key. `GET /api/v1/stats`, `GET /api/v1/ledger`,
`GET /api/v1/ledger/verify` and the page itself stay anonymous, because a
monitoring screen that demands a credential is blank exactly when someone needs
it.

The key is entered by a human, in the dashboard's key field:

- it is held in `sessionStorage`, so it dies with the browser tab;
- it is never written into the served HTML, so "view source" shows nothing;
- if it is wrong, the panel says **这颗 key 被服务器拒绝（403）** and keeps saying
  it until you enter a new one.

`POST /api/v1/agents/{id}/unlock` requires the same key. An unauthenticated
unlock would let anyone void the one penalty that actually stops a misbehaving
agent — a worse hole than a forged verdict.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Red **鉴权未启用** banner; no key field | `CEL_API_KEY` is unset, empty, or whitespace | Set it and redeploy. The startup log line says which: `auth: DISABLED` vs `auth: ENABLED`. |
| Dashboard's simulate button returns 403 | key field empty, or the key differs from the server's | Paste the server's key, click 保存. A trailing space is forgiven; a typo is not. |
| `curl` with the key still returns 403 | key set on a different service, or a stale deploy | Check `auth_enabled` and the startup log of the service you actually called. |
| Container exits at boot with "could not open its ledger log" | `/data` not mounted, or not writable by uid `10001` | Mount the disk at `/data`; `chown` it for bind mounts. The message names both causes. |
| History is empty after a deploy, disk is fine | expected — live state is memory | See §6. The file still has the evidence. |
| Two instances disagree / counts flicker | replicas > 1 | Scale to exactly 1. |
| Health check red, app logs "running" | `CEL_PORT` pinned over the platform's `PORT` | Remove `CEL_PORT` from the environment. |
| `agent_metabolism` not found during build | build context pointed at `product_mvp/` | Set the context to the repository root. |

---

## 9. Rotating the key

```bash
# 1. generate
python -c 'import secrets; print(secrets.token_urlsafe(32))'
# 2. set CEL_API_KEY on the service and redeploy
# 3. re-enter the new key in the dashboard's key field
```

The old key stops working the moment the new process accepts traffic, and every
dashboard tab holding the old one starts receiving 403 — which is the intended
signal, not a failure. There is one shared key per instance; there is no
per-client revocation, so rotating is the only revocation there is.

---

## 10. Ship checklist

- [ ] `CEL_API_KEY` set, and **not** present in `Dockerfile`, `docker-compose.yml`, or any committed file
- [ ] Persistent disk mounted at `/data`
- [ ] `CEL_LEDGER_LOG=/data/ledger.jsonl`
- [ ] Replicas = 1
- [ ] `GET /api/v1/stats` reports `auth_enabled: true`
- [ ] Anonymous `POST /api/v1/verify` returns `403`
- [ ] Keyed `POST /api/v1/verify` returns `200`
- [ ] `GET /api/v1/ledger/verify` returns `chain_ok: true`
- [ ] HTTPS in front of the service
- [ ] Startup log says `auth: ENABLED`

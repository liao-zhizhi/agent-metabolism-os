#!/usr/bin/env bash
# Deploy product_mvp to the demo host, and prove it took.
#
# Written because the manual path produced two silent failures that both looked
# like "the fix did not work":
#
#   1. the container ran with PORT=7860 while its command said --port 8000, so
#      the image's own HEALTHCHECK probed a dead port and the container sat
#      "unhealthy" for 21 hours while serving fine;
#   2. Cloudflare cached /static/*.js for four hours, so the API started
#      returning witness_hash while the console kept rendering the old file.
#
# Neither is caught by "docker ps says Up". Both are caught by the checks below,
# which is why they are here and not in a runbook.
#
# Rollback: the previous files are copied to /root/cel-backup-<ts>/ and the
# previous image is tagged cel-dashboard:rollback-<ts> before anything changes.
#
# Usage:
#   scripts/deploy_demo.sh                     # deploy current worktree
#   scripts/deploy_demo.sh --dry-run           # show what would be sent

set -euo pipefail

HOST="${CEL_DEPLOY_HOST:-root@107.172.186.4}"
REMOTE_DIR="/opt/agent-metabolism-os/product_mvp"
URL="${CEL_DEMO_URL:-https://demo.silicon-probe.com}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
TAG="mcgs-$(date +%Y%m%d-%H%M%S)"
DRY=0

#: Writes allowed per client IP per window. 10 rather than 5: the console is a
#: demo, and a visitor clicking the scenarios twice should not hit the wall.
RATE_LIMIT="${CEL_DEPLOY_RATE_LIMIT:-10}"
RATE_WINDOW="${CEL_DEPLOY_RATE_WINDOW:-60}"

#: The anonymous trial: how much a visitor may do *without* a key. 1 per 5
#: minutes is the funnel, not a mistake -- the second click has to be the moment
#: the page offers the local install. It is a separate knob from RATE_LIMIT so
#: that turning the funnel off (CEL_DEPLOY_ANON_LIMIT=0) does not also tighten
#: the budget for callers who brought a key.
ANON_LIMIT="${CEL_DEPLOY_ANON_LIMIT:-1}"
ANON_WINDOW="${CEL_DEPLOY_ANON_WINDOW:-300}"

#: The origin is reached only through the Cloudflare tunnel (cloudflared runs
#: --network host and dials 127.0.0.1:80), so the published port is bound to
#: loopback. This, not an iptables rule, is what makes the origin unreachable
#: from the internet: a rule can be forgotten on a rebuild, a bind cannot. It is
#: also what makes CEL_TRUST_PROXY safe to turn on -- the only party that can
#: reach the port is the tunnel, so X-Forwarded-For is written by a hop we own.
PUBLISH="${CEL_DEPLOY_PUBLISH:-127.0.0.1:80:7860}"

[[ "${1:-}" == "--dry-run" ]] && DRY=1

cd "$(dirname "$0")/.."
SRC="$(pwd)/product_mvp"
[[ -d "$SRC" ]] || { echo "product_mvp not found at $SRC" >&2; exit 1; }

FILES=(api_server.py cel_engine.py example_client.py
       Dockerfile .dockerignore
       static/app.js static/style.css templates/index.html
       tests/test_api.py tests/test_engine.py)

echo "== 1/6 local test gate =="
# Refuse to ship a tree that fails its own contract tests. A deploy is the worst
# place to discover a regression, because rollback costs a rebuild.
PY="${CEL_DEPLOY_PYTHON:-python3}"
( cd "$SRC" && "$PY" -m unittest discover -s tests -q 2>&1 | tail -3 )
echo "   tests ok"

echo "== 2/6 backup remote state =="
if [[ $DRY -eq 1 ]]; then
  echo "   [dry-run] would back up to /root/cel-backup-<ts>/"
else
  $SSH "$HOST" "set -e
    B=/root/cel-backup-\$(date +%Y%m%d-%H%M%S); mkdir -p \$B
    cd $REMOTE_DIR && cp ${FILES[*]} \$B/ 2>/dev/null || true
    docker tag cel-dashboard:latest cel-dashboard:rollback-\$(date +%H%M%S)
    docker inspect cel-app > \$B/container.json 2>/dev/null || true
    echo \"   backup: \$B\""
fi

echo "== 3/6 transfer =="
# The core is transferred into product_mvp/core_snapshot/ so the image is built
# from the code under test. Without this the Dockerfile falls back to the pinned
# release tarball and the service ships calling methods its core does not have --
# a failure that surfaces on the first request, not at build time. That is not
# hypothetical: the falsification endpoint was written against an unreleased
# `PenaltyLedger.record_falsification`, and the tag-pinned build would have
# booted happily and 500'd on the first refutation.
if [[ $DRY -eq 1 ]]; then
  ( cd "$SRC" && tar czf - "${FILES[@]}" ) | tar tzf - | sed 's/^/   [dry-run] /'
  echo "   [dry-run] + agent_metabolism/ -> core_snapshot/agent_metabolism/"
else
  ( cd "$SRC" && tar czf - "${FILES[@]}" ) | $SSH "$HOST" "tar xzf - -C $REMOTE_DIR"
  echo "   transferred ${#FILES[@]} files"

  # Stage this checkout's core into the remote build context. It is the same
  # code the local tests just ran against, which is the only version whose
  # passing tests mean anything about the image.
  CORE_LOCAL="$(cd "$SRC/.." && pwd)/agent_metabolism"
  [[ -d "$CORE_LOCAL" ]] || { echo "   FATAL: core not found at $CORE_LOCAL" >&2; exit 1; }
  ( cd "$SRC/.." && tar czf - agent_metabolism ) | $SSH "$HOST" \
    "rm -rf $REMOTE_DIR/core_snapshot/agent_metabolism && mkdir -p $REMOTE_DIR/core_snapshot && tar xzf - -C $REMOTE_DIR/core_snapshot"
  echo "   core snapshot staged for the build"
fi
[[ $DRY -eq 1 ]] && { echo "dry-run complete"; exit 0; }

echo "== 4/6 rebuild and restart =="
# The command is deliberately NOT overridden with --port: the image already
# declares PORT=7860, its HEALTHCHECK reads PORT, and overriding only one of the
# three is what produced the permanent "unhealthy" state.
$SSH "$HOST" "set -e
  cd $REMOTE_DIR
  docker build -q -t cel-dashboard:$TAG . >/dev/null
  KEY=\$(docker inspect cel-app --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^CEL_API_KEY=' | cut -d= -f2-)
  test -n \"\$KEY\" || { echo 'refusing to deploy without the API key' >&2; exit 1; }

  # Verify the image BEFORE swapping the container. A gate that runs after the
  # restart protects nothing: a failed assert would leave production running the
  # broken build, which is exactly what happened the first time this check was
  # written in the other order. Build and prove first, then swap.
  docker run --rm --entrypoint python cel-dashboard:$TAG -c \"
import sys; sys.path.insert(0,'/app/core')
from agent_metabolism.penalty import PenaltyLedger
assert hasattr(PenaltyLedger,'record_falsification'), 'image core is stale: no record_falsification'
import api_server
assert any('/api/v1/falsify' == r.path for r in api_server.app.routes), 'falsify route missing'
print('   image verified: falsify route + record_falsification')
\" || { echo '   FATAL: image failed verification; production untouched' >&2; exit 1; }

  # Only now does :latest move. Tagging it at build time would leave the rollback
  # pointer aimed at an image that just failed its own gate.
  docker tag cel-dashboard:$TAG cel-dashboard:latest

  docker stop cel-app >/dev/null; docker rm cel-app >/dev/null
  # -p $PUBLISH: loopback only. Nothing on the internet can reach the origin
  # directly, which is what makes CEL_TRUST_PROXY=1 below sound: the only hop
  # that can reach this port is the tunnel we control, so X-Forwarded-For was
  # written by that hop and not by the caller.
  docker run -d --name cel-app --restart always \
    -p $PUBLISH -v /opt/cel_data:/data \
    -e CEL_API_KEY=\"\$KEY\" -e CEL_LEDGER_LOG=/data/ledger.jsonl \
    -e CEL_RATE_LIMIT=$RATE_LIMIT -e CEL_RATE_WINDOW=$RATE_WINDOW \
    -e CEL_ANON_LIMIT=$ANON_LIMIT -e CEL_ANON_WINDOW=$ANON_WINDOW \
    -e CEL_TRUST_PROXY=1 \
    cel-dashboard:$TAG >/dev/null
  echo \"   image cel-dashboard:$TAG (listen $PUBLISH, ${RATE_LIMIT} writes/${RATE_WINDOW}s per IP, anonymous trial ${ANON_LIMIT}/${ANON_WINDOW}s)\""

echo "== 5/6 health gate =="
for i in $(seq 1 30); do
  STATUS=$($SSH "$HOST" "docker inspect cel-app --format '{{.State.Health.Status}}'" 2>/dev/null || echo starting)
  [[ "$STATUS" == "healthy" ]] && { echo "   container healthy"; break; }
  [[ $i -eq 30 ]] && { echo "   FATAL: container never became healthy ($STATUS)" >&2; exit 1; }
  sleep 2
done

echo "== 6/6 public verification =="
# The health gate above only proves the container is alive. These two prove the
# *public* path serves the new contract -- which is the thing that was actually
# broken by cache and by the port mismatch.
SHAPE=$(curl -s --max-time 20 "$URL/openapi.json" | python3 -c "
import json,sys
spec=json.load(sys.stdin)
p=spec['components']['schemas']['VerifyRequest']['properties']
paths=spec['paths']
ok = {'witness_hash','trace_id'} <= set(p) and '/api/v1/falsify' in paths
print('ok' if ok else 'STALE')")
[[ "$SHAPE" == "ok" ]] || { echo "   FATAL: public API lacks witness_hash or /api/v1/falsify" >&2; exit 1; }
echo "   public schema: witness_hash + trace_id + falsify endpoint present"

HTML=$(curl -s --max-time 20 "$URL/")
grep -qE '/static/app\.js\?v=[0-9a-f]{10}' <<<"$HTML" \
  || { echo "   FATAL: asset URL is not versioned; the CDN will serve a stale bundle" >&2; exit 1; }
# The landing page is now the product's front door. Check that the three things
# it promises are actually in the served HTML: nobody installs a tool they
# cannot find a command for.
for marker in "Audit your own agents locally" "Zero data leaves your machine" \
              "harness-audit-sidecar" '"mcpServers"' "causal-evolution-paper"; do
  grep -qF "$marker" <<<"$HTML" \
    || { echo "   FATAL: served landing page is missing: $marker" >&2; exit 1; }
done
echo "   public page: hero copy, install command, MCP config, repo links present"

# The hero CTA is a promise about the outside world, and it can go stale in the
# direction that hurts: the page keeps saying "not on PyPI yet" after a release,
# or keeps advertising `pip install harness-audit-sidecar` while that name still
# 404s. Both are the same bug as a wrong verdict -- a claim the evidence does not
# support -- so the deploy checks the claim against PyPI instead of trusting the
# person who last edited the HTML.
if curl -sf --max-time 10 https://pypi.org/pypi/harness-audit-sidecar/json >/dev/null; then
  grep -q "尚未发布" <<<"$HTML" \
    && { echo "   FATAL: package is on PyPI but the page still says 尚未发布" >&2; exit 1; }
  echo "   install claim: package is on PyPI, page does not deny it"
else
  grep -qF 'pip install "git+https://github.com/liao-zhizhi/harness-audit-sidecar' <<<"$HTML" \
    || { echo "   FATAL: package is not on PyPI but the page does not offer the git+ command" >&2; exit 1; }
  echo "   install claim: package is not on PyPI, page offers the command that works"
fi
JSV=$(grep -oE '/static/app\.js\?v=[0-9a-f]{10}' <<<"$HTML")
JS=$(curl -s --max-time 20 "$URL$JSV")
# Check for the wording, not just for the feature. A bundle that still renders a
# bare chain glyph passes any test that only looks for evidenceTag -- and that
# bundle is the overclaim we removed.
grep -q "commit \${shortHash" <<<"$JS" \
  || { echo "   FATAL: served app.js still renders a bare glyph; labels must say 'commit'" >&2; exit 1; }
# Same trap for the reconciliation control: a bundle without it looks identical
# in a screenshot to one where every entry happened to be un-refutable.
grep -q "api/v1/falsify" <<<"$JS" \
  || { echo "   FATAL: served app.js has no falsification client" >&2; exit 1; }
echo "   public bundle: $JSV labels commitments, and ships the reconciliation flow"

# The origin must not be reachable except through the tunnel. Checking the
# published binding, not a firewall rule: the rule can be absent while the bind
# holds, and that is the case that matters.
# `docker port` takes the CONTAINER port (7860), not the host port (80). Asking
# for 80/tcp prints "no public port published" and looks like a failed bind.
BIND=$($SSH "$HOST" "docker port cel-app 7860/tcp")
[[ "$BIND" == 127.0.0.1:* ]] \
  || { echo "   FATAL: origin port is published as $BIND, not loopback" >&2; exit 1; }
echo "   origin binding: $BIND (loopback only; direct internet access refused)"

# And the budget the process is actually using, not the one in this script.
LIMIT=$(curl -s --max-time 20 "$URL/api/v1/stats" | python3 -c "
import json,sys
print(json.load(sys.stdin).get('rate_limit',{}).get('limit'))")
[[ "$LIMIT" == "$RATE_LIMIT" ]] \
  || { echo "   FATAL: live rate limit is $LIMIT, expected $RATE_LIMIT" >&2; exit 1; }
echo "   live budget: $LIMIT writes per ${RATE_WINDOW}s per client IP"

# The trial has to be *observed*, not assumed. Two anonymous writes: the first
# must be judged without being sealed, the second must be refused with the
# funnel's 429. A deploy that only checks the stats block would pass while the
# endpoint still demanded a key, and the page would then be advertising a trial
# that does not exist.
TRIAL_BODY='{"agent_id":"deploy_probe","intent":"deploy verification","check":true,"witness":false}'
FIRST=$(curl -s --max-time 20 -o /tmp/cel_trial_probe.json -w '%{http_code}' \
  -X POST "$URL/api/v1/verify" -H 'Content-Type: application/json' -d "$TRIAL_BODY")
if [[ "$ANON_LIMIT" != "0" ]]; then
  [[ "$FIRST" == "200" ]] \
    || { echo "   FATAL: anonymous trial returned $FIRST, expected 200" >&2; exit 1; }
  python3 - <<'PY' || { echo "   FATAL: anonymous response claimed to be sealed" >&2; exit 1; }
import json, sys
body = json.load(open("/tmp/cel_trial_probe.json"))
sys.exit(0 if body.get("sealed") is False and body.get("channel") == "anonymous_trial" else 1)
PY
  SECOND=$(curl -s --max-time 20 -o /dev/null -w '%{http_code}' \
    -X POST "$URL/api/v1/verify" -H 'Content-Type: application/json' -d "$TRIAL_BODY")
  [[ "$SECOND" == "429" ]] \
    || { echo "   FATAL: second anonymous write returned $SECOND, expected the trial's 429" >&2; exit 1; }
  echo "   anonymous trial: 1st write judged (unsealed), 2nd refused 429 -- funnel is live"
else
  [[ "$FIRST" == "403" ]] \
    || { echo "   FATAL: trial disabled but anonymous write returned $FIRST" >&2; exit 1; }
  echo "   anonymous trial: disabled (403), as configured"
fi

# And the production ledger must not have grown from those two probes: an
# anonymous row on the chain would mean the funnel is writing evidence nobody
# can be held to.
STATS_JSON=$(curl -s --max-time 20 "$URL/api/v1/stats")
grep -q "deploy_probe" <<<"$STATS_JSON" \
  && { echo "   FATAL: the anonymous probe was booked as an agent; the trial is sealing" >&2; exit 1; }
SEALS=$(python3 -c "
import json,sys
print(json.loads(sys.argv[1])['write_policy']['anonymous']['seals'])" "$STATS_JSON")
[[ "$SEALS" == "False" ]] \
  || { echo "   FATAL: stats claims the anonymous tier seals entries" >&2; exit 1; }
echo "   trial probe left no agent on the production chain"
echo
echo "deployed: $TAG"
echo "rollback: docker tag cel-dashboard:rollback-* cel-dashboard:latest && restart"

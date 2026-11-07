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
  docker run -d --name cel-app --restart always \
    -p 80:7860 -v /opt/cel_data:/data \
    -e CEL_API_KEY=\"\$KEY\" -e CEL_LEDGER_LOG=/data/ledger.jsonl \
    cel-dashboard:$TAG >/dev/null
  echo \"   image cel-dashboard:$TAG\""

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
echo
echo "deployed: $TAG"
echo "rollback: docker tag cel-dashboard:rollback-* cel-dashboard:latest && restart"

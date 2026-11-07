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
if [[ $DRY -eq 1 ]]; then
  ( cd "$SRC" && tar czf - "${FILES[@]}" ) | tar tzf - | sed 's/^/   [dry-run] /'
else
  ( cd "$SRC" && tar czf - "${FILES[@]}" ) | $SSH "$HOST" "tar xzf - -C $REMOTE_DIR"
  echo "   transferred ${#FILES[@]} files"
fi
[[ $DRY -eq 1 ]] && { echo "dry-run complete"; exit 0; }

echo "== 4/6 rebuild and restart =="
# The command is deliberately NOT overridden with --port: the image already
# declares PORT=7860, its HEALTHCHECK reads PORT, and overriding only one of the
# three is what produced the permanent "unhealthy" state.
$SSH "$HOST" "set -e
  cd $REMOTE_DIR
  docker build -q -t cel-dashboard:$TAG -t cel-dashboard:latest . >/dev/null
  KEY=\$(docker inspect cel-app --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^CEL_API_KEY=' | cut -d= -f2-)
  test -n \"\$KEY\" || { echo 'refusing to deploy without the API key' >&2; exit 1; }
  docker stop cel-app >/dev/null; docker rm cel-app >/dev/null
  docker run -d --name cel-app --restart always \
    -p 80:7860 -v /opt/cel_data:/data \
    -e CEL_API_KEY=\"\$KEY\" -e CEL_LEDGER_LOG=/data/ledger.jsonl \
    cel-dashboard:latest >/dev/null
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
p=json.load(sys.stdin)['components']['schemas']['VerifyRequest']['properties']
print('ok' if {'witness_hash','trace_id'} <= set(p) else 'STALE')")
[[ "$SHAPE" == "ok" ]] || { echo "   FATAL: public VerifyRequest lacks witness_hash" >&2; exit 1; }
echo "   public schema: witness_hash + trace_id present"

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
echo "   public bundle: $JSV labels commitments as commitments"
echo
echo "deployed: $TAG"
echo "rollback: docker tag cel-dashboard:rollback-* cel-dashboard:latest && restart"

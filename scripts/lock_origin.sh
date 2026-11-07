#!/usr/bin/env bash
# Close the origin's public port, leaving the Cloudflare tunnel as the only way in.
#
# Run ON THE SERVER (idempotent; safe to re-run):
#
#   sudo bash scripts/lock_origin.sh
#   sudo bash scripts/lock_origin.sh --undo
#
# Why this exists at all, given deploy_demo.sh already publishes 127.0.0.1:80:
# the bind is the real gate, and it is checked by the deploy script. This is the
# second layer, for the rebuild that someone hand-runs as `docker run -p 80:7860`
# and forgets. Two layers, two different failure modes.
#
# Read this before editing the rules:
#
#   * DOCKER PUBLISHES PORTS IN nat/PREROUTING, NOT IN INPUT. A packet from the
#     internet to :80 is DNAT'd to the container and then traverses FORWARD, so
#     `iptables -A INPUT -p tcp --dport 80 -j DROP` does not stop it. That is the
#     classic "ufw is on and the port is still open" bug. The rules below are
#     therefore in DOCKER-USER and INPUT both: DOCKER-USER catches anything a
#     container publishes, INPUT catches a plain host process.
#
#   * Port 22 is never named here. SSH is this script's only way back in.
#
#   * cloudflared runs --network host and dials 127.0.0.1:80, so its traffic
#     never leaves the loopback interface. Every rule below exempts `lo`
#     explicitly; dropping loopback would take the site down while leaving SSH
#     perfectly alive, which is the confusing failure.

set -euo pipefail

PORT="${CEL_ORIGIN_PORT:-80}"
CHAIN_COMMENT="cel-origin"

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
UNDO=0
[[ "${1:-}" == "--undo" ]] && UNDO=1

# -- idempotent helpers ------------------------------------------------------
# `-A` twice on the same rule stacks duplicates silently, and 300 copies of a
# DROP rule still look like one rule in `iptables -L`. Check before adding --
# and check with the comment included, or the check never matches what was
# written and every run adds another copy.
add() {
  local rule=("$@" -m comment --comment "$CHAIN_COMMENT")
  iptables -C "${rule[@]}" 2>/dev/null || iptables -A "${rule[@]}"
}
del() {
  local rule=("$@" -m comment --comment "$CHAIN_COMMENT")
  while iptables -C "${rule[@]}" 2>/dev/null; do
    iptables -D "${rule[@]}" || break
  done
}

if [[ $UNDO -eq 1 ]]; then
  del INPUT -i lo -j ACCEPT
  del INPUT -i docker0 -p tcp --dport "$PORT" -j ACCEPT
  del INPUT -p tcp --dport "$PORT" -j DROP
  del DOCKER-USER -i docker0 -j RETURN
  del DOCKER-USER -i lo -j RETURN
  del DOCKER-USER -p tcp --dport 7860 -j DROP
  echo "rules removed"
else
  # Order matters: ACCEPT lines first, DROP last. `-A` appends, so the DROP is
  # only ever reached after every exemption above it has been considered.
  add INPUT -i lo -j ACCEPT
  add INPUT -i docker0 -p tcp --dport "$PORT" -j ACCEPT
  add INPUT -p tcp --dport "$PORT" -j DROP

  # Container-published path (post-DNAT port is the container's own: 7860).
  # RETURN for the tunnel's own bridge traffic, DROP for everything else.
  add DOCKER-USER -i docker0 -j RETURN
  add DOCKER-USER -i lo -j RETURN
  add DOCKER-USER -p tcp --dport 7860 -j DROP
  echo "rules installed for port $PORT (container port 7860)"
fi

# -- persistence -------------------------------------------------------------
# netfilter-persistent is not installed on this image, and installing it
# interactively is not something a script should do. A oneshot unit that
# restores a file is equivalent and has no packaging side effects.
#
# The file holds only *our* rules and it is restored with --noflush, on purpose.
# A full `iptables-save` image would freeze DOCKER/DOCKER-USER as they look right
# now, and restoring that at boot (before dockerd has created them) fails or
# fights dockerd for ownership of its own chains. `--noflush` appends, so it
# cannot erase a chain somebody else owns -- at worst it adds our lines twice,
# which is why the unit is idempotent by construction and not by hope.
RULES=/etc/cel-origin.rules
UNIT=/etc/systemd/system/cel-origin-firewall.service

if [[ $UNDO -eq 1 ]]; then
  systemctl disable --now cel-origin-firewall.service 2>/dev/null || true
  rm -f "$UNIT" "$RULES"
  systemctl daemon-reload
  echo "persistence removed"
else
  {
    echo "*filter"
    iptables-save | grep -E "^-A (INPUT|DOCKER-USER) .*cel-origin"
    echo "COMMIT"
  } > "$RULES"
  cat > "$UNIT" <<'EOF'
[Unit]
Description=Keep the CEL origin reachable only through the Cloudflare tunnel
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/iptables-restore --noflush /etc/cel-origin.rules

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable cel-origin-firewall.service >/dev/null
  echo "persisted: $RULES ($UNIT)"
fi

echo
echo "verify from OUTSIDE the host:   curl -I http://<public-ip>/   # expect timeout/refused"
echo "verify the tunnel still works:  curl -I https://demo.silicon-probe.com/   # expect 200"
echo "rollback:                       sudo bash scripts/lock_origin.sh --undo"

#!/bin/bash
# router_scout_enroll.sh — one-shot enrollment of an OpenWrt router into the
# meshforge-scout telemetry loop. Runs on the MANAGER box.
#
# Usage:
#   SCOUT_SSH="-p 2222 root@127.0.0.1" scripts/router_scout_enroll.sh <device-id>
#
# SCOUT_SSH is the ssh target/options for the router (operator-specific —
# MF014: never committed; for a NATed router it is the reverse-tunnel
# loopback). <device-id> is the identity the router will report in every
# tick and the kilo "scout" anchor value — short, stable, filename-safe.
#
# Steps (idempotent — safe to re-run):
#   1. scp the agent + conf template to the router, chmod
#   2. persist /etc/meshforge/ across sysupgrade flashes
#   3. install the router crontab line (*/15 check) if absent
#   4. run one collect and echo the tick back — the install proof
#   5. print the manager-side wiring the OPERATOR then applies by hand:
#      untracked wrapper, cron_verdict crontab line, kilo_nodes stanza

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
AGENT="$HERE/../templates/openwrt/meshforge-scout"
CONF_TPL="$HERE/../templates/openwrt/scout.conf.example"

DEVICE="${1:?usage: SCOUT_SSH=\"...\" router_scout_enroll.sh <device-id>}"
: "${SCOUT_SSH:?SCOUT_SSH unset — pass the router ssh target (MF014: operator value, never in repo)}"

case "$DEVICE" in
    *[!A-Za-z0-9._-]*|'')
        echo "device id '$DEVICE' must be filename-safe ([A-Za-z0-9._-])" >&2
        exit 2 ;;
esac

[ -f "$AGENT" ] || { echo "agent not found at $AGENT" >&2; exit 1; }

# shellcheck disable=SC2086 — SCOUT_SSH deliberately word-splits
rsh() { ssh -o ConnectTimeout=10 -o BatchMode=yes $SCOUT_SSH "$@"; }

echo "== 1/5 installing agent on the router"
# scp needs -P for port; derive scp args from ssh-style SCOUT_SSH by using
# ssh itself as the transport (portable across dropbear/openssh targets).
rsh "mkdir -p /etc/meshforge"
rsh "cat > /usr/bin/meshforge-scout && chmod 755 /usr/bin/meshforge-scout" < "$AGENT"
if rsh "[ -f /etc/meshforge/scout.conf ]"; then
    echo "   scout.conf already present — left untouched"
else
    sed "s/^#SCOUT_DEVICE=.*/SCOUT_DEVICE=\"$DEVICE\"/" "$CONF_TPL" \
        | rsh "cat > /etc/meshforge/scout.conf"
    echo "   scout.conf installed with SCOUT_DEVICE=$DEVICE"
fi

echo "== 2/5 persisting /etc/meshforge/ across sysupgrade"
rsh "grep -q '^/etc/meshforge/' /etc/sysupgrade.conf 2>/dev/null || echo '/etc/meshforge/' >> /etc/sysupgrade.conf"

echo "== 3/5 installing router crontab (*/15 check)"
rsh "crontab -l 2>/dev/null | grep -q meshforge-scout || { (crontab -l 2>/dev/null; echo '*/15 * * * * /usr/bin/meshforge-scout check') | crontab -; /etc/init.d/cron restart 2>/dev/null || true; }"

echo "== 4/5 proof collect — the tick as the router wrote it:"
rsh "/usr/bin/meshforge-scout collect && cat /etc/meshforge/scout_tick.json"

echo "== 5/5 manager-side wiring (apply by hand — operator values stay untracked):"
cat <<WIRING

  # ~/router_scout_pull  (chmod +x; UNTRACKED — carries the operator target)
  #!/bin/bash
  export SCOUT_SSH="$SCOUT_SSH"
  exec /opt/meshforge/scripts/router_scout_pull.sh "\$@"

  # crontab -e on this box (pull + verdict):
  7,37 * * * * \$HOME/router_scout_pull >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh router_scout \$?

  # ~/.config/meshforge/kilo_nodes.json — add to "nodes":
  {
    "kilo_id": "$DEVICE",
    "role": "router",
    "ids": {"scout": "$DEVICE"},
    "expected_metrics": ["vsz_kb", "mmap_regions", "uptime_s"],
    "cadence_s": 1800,
    "notes": "OpenWrt router via meshforge-scout tick mirror"
  }

  # verify end-to-end:
  \$HOME/router_scout_pull && tail -1 ~/router_scout_pull.log
  PYTHONPATH=/opt/meshforge/src python3 -m kilo collect --transport scout
  PYTHONPATH=/opt/meshforge/src python3 -m kilo status
WIRING
echo "enrolled: $DEVICE"

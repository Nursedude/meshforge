#!/usr/bin/env bash
# Install + configure Prometheus on a MeshForge NOC host.
#
# Phase D-4 of the map-domain audit arc. Hosted on moc1 (per the
# locked-in decision: Prometheus + Grafana co-locate on moc1).
#
# Usage:
#   sudo bash scripts/install_prometheus.sh
#
# Idempotent: re-running installs apt updates if any, copies the
# current scrape config into /etc/prometheus/, and restarts the
# unit. Existing Prometheus TSDB storage is preserved — the apt
# package owns /var/lib/prometheus.
#
# Verifies the install by polling :9090/-/ready; exits non-zero
# if Prometheus doesn't come up within 30s.

set -euo pipefail

SELF="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
TEMPLATE="$REPO_DIR/templates/prometheus/prometheus.yml"

if [[ "$EUID" -ne 0 ]]; then
    echo "$SELF: must run as root (sudo bash $0)" >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "$SELF: template not found at $TEMPLATE" >&2
    exit 1
fi

# --- Step 1: apt install ---
echo "[INFO] Installing prometheus via apt..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq prometheus
echo "[OK] prometheus package installed"

# --- Step 2: deploy scrape config ---
echo "[INFO] Deploying scrape config to /etc/prometheus/prometheus.yml..."
# Backup existing config (single generation)
if [[ -f /etc/prometheus/prometheus.yml ]]; then
    cp -p /etc/prometheus/prometheus.yml \
        /etc/prometheus/prometheus.yml.before-meshforge
fi
install -o root -g root -m 0644 "$TEMPLATE" /etc/prometheus/prometheus.yml
echo "[OK] config deployed"

# --- Step 3: validate config syntax ---
echo "[INFO] Validating config syntax..."
if ! promtool check config /etc/prometheus/prometheus.yml >/dev/null 2>&1; then
    echo "[ERROR] promtool reported config errors:" >&2
    promtool check config /etc/prometheus/prometheus.yml >&2
    echo "[INFO] Restoring previous config..." >&2
    if [[ -f /etc/prometheus/prometheus.yml.before-meshforge ]]; then
        cp -p /etc/prometheus/prometheus.yml.before-meshforge \
            /etc/prometheus/prometheus.yml
    fi
    exit 1
fi
echo "[OK] config syntax valid"

# --- Step 4: restart unit ---
echo "[INFO] Restarting prometheus.service..."
systemctl daemon-reload
systemctl enable --now prometheus
echo "[OK] prometheus.service enabled + started"

# --- Step 5: wait for ready ---
echo "[INFO] Waiting for /-/ready ..."
deadline=$((SECONDS + 30))
while [[ $SECONDS -lt $deadline ]]; do
    if curl -fsS http://localhost:9090/-/ready >/dev/null 2>&1; then
        echo "[OK] Prometheus is ready"
        break
    fi
    sleep 1
done

if ! curl -fsS http://localhost:9090/-/ready >/dev/null 2>&1; then
    echo "[ERROR] Prometheus did not become ready in 30s" >&2
    journalctl -u prometheus -n 30 --no-pager >&2
    exit 1
fi

# --- Step 6: report scrape targets ---
echo
echo "Scrape targets:"
curl -fsS http://localhost:9090/api/v1/targets 2>/dev/null | \
    python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('data', {}).get('activeTargets', []):
    labels = t.get('labels', {})
    err = (t.get('lastError') or '')[:60]
    print(f\"  {labels.get('job','?'):20s} {labels.get('instance','?'):22s} health={t.get('health','?'):5s} {err}\")
" 2>/dev/null || echo "  (could not parse target list)"

# --- Step 7: hostname resolution check ---
# The scrape config uses fleet hostnames (moc2, moc1, etc.) which
# operators set up as SSH aliases — but the prometheus daemon does
# NOT read ~/.ssh/config. If a target shows health=down with a
# "lookup ... no such host" error, the fix is /etc/hosts entries.
echo
echo "Hostname resolution check:"
miss=0
for t in $(grep -oE '"[a-zA-Z0-9_.-]+:[0-9]+"' /etc/prometheus/prometheus.yml | tr -d '"'); do
    host="${t%:*}"
    [[ "$host" == "localhost" ]] && continue
    if ! getent hosts "$host" >/dev/null 2>&1; then
        echo "  MISS  $host — add to /etc/hosts so prometheus can scrape it"
        miss=$((miss + 1))
    fi
done
if [[ "$miss" -gt 0 ]]; then
    cat <<EOF

  Operator action: $miss scrape host(s) need /etc/hosts entries.
  Example:
    echo "192.168.86.41 moc2" | sudo tee -a /etc/hosts
    sudo systemctl restart prometheus

  ssh-config aliases work for ssh + scp but NOT for prometheus —
  the daemon runs under a system user with no shell config.
EOF
fi

echo
echo "Done. Prometheus UI: http://localhost:9090/"
echo "Edit config: \$EDITOR /etc/prometheus/prometheus.yml"
echo "Reload after edit: sudo systemctl reload prometheus"

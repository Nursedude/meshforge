#!/usr/bin/env bash
# Install + configure Grafana on a MeshForge NOC host (Phase E of the
# map-domain audit arc).
#
# Hosted on moc1 (per the locked-in arc decision: Prometheus + Grafana
# co-locate on moc1). Adds Grafana's signed apt repo, installs the OSS
# package, provisions the local Prometheus :9090 datasource and the
# starter dashboard set out of the in-tree template dir.
#
# Usage:
#   sudo bash scripts/install_grafana.sh
#
# Idempotent: re-running re-deploys the provisioning files + dashboards
# and reloads grafana-server. Existing TSDB / users / API keys are
# preserved (apt package owns /var/lib/grafana).
#
# Exit non-zero if Grafana doesn't bind :3000 within 60s.

set -euo pipefail

SELF="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
TEMPLATE_DIR="$REPO_DIR/templates/grafana"

if [[ "$EUID" -ne 0 ]]; then
    echo "$SELF: must run as root (sudo bash $0)" >&2
    exit 1
fi

if [[ ! -d "$TEMPLATE_DIR" ]]; then
    echo "$SELF: template dir not found at $TEMPLATE_DIR" >&2
    exit 1
fi

# --- Step 1: add Grafana apt repo (idempotent) ---
echo "[INFO] Configuring Grafana apt repo..."
KEYRING=/usr/share/keyrings/grafana.gpg
LIST=/etc/apt/sources.list.d/grafana.list

if [[ ! -s "$KEYRING" ]]; then
    apt-get install -y -qq software-properties-common wget gpg >/dev/null 2>&1 || true
    wget -qO - https://apt.grafana.com/gpg.key | gpg --dearmor > "$KEYRING"
    chmod 0644 "$KEYRING"
fi
echo "deb [signed-by=$KEYRING] https://apt.grafana.com stable main" > "$LIST"
echo "[OK] apt repo configured"

# --- Step 2: apt install ---
echo "[INFO] Installing grafana via apt..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq grafana
echo "[OK] grafana package installed"

# --- Step 3: deploy provisioning + dashboards ---
echo "[INFO] Deploying provisioning files..."
PROV_DS=/etc/grafana/provisioning/datasources
PROV_DASH=/etc/grafana/provisioning/dashboards
DASH_DIR=/etc/grafana/provisioning/dashboards/meshforge

install -o root -g grafana -m 0640 \
    "$TEMPLATE_DIR/provisioning/datasources/prometheus.yaml" \
    "$PROV_DS/prometheus.yaml"
install -o root -g grafana -m 0640 \
    "$TEMPLATE_DIR/provisioning/dashboards/meshforge.yaml" \
    "$PROV_DASH/meshforge.yaml"

mkdir -p "$DASH_DIR"
chown root:grafana "$DASH_DIR"
chmod 0750 "$DASH_DIR"
for f in "$TEMPLATE_DIR"/dashboards/*.json; do
    [[ -f "$f" ]] || continue
    install -o root -g grafana -m 0640 "$f" "$DASH_DIR/$(basename "$f")"
    echo "  deployed: $(basename "$f")"
done
echo "[OK] provisioning + dashboards deployed"

# --- Step 4: restart grafana-server ---
echo "[INFO] Restarting grafana-server..."
systemctl daemon-reload
systemctl enable --now grafana-server
systemctl restart grafana-server
echo "[OK] grafana-server enabled + started"

# --- Step 5: wait for ready ---
echo "[INFO] Waiting for /api/health on :3000 ..."
deadline=$((SECONDS + 60))
while [[ $SECONDS -lt $deadline ]]; do
    if curl -fsS http://localhost:3000/api/health >/dev/null 2>&1; then
        echo "[OK] Grafana is responding"
        break
    fi
    sleep 2
done

if ! curl -fsS http://localhost:3000/api/health >/dev/null 2>&1; then
    echo "[ERROR] Grafana did not respond within 60s" >&2
    journalctl -u grafana-server -n 30 --no-pager >&2
    exit 1
fi

echo
echo "Done. Grafana UI: http://$(hostname -I | awk '{print $1}'):3000/"
echo "  Default login: admin / admin (forces password change on first login)."
echo "  Dashboards: http://localhost:3000/dashboards (folder: MeshForge NOC)"
echo "  Datasource: Prometheus (auto-provisioned, http://localhost:9090)"
echo
echo "To add more dashboards:"
echo "  1. Drop *.json into /opt/meshforge/templates/grafana/dashboards/"
echo "  2. Commit + push + fleet_sync"
echo "  3. Re-run: sudo bash scripts/install_grafana.sh"

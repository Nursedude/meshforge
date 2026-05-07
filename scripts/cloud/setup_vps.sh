#!/usr/bin/env bash
# meshforge cloud :8808 — VPS one-shot setup
#
# Run on a fresh Ubuntu 24.04 LTS VPS with sudo access.
#
# Usage:
#   sudo bash setup_vps.sh <domain> [--push-user USER] [--meshforge-repo PATH]
#
# Example:
#   sudo bash setup_vps.sh noc.example.com --push-user meshforge
#
# What this does:
#   1. Installs Caddy, ufw, fail2ban, rsync, git
#   2. Configures UFW to allow SSH/HTTP/HTTPS only
#   3. Creates the rsync user + SSH dir (you paste the on-prem pubkey
#      into ~meshforge/.ssh/authorized_keys after this script finishes)
#   4. Clones the meshforge repo (read-only checkout for the static HTML)
#   5. Renders the Caddyfile from templates/cloud/Caddyfile.j2
#   6. Sets up /var/www/meshforge with index.html + a placeholder data.geojson
#   7. Reloads Caddy
#
# Idempotent — safe to re-run. Files are overwritten only if changed.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (use sudo)" >&2
    exit 1
fi

DOMAIN=""
PUSH_USER="meshforge"
REPO_DIR="/opt/meshforge"
REPO_URL="https://github.com/Nursedude/meshforge.git"
WEBROOT="/var/www/meshforge"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --push-user) PUSH_USER="$2"; shift 2 ;;
        --meshforge-repo) REPO_DIR="$2"; shift 2 ;;
        --repo-url) REPO_URL="$2"; shift 2 ;;
        --webroot) WEBROOT="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,30p' "$0"; exit 0 ;;
        -*)
            echo "unknown flag: $1" >&2; exit 1 ;;
        *)
            if [[ -z "$DOMAIN" ]]; then
                DOMAIN="$1"; shift
            else
                echo "unexpected argument: $1" >&2; exit 1
            fi
            ;;
    esac
done

if [[ -z "$DOMAIN" ]]; then
    echo "usage: $0 <domain> [flags]" >&2
    exit 1
fi

# Reject obvious typos rather than provision against a malformed name.
if [[ ! "$DOMAIN" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
    echo "domain doesn't look right: $DOMAIN" >&2
    exit 1
fi

log() { printf "[setup_vps] %s\n" "$*"; }

log "domain: $DOMAIN"
log "push user: $PUSH_USER"
log "webroot: $WEBROOT"

# 1. Packages
log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    caddy \
    ufw \
    fail2ban \
    rsync \
    git \
    python3 \
    curl \
    > /dev/null

# 2. Firewall
log "configuring ufw"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow OpenSSH > /dev/null
ufw allow http > /dev/null
ufw allow https > /dev/null
ufw --force enable > /dev/null

# 3. Push user (rsync target). No shell, only used to receive snapshots.
if ! id "$PUSH_USER" &>/dev/null; then
    log "creating push user $PUSH_USER"
    useradd --system --create-home --home-dir "/home/$PUSH_USER" \
        --shell /bin/bash "$PUSH_USER"
fi
mkdir -p "/home/$PUSH_USER/.ssh"
touch "/home/$PUSH_USER/.ssh/authorized_keys"
chown -R "$PUSH_USER:$PUSH_USER" "/home/$PUSH_USER/.ssh"
chmod 700 "/home/$PUSH_USER/.ssh"
chmod 600 "/home/$PUSH_USER/.ssh/authorized_keys"

# 4. Repo (read-only checkout for the static assets + Caddyfile template)
if [[ ! -d "$REPO_DIR/.git" ]]; then
    log "cloning meshforge into $REPO_DIR"
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
else
    log "updating existing meshforge clone in $REPO_DIR"
    git -C "$REPO_DIR" pull --ff-only --quiet || \
        log "  pull failed (network?); continuing with existing checkout"
fi

# 5. Webroot + permissions
log "setting up webroot $WEBROOT"
mkdir -p "$WEBROOT/.tmp"
cp "$REPO_DIR/web/cloud/index.html" "$WEBROOT/index.html"

# Initial placeholder data file so the page renders something even
# before the first push lands.
if [[ ! -f "$WEBROOT/data.geojson" ]]; then
    cat > "$WEBROOT/data.geojson" <<'EOF'
{"type":"FeatureCollection","features":[]}
EOF
fi
if [[ ! -f "$WEBROOT/meta.json" ]]; then
    cat > "$WEBROOT/meta.json" <<EOF
{"generated_at_epoch": 0, "feature_count": 0, "region": "hawaii", "source": "placeholder"}
EOF
fi

# Push user owns the webroot so rsync can write into it.
chown -R "$PUSH_USER:$PUSH_USER" "$WEBROOT"
# Caddy must be able to read.
chmod -R o+r "$WEBROOT"

# 6. Caddyfile — render from template using Python (avoid heavyweight Jinja
# dependency on the VPS).
log "rendering Caddyfile for $DOMAIN"
python3 - "$DOMAIN" "$WEBROOT" "$REPO_DIR/templates/cloud/Caddyfile.j2" \
    > /etc/caddy/Caddyfile <<'PYEOF'
import sys, re
domain, webroot, template_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(template_path) as f:
    src = f.read()
# Strip comment block at top (lines starting with `{# ... #}`).
src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
out = src.replace("{{ domain }}", domain).replace("{{ webroot }}", webroot)
sys.stdout.write(out)
PYEOF

# 7. Reload Caddy + smoke-test
log "validating Caddyfile"
if ! caddy validate --config /etc/caddy/Caddyfile > /dev/null 2>&1; then
    echo "Caddyfile validation failed; check /etc/caddy/Caddyfile" >&2
    exit 1
fi

log "reloading caddy"
systemctl reload caddy || systemctl restart caddy

log "done."
echo
echo "Next steps:"
echo "  1. Paste the on-prem push pubkey into:"
echo "       /home/$PUSH_USER/.ssh/authorized_keys"
echo "  2. Verify HTTPS:"
echo "       curl -I https://$DOMAIN/"
echo "  3. On the on-prem capture box (wherever meshforge-map runs), configure:"
echo "       /etc/default/meshforge-cloud-push  (CLOUD_HOST, CLOUD_USER, ...)"
echo "       systemctl --user enable --now meshforge-cloud-push.timer"
echo "       (or system-level if pushing as a system service)"
echo "  4. Watch the access log:"
echo "       tail -F /var/log/caddy/access.log"

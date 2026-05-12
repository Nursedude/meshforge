# MeshForge cloud :8808 deploy

Demo-grade public mesh map. Single VPS + Caddy + static files +
60-second push from on-prem.

> Full plan: `~/.claude/plans/cloud-8808-may17-deploy.md`
> Memory anchor: `project_cloud_8808_may17_demo`

## Architecture

```
the on-prem capture box (on-prem)                   cloud-vps (Hetzner CX22)
┌──────────────────────┐               ┌──────────────────────┐
│ meshforge-map :5000  │               │ Caddy + Let's Encrypt│
│  ↓ curl @60s         │               │  /          → index  │
│  /var/lib/meshforge/ │  rsync over   │  /data.geojson       │
│   cloud/data.geojson │  SSH (60s)    │  /meta.json          │
│   meta.json          │ ───────────▶  │  /healthz   → "ok"   │
└──────────────────────┘               └──────────────────────┘
```

No Python on the VPS. Caddy + static files only.

## Files in this deploy

| Path | Purpose |
|---|---|
| `scripts/cloud/setup_vps.sh` | One-shot VPS bootstrap (run as root on the VPS) |
| `scripts/cloud/push_snapshot.sh` | Snapshot + rsync (run on the on-prem capture box via timer) |
| `templates/cloud/Caddyfile.j2` | Parametrized Caddy config |
| `templates/cloud/meshforge-cloud-push.service` | Systemd oneshot |
| `templates/cloud/meshforge-cloud-push.timer` | 60s timer |
| `web/cloud/index.html` | The public demo page |

## Day 1 — VPS bootstrap

On the operator's workstation:

```bash
ssh-copy-id root@<VPS_IP>     # or have Hetzner inject the pubkey at provision time
```

On the VPS:

```bash
git clone https://github.com/Nursedude/meshforge.git /opt/meshforge
sudo bash /opt/meshforge/scripts/cloud/setup_vps.sh noc.example.com
```

What you'll see:
- ufw configured (22 / 80 / 443 only)
- Caddy installed, /etc/caddy/Caddyfile rendered for the domain
- TLS issued automatically by Let's Encrypt on first request
- /var/www/meshforge populated with index.html + placeholder data
- `meshforge` system user created; paste the on-prem pubkey into
  `/home/meshforge/.ssh/authorized_keys`

Verify:

```bash
curl -I https://<DOMAIN>/                      # HTTP/2 200
curl https://<DOMAIN>/healthz                  # "ok"
curl https://<DOMAIN>/data.geojson | jq        # placeholder FeatureCollection
```

## Day 2 — on-prem push setup

On the on-prem capture box (or whichever box runs meshforge-map):

```bash
# 1. Generate (or reuse) an SSH key for pushing.
ssh-keygen -t ed25519 -f ~/.ssh/meshforge_cloud_push -N ""
ssh-copy-id -i ~/.ssh/meshforge_cloud_push meshforge@<VPS_IP>

# 2. Configure the push.
sudo tee /etc/default/meshforge-cloud-push <<EOF
CLOUD_HOST=<DOMAIN>
CLOUD_USER=meshforge
CLOUD_WEBROOT=/var/www/meshforge
CLOUD_SSH_KEY=/home/<user>/.ssh/meshforge_cloud_push
LOCAL_MAP_URL=http://localhost:5000
REGION=hawaii
CACHE_DIR=/var/lib/meshforge/cloud
EOF

# 3. Install the systemd unit + timer.
sudo cp templates/cloud/meshforge-cloud-push.service /etc/systemd/system/
sudo cp templates/cloud/meshforge-cloud-push.timer   /etc/systemd/system/

# 4. Edit the unit's User= / Group= to match the operator account.
sudo systemctl edit --full meshforge-cloud-push.service
#   [Service]
#   User=wh6gxz
#   Group=wh6gxz

# 5. Enable + start the timer.
sudo systemctl daemon-reload
sudo systemctl enable --now meshforge-cloud-push.timer
sudo systemctl start meshforge-cloud-push.service     # fire one push immediately
```

Verify:

```bash
journalctl -u meshforge-cloud-push.service -n 20    # see the latest push log
systemctl list-timers meshforge-cloud-push.timer    # next firing time
curl https://<DOMAIN>/data.geojson | jq '.features | length'
curl https://<DOMAIN>/meta.json    | jq             # generated_at + count
```

## Day 2 (also) — public-position audit

Before pointing DNS publicly, confirm no node positions in the
captured stream are intentionally local-only:

```bash
# Quick scan of the snapshot for surprises.
curl -s http://localhost:5000/api/nodes/geojson?region=hawaii | \
    jq '.features[] | select(.properties.source_origin == "local_radio") | .properties.long_name' | \
    sort | uniq

# Cross-check against fleet boxes' explicit "private" markings.
# (No formal mechanism today — operator-eyeball audit.)
```

If any nodes need redaction, add a filter step to `push_snapshot.sh`
between the curl and the rsync — drop features whose `node_id` is
on a denylist.

## Operations

**See live access log on the VPS:**
```bash
ssh root@<VPS_IP> journalctl -u caddy -f
ssh root@<VPS_IP> tail -F /var/log/caddy/access.log
```

**Force a push:**
```bash
sudo systemctl start meshforge-cloud-push.service
```

**Pause the cloud demo (e.g. during a leak audit):**
```bash
sudo systemctl stop meshforge-cloud-push.timer
```

**Stop serving but keep VPS up (e.g. show 503 during maintenance):**
```bash
ssh root@<VPS_IP> systemctl stop caddy
```

**Tear down (post-talk decision):**
```bash
# On VPS
sudo systemctl disable --now caddy
sudo apt remove --purge caddy
sudo rm -rf /var/www/meshforge /home/meshforge

# On the on-prem capture box
sudo systemctl disable --now meshforge-cloud-push.timer
sudo rm /etc/systemd/system/meshforge-cloud-push.{service,timer}
sudo rm /etc/default/meshforge-cloud-push
```

## Risks + mitigations (from the plan file)

| Risk | Mitigation |
|---|---|
| DNS propagation lag pre-talk | Point DNS by May 14. |
| Let's Encrypt rate limit | Caddy retries; if first-issuance fails, retry after 1h or temporarily serve the demo at the raw VPS IP over HTTP until LE clears. No Cloudflare-proxy bridge — the demo uses NoIP DNS, not Cloudflare. |
| Public exposure of intentionally-local nodes | Day 2 audit before flipping DNS; denylist in push script if needed. |
| VPS down during talk | Mention `:5000` on-prem URL as fallback. |
| Snapshot push fails silently | Page shows "stale" stamp ≥ 5min; journald has full log. |

## What we are NOT shipping (post-May 17 decisions)

- Authentication / multi-tenant
- Cloud-side DB or live federation pull from NAT'd boxes
- Monitoring beyond Caddy access log
- High availability (single VPS, single region)
- Custom UI beyond the focused single-page map

These are gated on signal from the BIRC audience. Don't pre-build.

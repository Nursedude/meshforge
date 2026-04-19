# MeshForge systemd units

Unit templates here are **not installed automatically**. Per Issue #31
("no silent persistent system changes"), MeshForge never writes to
`/etc/systemd/system/` on its own — operators opt in explicitly.

## Available units

| File | Service |
|------|---------|
| `meshforge-gateway.service.in` | Gateway bridge (Meshtastic↔RNS/LXMF), runs 24/7 |

## Install

```bash
# From the repo root:
sudo scripts/install_gateway_service.sh          # current login user
sudo scripts/install_gateway_service.sh wh6gxz   # specific user
```

The installer:

1. Substitutes `@USER@`/`@HOME@` placeholders in the `.service.in` template
2. Writes to `/etc/systemd/system/meshforge-gateway.service`
3. Creates `~/.config/meshforge/` and `~/.cache/meshforge/` owned by that user
4. Runs `systemctl daemon-reload`, `enable`, and `start`
5. Tails the first few lines of the journal so you can see it come up

## Uninstall

```bash
sudo systemctl disable --now meshforge-gateway
sudo rm /etc/systemd/system/meshforge-gateway.service
sudo systemctl daemon-reload
```

## Placeholder reference

Template variables substituted at install time:

- `@USER@` — username the gateway runs as (`User=` and `Group=`)
- `@HOME@` — that user's home directory (used in `Environment=HOME=`
  and `ReadWritePaths=` for config/cache dirs)

These are NOT systemd specifiers — they are resolved once by the install
script. If you edit the live unit later, remember it's a rendered copy,
not the template.

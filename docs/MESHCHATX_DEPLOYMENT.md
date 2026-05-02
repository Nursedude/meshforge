# MeshChatX Deployment

Deployment recipe for MeshChatX — a third-party LXMF web chat client that
runs side-by-side with NomadNet on a MeshForge box. MeshChatX gives HAMs and
first-time RNS users a browser UI ("normal" UX) instead of the keyboard-driven
NomadNet TUI; the install path mirrors NomadNet's canonical pattern.

> Status: parity install (Phase 1). Field validation pending on the
> designated test-bed and operator-monitor boxes; remaining fleet
> hosts stay NomadNet-only until soak signal closes.

## What MeshChatX is

```
  ┌──────────────────┐       ┌────────────────────────┐
  │  Operator browser│──http─│  meshchatx.service     │
  │  127.0.0.1:8000  │       │  (systemd-user unit)   │
  │  or via SSH      │       │  Wrapper: refuses-loud │
  │  tunnel          │       │  on rpc_key mismatch   │
  └──────────────────┘       └──────────┬─────────────┘
                                        │ LXMF via RNS
                                        ▼
                             ┌────────────────────────┐
                             │ rnsd (Reticulum)       │
                             │   shared instance      │
                             │   rpc_key pinned       │
                             └──┬──────────┬──────────┘
                                │          │
                                ▼          ▼
                        Other RNS apps:  NomadNet (peer LXMF client)
                        ~/.nomadnetwork/         (separate identity)
```

Key facts:

* MeshChatX = `reticulum_meshchatx` PyPI-style wheel from
  `https://git.quad4.io/RNS-Things/MeshChatX/releases/latest`. Pure-Python
  (`*-py3-none-any.whl`), JS frontend pre-bundled.
* Storage: `~/.local/share/meshchatx/` (separate from NomadNet's
  `~/.nomadnetwork/`).
* LXMF identity is created on first launch — peers must add **both**
  hashes (NomadNet's and MeshChatX's) if you want them to reach you on
  either client.
* The MeshChatX daemon binds **127.0.0.1:8000**. Localhost-only is the
  safe default; use SSH tunnel for remote access.
* HTTPS is **off** by default (`--no-https`). Self-signed cert friction
  is a UX cost we pay only once we expose beyond localhost.

## Prerequisites

1. **rnsd running, rpc_key pinned** (Issue #41). Verify:

   ```bash
   sudo systemctl is-active rnsd
   grep '^rpc_key' /etc/reticulum/config
   # 64-char hex string expected
   ```

   If the rpc_key line is missing, run alignment first:

   ```bash
   sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize
   sudo systemctl restart rnsd
   ```

2. **pipx + python3 ≥ 3.11** (the wheel declares Python 3.11+).
   The installer apt-installs pipx itself if missing.

## Install

The canonical installer is idempotent — re-runs are no-ops on aligned boxes.

```bash
# Default: install if missing, refresh wrapper + unit if drifted
sudo bash /opt/meshforge/scripts/install_meshchatx.sh

# Read-only audit (CI / fleet-sync use this)
sudo bash /opt/meshforge/scripts/install_meshchatx.sh --check

# Force-rewrite wrapper + unit (no pipx churn)
sudo bash /opt/meshforge/scripts/install_meshchatx.sh --refresh

# pipx uninstall + reinstall (preserves identity by default)
sudo bash /opt/meshforge/scripts/install_meshchatx.sh --reinstall

# Wipe identity too (fresh LXMF hash)
sudo bash /opt/meshforge/scripts/install_meshchatx.sh --reinstall --wipe-identity
```

What the installer does:

1. `pipx install` of the latest wheel from `git.quad4.io/api/v1/repos/RNS-Things/MeshChatX/releases/latest`.
2. Writes `~/.config/meshforge/meshchatx_wrapper.sh` (refuses-loud on rpc_key
   mismatch — exit 87).
3. Renders `~/.config/systemd/user/meshchatx.service` from
   `templates/systemd/meshchatx-user.service`.
4. `loginctl enable-linger` + `systemctl --user enable --now meshchatx`.
5. Verifies the service is active.

## Open the web UI

| Box has a desktop?  | How                                                                          |
|---------------------|------------------------------------------------------------------------------|
| Yes (monitor on Pi) | Run `xdg-open http://127.0.0.1:8000/` on the box                             |
| No (headless Pi)    | `ssh -L 8000:localhost:8000 user@host` then visit `http://localhost:8000/`   |

The TUI menu does both for you: **MeshChatX > Open Web UI**.

## Day-to-day

```bash
# Service control (user scope)
systemctl --user status meshchatx
systemctl --user restart meshchatx
systemctl --user stop meshchatx

# Logs
journalctl --user -u meshchatx -f
journalctl --user -u meshchatx -n 100 --no-pager

# Audit current install state
sudo bash /opt/meshforge/scripts/install_meshchatx.sh --check
```

Or use the TUI: **MeshChatX > Service Control / View Logs / Run install audit**.

## Coexistence with NomadNet

Both clients can run concurrently on the same shared rnsd. Each owns a separate
LXMF identity (separate storage dirs, separate hashes). The
`_lxmf_utils.LXMF_CLIENT_NAMES` set knows about both, so the TUI's
exclusivity check correctly identifies them as siblings rather than
collisions.

If you want a peer to reach you on **either** client, share **both** LXMF
hashes. Run the TUI's status panel on each client to read the hash:

* NomadNet: **NomadNet > Show NomadNet Identities**
* MeshChatX: visible in the web UI's settings panel (look under "Identity"
  after first launch)

## Why MeshChatX *and* NomadNet (not one-or-the-other)

MeshChatX is the "friendly" first-mile — point-and-click in a browser, file
transfer + group chat features that NomadNet doesn't expose. NomadNet remains
the right tool for terminal-savvy operators (low-bandwidth, scriptable,
runs over SSH cleanly).

We don't replace NomadNet because:

1. NomadNet's micron page browser is a unique RNS-native capability MeshChatX
   doesn't replicate.
2. The fleet has live operators who already trust the NomadNet workflow —
   replacing it would be an unnecessary disruption.
3. Coexistence is cheap (two separate identities, one shared rnsd).

## Troubleshooting

### "AuthenticationError: digest sent was rejected" in the journal

The Issue #41 trap. The wrapper should refuse-loud (exit 87) on this; if
you see it as a runtime error from MeshChatX itself, rpc_key is unpinned
or has drifted.

```bash
grep '^rpc_key' /etc/reticulum/config
# missing? → run alignment:
sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize
sudo systemctl restart rnsd
systemctl --user restart meshchatx
```

### Service is `active` but `:8000` is not bound

The daemon is starting but hasn't bound the socket yet. Web servers need a
few seconds to finish startup; refresh after 5-10 seconds. If it persists,
check the journal:

```bash
journalctl --user -u meshchatx -n 50 --no-pager
```

Common causes: another process is already on `:8000`, the wrapper exited 87
(rpc_key), or pipx-installed `meshchatx` is missing dependencies.

### Service in `failed` state with `NRestarts: 5`

`StartLimitBurst=5` parked the unit after 5 retries in 300s. Almost always
the rpc_key path. Reset and try again:

```bash
systemctl --user reset-failed meshchatx
sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize
systemctl --user restart meshchatx
```

### "No wheel asset found in latest release"

The installer's gitea API parse failed. Either:

1. Network is down — the installer hits `git.quad4.io` directly.
2. Upstream changed asset naming (e.g. arch-specific wheels). Run with
   `bash -x` to see the API response, then file an issue with the JSON.

## Uninstall

The TUI's **MeshChatX > Uninstall** action stops + disables the service, removes
the unit file, and `pipx uninstall reticulum-meshchatx`. It does **not** delete
the storage directory or wrapper — to clean those, re-run the installer with
`--reinstall --wipe-identity`, or remove manually:

```bash
rm -rf ~/.local/share/meshchatx
rm -f ~/.config/meshforge/meshchatx_wrapper.sh
```

## Rollout sequencing

The opt-in feature flag (`meshchatx: False` on every profile) keeps this
dormant until an operator decides to enable it. Recommended order:

1. **Test-bed Pi** (full profile, designated for staged rollouts) — install
   first, run `--check`, soak 24-48h watching `journalctl --user -u meshchatx`
   for restart-loop signal.
2. **Operator-monitor Pi** (the box with a physical display) — second
   install. The on-box browser is the actual "friendly UX" demo
   surface, so this box validates the use case the integration was
   built for.
3. **Remaining full-profile boxes** — opt-in only after the first two
   show clean soak.
4. **Gateway profile box(es)** — defer until last; the gateway already
   has a heavy load profile and adding a second LXMF client there
   should follow rather than lead.

Per-fleet rollout state belongs in operator memory (e.g.
`project_meshchatx_rollout.md`) rather than this repo doc — that
prevents operator-specific hostnames from leaking through MF014 and
keeps the doc generic across boxes.

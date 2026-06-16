---
name: MeshForge
description: >
  MeshForge NOC (Network Operations Center) assistant for LoRa mesh network development.
  Handles Meshtastic and RNS (Reticulum) network operations, configuration, debugging, and development.

  Use when working with: (1) Meshtasticd configuration and service management, (2) RNS/Reticulum
  network setup and bridging, (3) LoRa radio configuration (presets, frequencies, regions),
  (4) MeshForge TUI development, (5) Gateway bridge between Meshtastic and RNS,
  (6) RF calculations and link budgets, (7) Node discovery and monitoring.

  Triggers: meshtastic, meshtasticd, rnsd, reticulum, lora, meshforge, gateway, rnode, nomadnet
---

# MeshForge Development Assistant

> **Scope note (2026-06-09):** CLAUDE.md, `.claude/rules/security.md`, and
> `.claude/foundations/persistent_issues.md` are auto-loaded into every session —
> do NOT restate them here. This skill carries only operational reference that
> lives nowhere else. Version: read `src/__version__.py`, never hardcode it here
> (a stale copy sat at 0.5.5-beta while main was 0.6.1-beta). Handler surface:
> read `capability_index.md` (next to this file) — auto-generated from the live
> `get_all_handlers()` registry and test-pinned, so the count never goes stale
> by hand again (it drifted 60→64→96 when hardcoded).

## Key Ports

| Service | Port | Protocol | Notes |
|---------|------|----------|-------|
| meshtasticd TCP API | 4403 | TCP | PhoneAPI — single consumer (#17); never probe casually (#75/#76) |
| meshtasticd Web UI | 9443 | HTTPS | guarded against HAT-overlay port theft (#58) |
| RNS shared instance | 37428 | TCP | legacy port; live IPC is the AF_UNIX `@rns/<instance>` socket — owner must be rnsd (#69): `sudo ss -xnpl \| grep "@rns/"` |
| MeshForge map | 5000 | HTTP | federator on VolcanoAI; `/api/status` is the probe surface |
| HamClock Live / API | 8081 / 8082 | HTTP | |
| MQTT | 1883 | TCP | per-box broker islands — no fleet consensus |

## Service Quick Facts

- `rnsd`, `meshtasticd`, `meshforge-map` are **system** services (`sudo systemctl …`).
  `meshforge-mini-dudeai` and `nomadnet` are **user** units (`systemctl --user …`,
  logs via `journalctl _SYSTEMD_USER_UNIT=<unit>`).
- Service state ONLY via `check_service()` from `utils.service_check` (MF008).
- After editing `/etc/reticulum/config`: `sudo systemctl restart rnsd` (authkey
  derives from identity, #37) — then restart RNS-using services, and never
  rapid-cycle rnsd fleet-wide (#69 race window).

## TUI Handler Pattern

Each menu action is a self-contained handler in `src/launcher_tui/handlers/`,
dispatched by `handler_registry.py`. Context arrives via `set_context()` (stored
as `self.ctx`); `execute()` receives the selected action **tag**, not the context:

```python
from handler_protocol import BaseHandler  # bare import: src/launcher_tui on path

class MyHandler(BaseHandler):
    handler_id = "my_handler"
    menu_section = "system"           # which menu it appears under

    def menu_items(self):             # (tag, label, feature_flag_or_None)
        return [("mything", "My Thing — does the thing", None)]

    def execute(self, action: str):   # action == the selected tag
        self.ctx.report_action(ok, "Done", "It worked", "Failed", "It did not")
```

New handlers must be appended in `handlers/__init__.py:get_all_handlers()` or
they are silently dead UI (`TestHandlerReachability` guards this). The full
command surface — every section, tag, label, and feature flag — is in
**`capability_index.md`** (next to this file); grep it to answer "can the TUI
do X?", then open the handler. Regenerate it after touching any handler:
`python3 scripts/gen_capability_index.py`.

## Launch & Verify

```bash
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools

python3 scripts/lint.py --all           # Blocking gate (MF rules)
python3 -m pytest tests/ -v             # Full suite
python3 scripts/parity_check.py         # MeshForge<->MeshAnchor drift
python3 scripts/db_audit.py             # DBSpec inventory (MF013)
```

For honest test results in long sessions, redirect to a file and check the exit
code explicitly (`pytest … 1>/tmp/out.log 2>&1; echo EXIT=$?`) — never trust a
`| head`/`| tail`-truncated stream.

## For Detailed Reference

- Known issues & fixes: `.claude/foundations/persistent_issues.md` (auto-loaded)
- Architecture: `.claude/foundations/domain_architecture.md`
- Full doc index: `.claude/INDEX.md`
- Research deep dives: `.claude/research/`
- Knowledge Base API: `src/utils/knowledge_base.py`

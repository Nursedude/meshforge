# Environment Variables

> **Configuration SSOT is NOT environment variables.** Persistent settings live
> in `~/.config/meshforge/*.json` managed by `SettingsManager`
> (`src/utils/common.py`). There is **no `.env` file mechanism** — a dead
> `utils/config.py` dotenv loader was removed 2026-07-03 after an audit found
> it had zero importers. If you were setting values in a `.env` file, they
> were never read; use the TUI settings menus or the JSON config files.
>
> Environment variables are used only as **runtime overrides and daemon
> knobs** — things systemd drop-ins, cron lines, and test harnesses set.
> This page lists the deliberate ones. Fleet shell scripts additionally honor
> per-script `${VAR:-default}` overrides documented in each script's header.

## AI assistant

| Variable | Read by | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | `utils/claude_assistant.py`, `utils/audit.py`, TUI AI tools | Enables PRO-tier AI features (assistant, auto-review, diagnostics). Absent = Standalone tier. |

## Gateway / RNS reliability knobs

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MESHFORGE_RNS_PROBE_TIMEOUT` | `utils/rns_init.py` | Bounded AF_UNIX probe timeout in the `open_reticulum()` chokepoint (#68). |
| `MESHFORGE_RNS_WAIT_FOR_RNSD_TIMEOUT` | `utils/rns_init.py` | How long the chokepoint waits for an enabled rnsd to bind before proceeding (#69 boot race). |
| `MESHFORGE_LAB_RNS_INIT_TIMEOUT` | `utils/rns_init.py` | Lab-harness override for RNS init timeout. |
| `MESHFORGE_BOUNDED_RPC_NO_EXIT` | `gateway/bounded_rpc.py` | Disables the wedged-RPC `os._exit(2)` backstop (#57) — test/dev only. |
| `MESHFORGE_BRIDGE_RX_STALE_SEC` | `gateway/_rns_bridge_aux.py` | RX-staleness threshold for the bridge health monitor. |

## Oracle (mesh chat assistant)

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MESHFORGE_ORACLE_ENABLED` | gateway + monitoring handlers | Master switch for the mesh oracle. |
| `MESHFORGE_ORACLE_CHANNELS` | `oracle_phoneapi_tap.py`, `meshtastic_handler.py` | Channels the oracle listens/answers on. |
| `MESHFORGE_ORACLE_PHONEAPI_TAP` | `oracle_phoneapi_tap.py` | Enables the PhoneAPI tap ingest path. |

## Observability / state-path overrides

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MESHFORGE_DELIVERY_COUNTERS_DB` | `gateway/delivery_counters.py` | Delivery-counters DB path override (#63). |
| `MESHFORGE_CONTENT_ID_VIEW_STATE` | `gateway/delivery_counters.py` | Confirmation-view state path (#74). |
| `CALIBRATION_LEDGER_PATH` | `utils/calibration_ledger.py` | Calibration ledger location override. |
| `HONEST_VERDICT_PATH` | `mini_dudeai/warmstart.py` | honest_status verdict file consumed by the warm brief. |
| `MESHFORGE_REPO` | `mini_dudeai/warmstart.py` | Repo root override for warm-brief pointers. |
| `FLEET_HOSTS` | `utils/fleet_dup_collector.py` | Override for the fleet host list (`~/.config/meshforge/fleet_hosts`). |
| `MESHFORGE_EDITION` | `core/edition.py` | Edition override (`pro` / `amateur` / `io`); wins over `edition.json` + marker files. |

## mini-dudeai

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MINI_DUDEAI_HOME` | `mini_dudeai/_util.py` | State-directory override (rules, history, briefs). |
| `MINI_DUDEAI_NTFY_TOPIC` | fleet preset, standalone | ntfy topic for pages. |
| `MINI_DUDEAI_NATS_SERVER` / `MINI_DUDEAI_NATS_TOKEN` | `nats_client.py`, standalone | NATS transport endpoint + auth. |
| `MINI_DUDEAI_OLLAMA_URL` / `MINI_DUDEAI_OLLAMA_MODEL` | `chat.py`, engine | Local-LLM chat backend (de-prioritized; Claude-first). |
| `MINI_DUDEAI_CLAW_DEVICE` / `MINI_DUDEAI_CLAW_SENSORS` / `MINI_DUDEAI_CLAW_TEMP_THRESHOLD` | standalone (dude-claw) | ESP32 claw serial device + sensor config. |
| `MINI_DUDEAI_ENABLE_BOOT_HEALTH` / `MINI_DUDEAI_ENABLE_DIGEST` / `MINI_DUDEAI_ENABLE_FEDERATION` | `presets/meshforge_fleet.py` | Feature toggles for the fleet preset. |
| `MINI_DUDEAI_CADENCE_MODEL` | `scripts/mini_cadence_launch.sh` | Model used for the cadence PROPOSE run (default opus). |
| `MINI_DUDEAI_TIER_SLO_URL` | `scripts/claw_metrics_push.py` | `/fleet/slo` URL of the box running the frontier cadence cron — enables the claw OLED brain-tier glyph (F/L/R via `display_tier`, firmware ≥0.4.0+dudeclaw.15). Unset = tier feed disabled (glass decays to SOLO). |
| `MINI_CADENCE_LOCAL_TIMEOUT_S` | `scripts/mini_cadence_launch.sh` | Bound on the W1 local-tier triage fallback (`mini_dudeai.cadence_fallback`, default 600 s — sized for qwen3-4B at ~30 s/entry × the 12-entry fed cap). The fallback triages the proposed-delta backlog when the frontier session fails; it never ratifies and never turns the verdict OK. |

## Fleet scripts (cron / systemd context)

| Variable | Read by | Purpose |
|----------|---------|---------|
| `MESHFORGE_NTFY_TOPIC` / `MESHFORGE_NTFY_TOKEN` | `scripts/fleet_ntfy_*.sh` | Override the ntfy topic/token config files (`~/.config/fleet_push_topic` / `_token`). |
| `MESHFORGE_ACK_STATE` / `MESHFORGE_ACK_PING_INTERVAL_S` | `scripts/fleet_ntfy_ack.sh` | Weekly tap-to-ack state path + cadence. |
| `MESHFORGE_LOOPBACK_INTERVAL_S` | `scripts/fleet_ntfy_loopback.sh` | Loopback probe cadence. |
| `CRON_VERDICT_LOG` | `scripts/cron_verdict.sh` | Verdict log path (default `~/cron_verdicts.log`). |
| `FLEET_EMAIL_TO` / `FLEET_EMAIL_FROM` / `FLEET_EMAIL_CREDS` | `scripts/fleet_alert_email.sh` | Phase-1 email backbone endpoints (creds via file, never committed). |

## Standard/system variables

`SUDO_USER`, `DISPLAY`, `WAYLAND_DISPLAY`, `SSH_*`, `XDG_*`, `TERM`, `LANG`
are read for **environment detection** (real-user home resolution per MF001,
headless detection, emoji capability) — they are not MeshForge knobs.

## Maintenance

When adding a new env var to `src/`, add a row here. Enumerate current
ground truth with:

```bash
grep -rnoE "(os\.environ(\.get)?|os\.getenv)\(['\"][A-Z][A-Z0-9_]+" src/
```

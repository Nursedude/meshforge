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
> Router→primer upgrade 2026-07-14 (cross-model arc): domain model, fleet facts,
> harness layer, and the MF lint index now live here so a smaller model reading
> ONE skill sees the whole operating picture.

## The Domain in One Screen

Two incompatible mesh ecosystems, one NOC bridging them:

- **Meshtastic leg** — `meshtasticd` (system service) owns the LoRa radio.
  Surfaces: PhoneAPI TCP `:4403` (**single-consumer** — a second reader eats
  packets, #17-class), Web UI `:9443`, MQTT JSON uplink (per-box broker).
  TX goes through `send_text_direct()` (`meshtastic_protobuf_client.py`);
  connections through `MeshtasticConnection` (`connection_manager.py`). Never
  read `/api/v1/fromradio` outside the one owner.
- **RNS leg** — `rnsd` (system service) is the ONE RNS host per box (shared
  instance, AF_UNIX `@rns/<instance>`); every app is a client of it. LXMF is
  the messaging layer; NomadNet the human client. rns/lxmf are **MeshForge-owned
  forks** pinned by `# MF-FORK-PIN` in `requirements/rns.txt` (`+mf.N` markers).
  ALL construction goes through `open_reticulum()` (`utils/rns_init.py`) — the
  guarded chokepoint (MF019).
- **Gateway** — `src/gateway/rns_bridge.py` bridges the two: mesh text →
  LXMF (long_name in subject, `[Mesh:xxxx]` prefix) and LXMF → mesh
  (`@id`/`@short_name` directed downlink). `CanonicalMessage`
  (`canonical_message.py`) is the shared contract with MeshAnchor — byte-locked
  twin file. SQLite retry queue in `message_queue.py`; delivery honesty via
  `compute_confirmation_view` (#74 — never cross-population rates).
- **Map/NOC** — `meshforge-map` serves `:5000`; collectors feed it; boxes
  federate via each other's `/api/status`. Serving must never block on
  collection (response byte-caches, #70/#71).
- **Observability spine** — watchdog probes → `/var/lib/meshforge/watchdog.json`
  → mini-dudeai (user unit, 30s rule loop) → ntfy pages. Silence is a failure
  mode: cron verdicts (`cron_verdict.sh` → #78 probe), synth soak, tracer RTT.

**Where truth lives**: journals + `/api/status` + scripts — never synthesis.
Radio RX truth = `grep 'Received text msg'` in the meshtasticd journal (json
greps miss `via_mqtt`, #75 trap). Service state = `check_service()` only.

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

## Fleet Facts a Session Must Not Miss

- Roster (2026-07-14): manager **VolcanoAI** + `moc, moc1, moc2, moc3, moc5,
  kiai, meshanchor-server` — live list in `~/.config/meshforge/fleet_hosts`;
  posture pane: `PYTHONPATH=src python3 -m mini_dudeai.rollup` (or `/warmstart`).
- **Two-preset fleet, deliberately**: LONG_FAST/ch20 everywhere + moc3 on
  SHORT_TURBO/ch8; moc runs the cross-preset bridge. Preset "drift" checks must
  best-match over BOTH templates.
- **kiai** sits behind the OpenWrt router at the AREDN site — reach it via
  `ssh kiai` (rtun ProxyCommand); no LAN-routable HTTP until the dstnat lands.
- **meshanchor-server** is the MeshAnchor-owned box: MA services + a read-only
  `/opt/meshforge` checkout for the lab/mini user units. No MF watchdog there —
  mini runs `MINI_DUDEAI_ENABLE_WATCHDOG=0` (declared absent ≠ error).
- ⚠️ **No multi-agent fan-outs on VolcanoAI** (kernel-lockup class, 2/2 froze
  the box) — one sequential agent at a time; no /deep-research fan-out here.
- fleet_sync restarts gateways on `^src/` diffs — **never during a soak**; the
  no-restart deploy is targeted `git pull --ff-only` per box. Always pull every
  box after `git push` (divergence failure mode).
- meshtasticd is **deliberately apt-held** fleet-wide (operator's roll call
  owns upgrades); "update available" truth = apt candidate, never GitHub tags (#83).

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

## Cold-Start Ground Truth & the Harness Layer

Any model, any session — these commands re-derive state; never trust a summary:

```bash
bash scripts/honest_status.sh            # THE check of record (exit 0 = green; 2/UNKNOWN ≠ pass)
bash scripts/harness_audit.sh            # is the second-brain spine itself wired? (9 legs)
PYTHONPATH=src python3 -m mini_dudeai.rollup   # all-boxes posture, freshness re-derived now
git config core.hooksPath                # must say .githooks (found OFF once — #29 spine dormant)
```

The harness is the portability layer, not the model (`.claude/rules/model_advisor.md`):

- **Claim gate** (Stop hook) blocks an unevidenced "all green" once and shows
  re-derived truth; **calibration ledger** (`~/calibration_ledger.jsonl`) records
  VERIFIED claims per model so drift is measurable. Tag every completion claim
  VERIFIED / BELIEVED / UNKNOWN (`.claude/rules/calibrated_claims.md`, auto-loaded).
- **Tier ladder**: frontier (rationed — adversarial review, novel design,
  forensics) / Opus day-to-day / fast mechanical / tier-L local LLM
  (PROPOSE-only, eval-gated weekly) / tier-R rules+probes (always-on). Review- or
  design-shaped work on a smaller model → queue the range in
  `.claude/audits/review_provenance.md`, never fake the pass.
- **Gates never scale down with the model** — smaller model = lean on lint,
  regression guards, and honest_status HARDER.
- A resolved incident compiles to THREE artifacts: probe/rule (R) +
  persistent_issues entry (R) + tier-L eval case in `evals/local_brain/`
  (`.claude/rules/honest_failure_modes.md` point 10).
- Full map: `.claude/foundations/harness_map.md` (hooks → claim-gate → ledger →
  mini → probes → paging; truth-source oracle table; SPOF list).

## Lint Rules (MF-series) — the executable house style

Authoritative one-liners live in the `scripts/lint.py` header docstring — re-read
it when touching lint-adjacent code; new rules land there first. Grouped digest:

- **Paths/env**: MF001 no `Path.home()` (sudo breaks it). MF014 no
  operator-specific values in src/templates/scripts/docs. MF015 no LAN IPs in
  published docs (security.md).
- **Subprocess/safety**: MF002 no `shell=True` · MF003 no bare `except:` ·
  MF004 subprocess needs `timeout=` · MF010 daemon loops use
  `_stop_event.wait()` not `time.sleep()`.
- **Meshtastic/RNS contracts**: MF007 no direct `TCPInterface()` · MF008 service
  state via `check_service()` only · MF009 `RNS.Reticulum()` needs `configdir=` ·
  MF019 RNS construction ONLY via `open_reticulum()` chokepoint · MF023 map
  collector interface creation only via the bounded helper.
- **Honesty/structure**: MF006 no `safe_import` for first-party · MF011 repair
  logic placement · MF013 SQLite via `connect_tuned()` + `DBSpec` · MF016 test
  patch seams (`utils.paths`, not `src.utils.paths`) · MF018 no TUI
  shell-escapes (in-domain principle) · MF020 never discard
  `apply_config_and_restart()` result · MF021 mini-dudeai is observation-only ·
  MF022 installers route pip/apt through `install_common.sh`.
- **Meta/ratchets**: MF012 persistent_issues.md ≤40k chars · MF017 systemd
  ReadWritePaths drift · MF024 version SSOT 4-way consistency · MF025 src files
  ≤1,500 lines (frozen shrink-only baseline).

## For Detailed Reference

- Known issues & fixes: `.claude/foundations/persistent_issues.md` (auto-loaded)
- Architecture: `.claude/foundations/domain_architecture.md`
- Full doc index: `.claude/INDEX.md`
- Research deep dives: `.claude/research/`
- Knowledge Base API: `src/utils/knowledge_base.py`

## Searching the Lore (offline oracle)

To answer "has this fleet seen X before?" search the WHOLE corpus (persistent
issues + archive, foundations, rules, research, docs, memory topic files) in
one deterministic shot — no INDEX to consult, no grep guessing:

```bash
PYTHONPATH=src python3 -m mini_dudeai.offline_oracle --retrieve-only "<question>"
```

BM25-ranked excerpts with paths land in ~1s. Drop `--retrieve-only` for a
cited local-LLM answer when Ollama is reachable (tier L; works with the
frontier away). In-app: TUI → dashboard → "offline oracle". Answers are
citation-gated — an answer citing nothing it was shown degrades to the
retrieval list, honestly.

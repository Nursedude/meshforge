# TUI Honesty + Domain Arc — multi-session plan (opened 2026-06-08)

> **Spine (one principle, three threads):** *the app tells the truth — it works,
> or it says exactly how it didn't.* No silent failure, no false info, and the
> fleet architecture reproducible from code, not tribal memory.
>
> This is the cross-session anchor. Each session updates the checkboxes and the
> session-handoff memory so the next session (and mini) warm-start on the exact
> state. Origin: `/warmstart` 2026-06-08 — "quality review TUI functionality …
> many things were built but not actually working as prescribed."

---

## Thread 1 — TUI honest-signal burn-down (the audit worklist)

A 2026-06-08 multi-agent audit of all 95 TUI handlers + adversarial verification
confirmed **31 honest-signal defects** (12 high). Dominant shape (19/31): *a
result-bearing call's return is discarded, then a hardcoded success dialog fires
regardless* — the #74–#77 family, now in the TUI. Full audit JSON:
`/tmp/.../tasks/wzje4jyat.output` (regenerate via the audit workflow if gone).

**Sequencing principle:** life-safety first; build the guardrail early so later
slices are mechanical applications with a test that prevents recurrence.

**MeshAnchor parity:** the sister app is a near-mirror TUI fork and carries the
same defect class. S0+S1+S2 ported 2026-06-08 (`f90ecbb4`); **S3 ported
(`a32122f5`)** — all 4 sites byte-identical (only "-> MeshAnchor" string differs);
**S4 ported (`fb3e22d7`)** — parity sites adapt (svc name `meshanchor`, no
`extensions.py` in MA). All parity_check in sync, meshanchor-server pulled.
**Port each future slice (S5-S7) to MeshAnchor too** — read MA's
`src/launcher_tui/handlers/` (line numbers drift; `rns_diagnostics`/`service_menu`
differ; no `meshtasticd_config`/`extensions` there), verify lint+tests,
commit+push origin, pull on meshanchor-server.

**MA-divergent surfaces (the audit the plan calls for, partially triggered):**
S4's new guard surfaced two MA-native raw `is-active` reads with NO MeshForge
twin — `meshcore._daemon_status_summary` (subtitle prints literal state, keeps
`activating` granularity) and `ai_tools._get_map_service_status` (returncode tells
in-process-TUI from systemd unit). Both are TRUTHFUL (not honesty defects) and
preserve info `check_service` drops → allowlisted + commented, not converted. A
fuller honest-signal audit of MA's divergent handlers (MeshCore menu, MeshCore
gateway handler, ActiveHealthProbe) remains a worthwhile fan-out.

### ✅ S0 — shared guardrail (DONE 2026-06-08)
- [x] `TUIContext.report_action(ok, success_title, success_body, fail_title, fail_body)` — confirm-or-honest dialog primitive (`handler_protocol.py`).
- [x] **Lint MF020** — `apply_config_and_restart()` return discarded (bare statement) in `launcher_tui/handlers/` (`scripts/lint.py`). Zero-false-positive subset of the broad pattern; **extend later** to `*_service` + `.save()` correlated-with-success-string.
- [x] `tests/test_honest_signal_guards.py` (9 tests): MF020 regression scan + `report_action` unit tests + MF020 rule fires/quiet tests. **This is the home future slices add their guards to.**

### ✅ S1 — EMCOMM + tactical send-path honesty (life-safety, DONE 2026-06-08)
- [x] `emergency_mode.py` `_emcomm_broadcast`(129) + `_emcomm_direct`(188) — capture returncode, "Message sent." only on rc==0 (mirrors `_emcomm_sos_beacon`).
- [x] `tactical_ops.py` `_send_tactical_message`(519) — relabelled "{type} Sent" → "{type} ENCODED (recorded locally — NOT transmitted)"; explicit no-transmit note. (A real OTA transmit action is a **Thread-2 feature**, not a bug-fix.)

### ✅ S2 — apply_config_and_restart() discarded-return cluster (DONE 2026-06-08)
All 8 sites bound `ok, msg = …` + honest branch (5 audit-confirmed + 3 same-shape MF020 caught):
- [x] `first_run.py`:458, :805, :850 (#58 Port:443 makes USB path a real failure) → `report_action`
- [x] `updates.py`:221 → `report_action` + honest `_HAS_SERVICE_CHECK is False` branch (no implied restart)
- [x] `service_menu.py`:325 → `report_action`
- [x] `meshtasticd_config.py`:195 — module helper now `return ok` (activated the **dead error branch** in `meshtasticd_radio.py:510`)
- [x] `startup_health.py`:105 — honest restart note (in-domain, no shell-escape)
- [x] `rns_diagnostics.py`:704 — gate verdict on `restart_ok and new_user==target_user`

### ✅ S3 — RNS service-lifecycle false-success + remediation (DONE 2026-06-08)
- [x] `rns_interfaces.py` `_fix_rns_ownership`(489) — captures `stop_ok,stop_msg`/`start_ok,start_msg`; "rnsd restarted and permissions re-applied" now gated on `start_ok`, else "FAILED to restart rnsd: {msg}" + in-domain recovery (re-run / Diagnose RNS, no shell). Guard: `TestRnsRestartReturnChecked` (scoped `_BARE_SVC` scan of rns_interfaces.py — handler-wide would FP on ~10 benign/later-slice sites). **HIGH — DONE 2026-06-08**
- [x] `_rns_diagnostics_engine.py` `diagnose_rns_port_conflict`(627) — binds `start_ok,start_msg`; now gates "Done." on `handler._wait_for_rns_shared_instance(10)` (rnsd started ≠ conflict resolved), honest failure on start-fail or instance-never-up + in-domain recovery. Guard: `TestPortConflictVerifyBeforeDone` (behavioral — line 459's sibling `stop_service` is a legit stop-then-pkill, so no file scan). **DONE 2026-06-08**
- [x] `daemon.py` `_daemon_stop`(162) — title now on `result.returncode`: "Stop Daemon" on rc==0, else "Stop Failed" + stderr (was always the neutral "Stop Daemon"). **DONE 2026-06-08**
- [x] MF008 RNS sites: `_rns_interface_mgr.py`:96 (+import) and `rns_diagnostics.py`:504 → `check_service('…').available` (raw `systemctl is-active` removed). MF008 *coverage* extension to handlers/** stays S4. **DONE 2026-06-08**

### ✅ S4 — service-state SSOT consolidation (DONE 2026-06-08)
- [x] `service_menu.py` `_show_all_service_status` + `quick_actions.py` `_qa_service_status` — `meshforge` green-either-way now branches on `check_service().available`/`ServiceState.FAILED/DEGRADED` (adds the missing FAILED dot); quick_actions' **dead `failed` branch revived** (it used `check_systemd_service`→active/inactive only, so a FAILED svc read as "inactive"). **DONE**
- [x] `dashboard.py` `_service_status_display` fallback — raw `systemctl is-active` → `check_service()` + `ServiceState` (FAILED now shows red, mirrors the primary `ServiceRunState` branch). **DONE**
- [x] `extensions.py` `_ma_is_running` → `check_service(self._MA_SERVICE).available`. **DONE**
- [x] **Guardrail:** extended `TestServiceCheckContract.test_no_multiline_systemctl_state_reads_in_handlers` (regression test, NOT a lint change — pre-commit runs lint at `--severity error` so MF008-warning wouldn't gate, and `test_regression_guards.py` IS pre-commit-run; the test also ports cleanly vs the diverged MeshForge/MeshAnchor linters). Catches the list-literal `['systemctl','is-active',…]` form the single-line regex/MF008 both miss. **DONE**
- ⚠️ **`_rns_repair.py:408` kept raw + allowlisted** (`MULTILINE_READ_ALLOW`): its wait-loop must distinguish `'activating'` (keep waiting) from `'inactive'/'failed'` (crash) — `check_service` collapses `activating`→`NOT_RUNNING` (lossy), so converting it would falsely declare a crash mid-start. Documented at the call site. NOT in the plan's named sites — caught by the blast-radius pass.

### ✅ S5 — persisted-write + meshtasticd-apply honesty (DONE 2026-06-08)
- [x] `dual_radio_failover.py` — `_deploy_secondary` headline gated on `service_ok` (was unconditional "deployed successfully!"); `_toggle_failover` + 3 `_edit_config_field` sites gate on `cfg.save()` (returns bool, True/False never raises) — failed write now surfaces, doesn't read as "Toggled"/"enabled". **HIGH — DONE**
- [x] `channel_config.py` `_set_channel_role` — branches on `result.success`; failure is a real "Role Change Failed" error, not the soft "may require restart" note. **HIGH — DONE**
- [x] `meshtasticd_radio.py` `_apply_radio_preset` — `fully_ok` now includes `verified`; unconfirmed readback shows "Partial Success" + " [UNVERIFIED — readback not confirmed]" instead of plain "Success". **DONE**
- [x] `propagation.py` `_toggle_rest_source` — BOTH branches reflect `data['persisted']` (configure_source returns `.ok` even when the disk write fails). **DONE**
- [x] `_nomadnet_install_utils.py` `_upgrade_nomadnet` — title "Upgrade Complete" only when `rns_upgraded` (returncode==0), else "Upgrade Incomplete". **DONE**
- [x] **Guardrail:** `TestSaveReturnChecked.test_no_bare_cfg_save_in_handlers` (regression test, NOT an MF020 lint change — same reasoning as S4: MF020 is warning-only at pre-commit `--severity error`, test_regression_guards IS pre-commit-run, and the test ports cleanly across the diverged linters). Scoped to the zero-FP `cfg.save()` GatewayConfig idiom. **DONE**
- ⚠️ **Deferred broader `.save()` sweep (blast-radius pass):** `SettingsManager.save()` ALSO returns bool — `automation.py` (`settings.save()` ×9) + `first_run.py` (×1) + `meshcore.py` (`config.save()` ×2, GatewayConfig) all discard a bool. NOT fixed (out of S5's named scope, ~12 sites); many are benign silent-saves with no false-success dialog. A focused audit separating true false-success from benign silent-save is a worthwhile follow-up.

### ⬜ S6 — fabricated-data labeling (provenance must be visible)
- [ ] `sdr.py` 5 measurement handlers(117) — prepend "MOCK MODE — SIMULATED DATA" when `rf.backend == MOCK`; `_rf_settings`(351) gate on `set_gain()`. **HIGH (HAMs act on np.random)**
- [ ] `traffic_inspector.py` `_path_html_view`(588) — "No Path Data" or watermark the demo graph.
- [ ] `channel_config.py` `_view_all_channels`(121) — parse PSK via `_parse_channel_field`, not whole-output substring. **(security audits)**
- [ ] **Guardrail:** "provenance" rule — mock/demo paths must set a labeled banner; regression test.

### ⬜ S7 — false-clean swallowed-error tail (low severity)
- [ ] `gateway.py` `_show_gateway_status`(195) circuit-breaker block; `meshcore.py` `_meshcore_status_line`(93) subtitle; `nomadnet.py` `_get_rns_config_for_user`(264) storage-prep; `updates.py` `_update_meshforge`(609) service-file step; `_nomadnet_rns_checks.py` `_check_rns_for_nomadnet`(106) instance_name hardcode (#72 class).
- [ ] **Guardrail:** "a failed read in a status surface must render '(status unavailable: …)' not vanish"; extend MF003 to handlers.

---

## Thread 2 — Bidirectional addressability (feature; research DONE)

Research report: `.claude/research/bidirectional_addressability_2026_06_08.md`
(web-sweep + primary-source + adversarial-verify synthesis, 2026-06-08).

**Key finding — composition, not green-field:** MeshForge **already has** the
machinery behind default-off flags: `ContactMappingTable` (persistent SQLite
cross-protocol identity map w/ `resolve_destination`), `DownlinkInjector`
(true-origin NODEINFO + text downlink — the puppeting primitive), `SessionStore`,
`IdentityBinder`, `ReplyContextStore`, `format_reply_token`, the `@id`
directed-downlink parser. The structural blocker is **#35**: every bridged mesh
node collapses to the gateway's single LXMF source identity, and inbound LXMF has
`destination_id=None` — so per-node attribution survives only as a body text
prefix, not a routable address. Reply routing is fundamentally a **state problem**.

**Design direction (asymmetric — the address spaces aren't isomorphic):**
namespace-per-node toward RNS (mint a stable LXMF identity per mesh node — the
Matrix/SSB ghost model); small alias-pool + correlation-token toward Meshtastic
(the Twilio Proxy model, because a 16-byte RNS hash can't embed in a 4-byte
NodeNum). Honors the **wire-compat invariant** (no RNS/Meshtastic wire change)
and the **honest-delivery principle** (#16 "not guaranteed").

- [ ] Phase F1: promote `ContactMappingTable`/`SessionStore` from default-off → on; durable (survive restart) reply mapping with TTL/GC.
- [ ] Phase F2: per-node LXMF identity minting toward RNS (resolve #35 attribution).
- [ ] Phase F3: alias-pool + reply-token on the Meshtastic leg; wire to `@id` directed downlink.
- [ ] Phase F4: real "Transmit" action for tactical_ops (closes the S1 relabel honestly).
- [ ] Keep `CanonicalMessage` compatible with MeshAnchor; land RNS-side changes here first (lead repo).

---

## Thread 3 — Fleet reproducibility: bare-box → fleet-member

**Today:** `docs/fleet_roles.yaml` *declares* the architecture (roles, invariants,
foundation perms, defaults); `provision_role.py` v1/v2 *converges unit state* +
masking + fleet singleton checks. **Gaps** (`.claude/plans/provisioner_scope.md`):
v1 is NOT a package installer (assumes base install), config deltas are
**advisory only**, and the v3 watchdog→re-converge loop is unbuilt.

- [ ] **Bootstrap (the missing rung):** fresh Pi + one command → clone + base install (`install_noc.sh`) + fork-pinned RNS/LXMF (`requirements/rns.txt` `MF-FORK-PIN`) + role-stamped `deployment.json`. Make the manual recovery runbook executable + idempotent.
- [ ] **Promote config deltas advisory → enforced:** mqtt.root (#77), bbox/cap/response-caches, foundation perms, want_ack — converge, don't just warn.
- [ ] **v3 loop:** watchdog drift signal → provisioner re-converge (pairs with auto-remediation off dry_run).
- [ ] **Validation:** a bare board reaching full federated fleet-member state in one run, RF-proven (the moc5 06-04 marathon, but automated).

---

## Mini warm-start continuity (the cross-session contract)

- Each session ends with a `project_session_handoff_*` memory + this plan's
  checkboxes updated, so `/warmstart` + mini's per-tick brief carry the exact state.
- Grow detection via **watchdog probes** ([[project_mini_scales_via_watchdog_probes_2026_06_01]]),
  not a synthesis brain. A future probe candidate: `tui_honesty_regression` is
  unnecessary — MF020 + the guard suite are compile/CI-time, not runtime.
- **Order of attack (recommended):** S3 → S4 (RNS + service-state SSOT, both lead-repo
  reliability) → S5 → S6 → S7; Thread 3 bootstrap can interleave (independent);
  Thread 2 is the longer feature build once the honesty floor is solid.

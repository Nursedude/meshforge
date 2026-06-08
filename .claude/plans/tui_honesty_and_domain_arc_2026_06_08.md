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
(`a32122f5`)** — byte-identical (only "-> MeshAnchor" string); **S4 ported
(`fb3e22d7`)** — svc name `meshanchor`, no `extensions.py`; **S5 ported
(`7c5078c8`)** — byte-identical, no `meshtasticd_radio.py` in MA (skipped that
one site). All parity_check in sync, meshanchor-server pulled.
**Port each future slice (S6-S7) to MeshAnchor too** — read MA's
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

### Per-slice execution playbook (the proven S3–S5 method — follow for S6+)

Each slice is the same loop. The non-obvious rules cost real reasoning to derive;
follow them, don't re-derive:

1. **Read the contract, don't assume.** For each named site, read the helper/
   return it depends on BEFORE editing — e.g. `GatewayConfig.save()`/
   `SettingsManager.save()` return `bool`; `check_service` collapses systemd
   `activating`→`NOT_RUNNING` (lossy — broke a naive `_rns_repair` swap);
   `configure_source` returns `.ok` even when `persisted=False`.
2. **Blast-radius pass FIRST.** `grep` the whole pattern across `handlers/` before
   writing the guard — the plan's named sites are rarely all of them (S3.4 found
   `_rns_repair`; S5 found `automation`/`first_run`/`meshcore` `.save()`). Decide
   per extra site: **FIX** (clean swap) · **ALLOWLIST + call-site comment** (the
   helper is lossy there — `_rns_repair`/`meshcore`/`ai_tools`) · **DEFER + document**
   (a broader sweep, e.g. the `.save()` one). Never silently skip.
3. **Guard = a regression test in `tests/test_regression_guards.py`, NOT a lint
   rule.** Pre-commit runs lint at `--severity error`, so MF008/MF020 *warnings*
   don't gate; it DOES run `test_regression_guards.py`. And a test ports cleanly
   across the **diverged** `MeshForgeLinter`/`MeshAnchorLinter`. Scope it zero-FP.
4. **Verify (route truth to a file): ** `py_compile` changed files · `grep` residual
   pattern gone · `lint.py --all` exit 0 · `pytest test_regression_guards
   test_honest_signal_guards` · affected handlers `-k` · `pytest --collect-only`
   (import check). Use `1>/tmp/x 2>&1; echo EXIT=$?; tail` — never `pytest | tail`.
5. **Commit locally per slice** (power-cut safety — VolcanoAI history), then **push
   origin + targeted `git -C /opt/meshforge pull --ff-only` per box** (moc/moc1/
   moc2/moc3/moc5). TUI-only → **NO daemon restart**; never `fleet_sync.sh` (it
   restarts remotes — #68 cold-start risk / soak break). `meshanchor-server` is
   MeshAnchor-only (no `/opt/meshforge`).
6. **Port the slice to MeshAnchor ("doing it twice" — the most-forgotten step).**
   MA is a near-mirror that DIVERGES: **Read each MA file before Edit** (harness
   requires it); line numbers drift; service name is `meshanchor` not `meshforge`;
   some files are ABSENT (`extensions.py`, `meshtasticd_radio.py`,
   `meshtasticd_config.py`); `check_rns_shared_instance()` is no-arg in MA. Same
   verify gates **+ `python3 scripts/parity_check.py` in sync**; commit+push origin;
   `ssh meshanchor-server 'git -C /opt/meshanchor pull --ff-only'`. MA-native sites
   with no MeshForge twin get their own allowlist+comment.
7. **Update plan checkboxes + `project_session_handoff_2026_06_08` + `MEMORY.md`**
   each slice so `/warmstart` stays honest.

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

### ✅ S6 — fabricated-data labeling (provenance must be visible) (DONE 2026-06-08)
- [x] `sdr.py` — new `_mock_banner(rf)` (compares `rf.backend.name == "MOCK"`, no new import) prepended to ALL 5 measurement surfaces (spectrum/waterfall/utilization/survey/interference). In MOCK every value is `np.random` noise (`MockSDR.receive_samples`). `_rf_settings` gain now gated on `set_gain()`'s `bool`: not-connected → "Gain Stored" (preference only, nothing applied), reject → "Gain Not Applied" — was an unconditional "Gain set to X dB". **HIGH — DONE**
- [x] `traffic_inspector.py` `_path_html_view` — tracks `used_demo = path_count == 0`; the final "Path Visualization Generated" dialog (both SSH + browser branches) carries a `*** SAMPLE DATA — not real traffic ***` note when the graph is demo hops with fabricated SNR/RSSI (the dismissable "Generating Demo" prompt was the only label before). **DONE**
- [x] `channel_config.py` `_view_all_channels` — PSK column now classifies THIS channel's `psk` field via new `_classify_psk()` instead of the whole-output substring (`'psk' in raw and 'none' not in raw` → false verdict if those words appeared anywhere in the blob). Unparseable → `'?'` (honest unknown, never false `'None'`); `AQ==` → `'Default'` (the public default key — a real security distinction). Added `import re`. **(security audits) — DONE**
- [x] **Guardrail:** `TestSdrMockProvenance` / `TestChannelPskProvenance` / `TestTrafficDemoProvenance` in `tests/test_honest_signal_guards.py` (regression tests: unit tests on `_mock_banner`/`_classify_psk` + static scans that the 5 banner surfaces + gain-gating + SAMPLE-DATA note + non-substring PSK are present). **Skip-if-file-absent** so they port cleanly to MA's divergent tree. **DONE**
- **Blast-radius pass:** the provenance class is well-contained — `demo.py`, `dashboard.py`'s `[DEMO MODE ACTIVE]` banner, and `meshcore.py`'s `simulation_mode` line ALL already surface their simulated state honestly (truthful, not defects — no fix). No handler compared `backend == MOCK` before; no existing test touched the three files.
- **MA port:** MeshForge `1b91f85` (fleet-pulled, TUI-only/no restart) / MeshAnchor `f4afd660` (meshanchor-server pulled). All three files were byte-identical to the MeshForge twins → same edits; parity_check in sync.

### ✅ S7 — false-clean swallowed-error tail (DONE 2026-06-08) — **Thread 1 complete**
- [x] `gateway.py` `_show_gateway_status` — circuit-breaker read `except Exception: pass` → appends `"CIRCUIT BREAKERS: (status unavailable: {e})"`. An empty section had read as "no open breakers" when the read itself failed.
- [x] `meshcore.py` `_meshcore_status_line` — config-read failure returns a distinct `"MeshCore: status unavailable (config read failed)"` instead of the no-module neutral subtitle (the two were indistinguishable). **DONE**
- [x] `updates.py` `_update_meshforge` — service-file step's bare `except Exception: pass` → appends `"(service update error: {e})"` to `svc_msgs` so the "Update Complete" dialog reflects it (mirrors the existing OSError branch). **DONE**
- [x] `nomadnet.py` `_get_rns_config_for_user` — swallowed `/etc/reticulum/storage` perms-fix failure is stashed on `self._rns_storage_prep_warning` (reset per call) and **surfaced at the NomadNet launch surface** (drift/permission risk), not just a debug-invisible log. **DONE**
- [x] `_nomadnet_rns_checks.py` `_check_rns_for_nomadnet` — hardcoded `'\x00rns/default'` probe → canonical instance-aware, #68-bounded `utils.rns_init._probe_shared_instance_connect`; instance_name from `ReticulumPaths.get_configured_instance_name()` (sudo/active-config-aware; falls back to `'default'` — no regression on standard boxes; a non-default box no longer gets a false health verdict, #72 class). Removed now-unused `import socket`. **DONE** (resolver swapped from my interim `_read_instance_name_from_config('/etc/reticulum')` in follow-up `63a1d9b` — removed the hardcode the fix itself introduced.)
- [x] **Guardrail:** `TestSwallowedErrorTailS7` (5 static, skip-if-absent) in `tests/test_honest_signal_guards.py`. **Note:** MF003 catches *bare* `except:`; these defects are *typed* `except Exception: pass`, so the guard is regression tests, **not** an MF003 extension (the plan's earlier "extend MF003" guess didn't fit — MF003 already covers bare-except and no handler has one).
- **Blast-radius pass:** the other handler swallows are input-handling (`EOFError`/`KeyboardInterrupt` on dialogs) or benign device probes — not status surfaces, out of scope.
- **MA port:** MeshForge `5a34a80`+`63a1d9b` (fleet-pulled, TUI-only/no restart) / MeshAnchor `8f9def16` (meshanchor-server pulled). All 5 files byte-identical to the MeshForge twins (only nomadnet's "return to MeshAnchor" string + `_update_meshanchor` + MA's None-resolving `get_rns_shared_instance_info`); parity_check in sync.

> **Thread 1 (TUI honest-signal burn-down) is fully closed (S0–S7).** Remaining: Thread 2 (bidirectional addressability feature) and Thread 3 (fleet bootstrap).

### ✅ MA-divergent honest-signal audit — DONE 2026-06-08 (worklist = candidate S8)
The deferred audit of MeshAnchor's surfaces with **no MeshForge twin** (MeshCore-primary fork) is complete: 5 parallel read-only agents over ~34 MA-native files, 2 HIGH findings independently re-verified. **11 findings (2 high, 4 med, 5 low).** Full worklist + evidence + fixes: `.claude/research/ma_divergent_honest_signal_audit_2026_06_08.md`. The 2 HIGH are operator-facing **false verdicts**:
- **H1** `gateway/meshcore_supervisor_handler.py` — MeshCore reads HEALTHY while the radio is physically down (`radio_down` is an unrecognized `record_connection_event` no-op + unconditional `connect()` "connected" → `_connected` latches True; the #74 dead-branch class).
- **H2** `gateway/lxmf_broadcast_bridge.py:1018` — `mark_delivered()` fires on **enqueue** (delivery is async via `on_delivered` receipt) → status shows `last_ok`/`healthy` for a dead destination forever (the #16 best-effort class).
- MED: meshcore `.save()` false-"Saved" ×2 (fold into the S5 `.save()` sweep), fleet "Schedules" panel green-on-probe-failure, NomadNet uninstall "removed"-when-not. LOW ×5 (triage). **MeshAnchor is the lead repo for this set** (no MeshForge twin except the `.save()` family); guard home = MA's `tests/test_honest_signal_guards.py`.

### 🔶 S8 — MA-divergent honest-signal burn-down (MeshAnchor-lead) — HIGH done, MED/LOW open
- [x] **H1** `gateway/meshcore_supervisor_handler.py` (MeshAnchor `73c86dd1`, deployed) — `connect()` now reflects the radio's actual state via `hello.get("connected")` (was unconditional "connected"); `_on_connection_state_event` emits the recognized `"connected"`/`"disconnected"` events (was the no-op `radio_up`/`radio_down`) so a radio-down clears `_connected`. No longer reads HEALTHY while the radio is down. **DONE**
- [x] **H2** `gateway/lxmf_broadcast_bridge.py` (`73c86dd1`, deployed) — `SubscriberStore.mark_delivered`→`mark_fanout_enqueued` (honest docstring); status dict exposes `last_fanout_enqueued` (deprecated `last_delivery` alias kept for API stability); TUI labels `last_fanout` + "delivery NOT confirmed (#16)" legend instead of `last_ok`/"Last OK". State machine + DB column unchanged (no migration). True-receipt→confirmed-delivery field is a noted follow-up. **DONE**
- [x] **Guard:** `TestMADivergentS8` (4 static, skip-if-absent) in MA's `tests/test_honest_signal_guards.py`; updated `test_lxmf_broadcast_bridge.py` call sites. MA-native (no MeshForge twin; `bridge_health` untouched → no port). Verify: lint 0, 175 affected+guard pass, collect 5067, parity in sync.
- [x] **M1+M2** meshcore `.save()` ×2 (MeshForge `57c359d` + MeshAnchor `ff635f69`, **both** — shared handler; the meshcore slice of the deferred `.save()` sweep). `_meshcore_configure`/`_meshcore_toggle` now bind `saved = config.save()` and branch — "Saved"/"enabled" no longer fires on a failed write (`GatewayConfig.save()` returns False, never raises). Guard `TestMeshcoreSaveGatedM12` (both repos). **DONE**
- [x] **M3** fleet "Schedules" panel green-on-probe-failure (MeshAnchor `ff635f69`, MA-native). `fleet_aggregator._list_timers_scope` returns `None` on probe failure (rc≠0/timeout/parse) vs `[]` for "no timers"; `_schedules_block` emits `{healthy:False, reason:"timer state unavailable (…)"}`; `web/fleet.html renderSchedules` shows an "unavailable" badge + reason (not green "ok"/"all healthy") + an alert chip. Guard `TestSchedulesProbeFailureM3` (behavioral). **LIVE** — `meshanchor-map` restarted 2026-06-08 13:26 HST (clean, healthz 200, NRestarts=0); `/fleet` serves the new renderSchedules + `/fleet/rollup` schedules healthy (4 units, no false reason). **DONE**
- [x] **M4** NomadNet uninstall "removed"-when-unlink-failed (MeshAnchor `ff635f69`, MA-native). `_do_nomadnet_uninstall` tracks `removal_errors`; reports "NomadNet uninstall incomplete (… could NOT be removed)" instead of unconditional "removed". Guard `TestNomadnetUninstallM4`. **DONE**
- [x] **L1–L5** DONE (MeshAnchor `6311722b`; L3 also MeshForge `1834fcc` — shared `active_health_probe.py`). **L1** documented the necessary user-unit exception (`check_service` is system-scope only — answered: no `--user` support). **L2** reviewed, no-op (best-effort confirmation DM; `added` state real; H2 owns delivery). **L3** `get_status` emits `uptime_percent=None` (not 0.0) for never-checked. **L4** `_collect_daemon_federation_peers` returns `(peers, err)` → rollup threads the registry-fetch failure into `rollup.errors` (no silent "0 peers"). **L5** `validate_meshcore_device` `responds=None` (not fabricated True) when a persistent owner holds the radio. Guards `TestLowTierS8` (MA) + `TestUptimePercentSurfaceL3` (MF) + updated `test_meshcore_connection`. ⚠️ L4 latent until next `meshanchor-map` restart. **S8 COMPLETE.**
- [ ] **Broader `.save()` sweep (deferred S5)** — `SettingsManager.save()` bool discarded in `automation.py` ×9 / `first_run.py` ×1 (meshcore ×2 now done via M1/M2). Separate focused audit (the last open honest-signal item).

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

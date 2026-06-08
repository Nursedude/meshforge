# MeshAnchor-divergent honest-signal audit (2026-06-08)

> Companion to the TUI honest-signal arc (`tui_honesty_and_domain_arc_2026_06_08.md`).
> The original 2026-06-08 audit covered the **shared** TUI handlers (fixed in S0–S7
> + ported to MeshAnchor). This audit covers the surfaces that exist **only in
> MeshAnchor** (MeshCore-primary fork) or **diverge** from MeshForge — never
> covered by the shared audit. Method: 5 parallel read-only agents over ~34
> MA-native files, each hunting the 6-shape honest-signal taxonomy with an
> adversarial bar; the 2 HIGH findings independently re-verified by reading the
> consuming code.

**Shapes:** (1) discarded-return false-success · (2) send-path false-"sent" ·
(3) false service-state/false-green · (4) persisted-write false-"saved" ·
(5) fabricated-data unlabeled · (6) false-clean swallowed-error.

**Result: 11 findings (2 high, 4 med, 5 low).** The 2 HIGH are the load-bearing
ones — both are operator-facing **false-HEALTHY / false-DELIVERED verdicts**.

---

## HIGH (verified — fix first)

### H1 · MeshCore shows HEALTHY while the radio is physically down
`src/gateway/meshcore_supervisor_handler.py:133` + `:323-331` — SHAPE 3 — **CONF high (re-verified)**

- `connect()` fires `record_connection_event("meshcore", "connected")` **unconditionally**
  on supervisor socket-open (no check of `hello.get("connected")`) → `_connected["meshcore"]=True`.
- When the radio drops, `_on_connection_state_event` emits `"radio_up"`/`"radio_down"` —
  but `bridge_health.record_connection_event` only acts on `"connected"`/`"disconnected"`/`"error"`
  (`bridge_health.py:260-273`). `radio_down` matches **neither branch → silent no-op** (verified
  the only emit sites repo-wide). `_connected["meshcore"]` latches True forever.
- `is_healthy()` / the "MeshCore disconnected" reason line read that stuck flag → MeshCore
  reports CONNECTED/HEALTHY while the radio is down and the supervisor reconnect loop backs off.
  **The #74 dead-correction-branch class.**
- **FIX:** emit the recognized `"connected"`/`"disconnected"` events from `_on_connection_state_event`,
  and gate the `connect()` "connected" report on `hello.get("connected")`.

### H2 · LXMF broadcast presents an enqueue as a confirmed delivery
`src/gateway/lxmf_broadcast_bridge.py:1018` — SHAPE 2 — **CONF high (re-verified)**

- `call_boundary("rnsd.handle_outbound", self._router.handle_outbound, lxm, …)` only **enqueues**
  (delivery is async; the `on_delivered`/`on_failed` receipt callbacks registered at `:1006-1007`
  are the actual signal). Immediately after, `self._subs.mark_delivered(sub.lxmf_hash)` (`:1018`)
  records a successful delivery, resets the failure counter, forces `state=healthy`, increments
  `fanouts`, returns True.
- The status surface (`lxmf_broadcast.py` `_format_subscriber_rows`/`_format_status`) renders that
  timestamp as `last_ok` + `state=healthy`. A subscriber with a dead RNS destination (resolved past
  `Identity.recall`) shows `last_ok` updating and `healthy` **forever**. **The #16 best-effort class.**
- **FIX:** only set `last_delivery`/reset-to-healthy from the `on_delivered` receipt callback;
  rename the post-enqueue bookkeeping to attempt/enqueue semantics and label the status column
  "last_enqueued (delivery not guaranteed)" until a receipt confirms.

---

## MED

### M1+M2 · MeshCore config `.save()` false-"Saved"
`src/launcher_tui/handlers/meshcore.py:321` (`_meshcore_configure` save) + `:353` (`_meshcore_toggle`) — SHAPE 4 — CONF high

- `config.save()` called as a bare statement; `GatewayConfig.save()` catches all exceptions and
  returns `False` on a failed write (never raises) → the enclosing `try/except` is dead → the
  "Saved" / "MeshCore is now {enabled|disabled}" msgbox fires even when the disk write failed.
- **FIX:** gate on the `.save()` bool (MeshForge S5 idiom) — `if config.save(): … else: "Save Failed — NOT persisted"`.
  *(Same `SettingsManager/GatewayConfig.save()`-discarded family as MeshForge's deferred `.save()` sweep.)*

### M3 · Fleet "Schedules" panel reads green when the timer probe failed
`src/monitoring/fleet_aggregator.py` `_list_timers_scope`/`_schedules_block` (~`:619`/`:683`) — SHAPE 6 — CONF high

- `_list_timers_scope` swallows `TimeoutExpired`/`OSError`/`FileNotFoundError`/`JSONDecodeError`/`rc!=0`
  → `return []` → `_schedules_block` → `{healthy: True, stale_count: 0}` → `/fleet/slo` dashboard
  renders green "N timers · all healthy". A wedged `systemctl list-timers` is indistinguishable from genuinely-healthy.
- **FIX:** return `None` on probe failure (vs `[]` for "no timers") → emit `{healthy: False,
  reason: "timer state unavailable: <err>"}` → panel renders "(timer state unavailable)".

### M4 · NomadNet uninstall claims "removed" when the file is still there
`src/launcher_tui/handlers/_nomadnet_tmux_service_ops.py:557-564` — SHAPE 6 (+1) — CONF high

- `_do_nomadnet_uninstall` swallows `path.unlink()` OSError (logged only) and discards the
  stop/disable/daemon-reload returns, then unconditionally shows "uninstalled / unit + wrapper removed".
  Under sudo (files were chowned to the real user) the unlink can genuinely fail → the unit remains on disk.
- **FIX:** track unlink/stop/disable success; report "(removal incomplete: <path> still present: <err>)".

---

## LOW (triage / opportunistic)

- **L1 · `_chat_pane_service_ops.py:167`** — SHAPE 3 — raw `systemctl --user is-active` instead of
  `check_service()` SSOT (MF008 shape). FAILED is *not* collapsed to green (honest today), but it
  bypasses the SSOT. **Open question:** does `service_check` support `--user` units? If yes → route through it;
  if no → document the user-unit exception so it isn't flagged as drift. CONF med.
- **L2 · `lxmf_broadcast_bridge.py:880`** (`_reply` "Subscribed, you'll receive…") — SHAPE 2 — asserts an
  outcome on an unconfirmed `_send_to_subscriber`. Mostly covered by the H2 fix. CONF med.
- **L3 · `utils/active_health_probe.py:171`** — SHAPE 5 — `uptime_percent` returns `0.0` for a
  never-checked service (mitigated by the sibling `state:"unknown"`). FIX: return `None` for `total_checks==0`. CONF low.
- **L4 · `monitoring/fleet_rollup.py:159`** — SHAPE 6 — daemon `/fleet/federation` fetch error → `[]`,
  no error row → "0 peers" reads the same as "heard nothing". FIX: thread the fetch error into `rollup.errors`
  (`_map_fleet.py:595` already does this for the same failure). CONF low.
- **L5 · `utils/meshcore_connection.py:154-157`** — SHAPE 5 — `validate_meshcore_device` returns
  `readable=True, responds=True` "by inference" when a persistent owner holds the radio (carries an honest
  `error` string; no current surface consumes `responds`). FIX: set `responds=None`/`probed=False` so a
  future status surface can't inherit a fabricated green. CONF low.

---

## Recommended sequencing (if this becomes slice S8 — MeshAnchor lead)

1. **H1 + H2** — the two false verdicts an operator acts on. Each gets a behavioral guard
   (H1: a `radio_down` event clears `_connected`; H2: `last_delivery` only moves on `on_delivered`).
2. **M1+M2** — fold into / alongside the deferred `.save()` sweep (MeshForge S5) — same family.
3. **M3 + M4** — false-clean status/uninstall surfaces (the S7 idiom: render "(… unavailable)" / "(removal incomplete)").
4. **L1–L5** — opportunistic; L1 needs the `check_service` `--user` question answered first.

These are **MeshAnchor-native** — MeshAnchor is the lead repo for this set (no MeshForge twin to keep
in parity, except M1/M2 which echo the shared `.save()` family). Guard home: MeshAnchor's
`tests/test_honest_signal_guards.py` (the same suite the shared arc ports through).

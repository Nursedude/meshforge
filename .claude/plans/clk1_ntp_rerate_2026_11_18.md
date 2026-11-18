# CLK-1 — meshtasticd re-rates its clock after a fail-open boot (2026-11-18)

Status: planned (NEXT). Added by operator decision 2026-11-18: "yes, the automatic re-rate — add it to NEXT".

## Gap

Portduino meshtasticd rates its clock ONCE at start (`timedatectl ... synchronized`,
firmware `src/main.cpp`). The NTP gate (`templates/systemd/meshtasticd.service.d/20-wait-for-ntp-sync.conf`
→ `scripts/wait_for_ntp_sync.sh 60`) FAILS OPEN after 60 s by design: a no-WAN box
still gets its radio. Nothing restarts meshtasticd once the clock syncs.

## Hits (4 in 47 days, each cured only by a hand restart)

| Box | Window | Consequence |
|---|---|---|
| moc4 | 10-02, 5.8 d | 0/374 online |
| moc2 | 10-26 → 11-08, 13 d | 0 online |
| moc2 | 11-14 04:29 → 11-17 23:15 | 351/351 `num_online_nodes=0` |
| moc | 11-14 04:55 → 11-18 07:07 | nodes still 36–38 (not frozen, lehua shape); own Position time stripped (`Strip low RTCQuality (2)`) for 4 d |

The `meshtasticd_clock_unrated` probe (`src/utils/watchdog_probes_clock.py`) sees it but is
OBSERVE-ONLY (operator 11-09) and cannot act.

## Cure (design)

- Trigger: the CURRENT invocation's gate printed `wait_for_ntp_sync: TIMEOUT`, authenticated
  by journald's trusted `_COMM` (the probe already reads exactly this).
- A bounded one-shot waits for `timedatectl` synchronized (≤ N min; else a `gave-up` witness).
- Then restart meshtasticd ONCE, keyed to the InvocationID (a marker), so it can never loop.
- Only if meshtasticd is still active: never STARTS a deliberately stopped unit
  (`feedback_deploy_restarts_only_active_units`).
- Witness line + counter on every outcome: `restarted` / `gave-up` / `not-needed` (hfm #9).
- Ships via `templates/systemd/` + `scripts/update.sh` the same commit (Pre-Push line 4).

⛔ Never triggered from the CONSEQUENCE (`num_online_nodes=0`, RX lines): the journal is
mesh-writable (a received text is logged verbatim, newlines split entries), so a stranger's
text would become a remote restart primitive.

## END + acceptance

- END: a box booted before NTP stamps real time within N min of sync, with no operator.
- Drill (operator go — a radio outage): block NTP on one radio box, restart meshtasticd,
  unblock → exactly one restart + `restarted` witness; re-run with NTP up → `not-needed`;
  keep NTP blocked past N → `gave-up`, no restart. A control that can fail: the
  second-restart count must stay 0.
- Surface (hfm #11): fleet/TUI line "clock unrated since boot — re-rate pending / done / gave up".
  Today only mini saw it, as `detector_blind`.
- Moves 1.0 gate 5: a stranger's RTC-less Pi boots before NTP → every node offline, and
  nothing in-app says why.

## Open

- N (the wait budget) — pick from measured sync-after-boot times on the fleet (lehua synced
  56 s after its 10-26 timeout).
- moc bridges the two presets: the one restart briefly drops that bridge — acceptable at
  boot, say so in the witness.
- Does the probe's verdict read `clean` after an automatic re-rate? (Its "only the gate line
  reads clean" rule; the new invocation's gate line should say `synchronized after 0s`.)

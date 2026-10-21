# Fleet web (`:5000/fleet`) upgrade — plan, 2026-10-20

Operator decision 2026-10-20: "i'll go with your recommendations". Lands in
`.claude/plans/` with the first code slice (kept out of the live tree until the
MF deploy chain of the day finishes — an untracked file reads as a dirty tree).

## Measured starting point
- `STRUCTURAL_DARK` (`src/utils/fleet_truth.py`) = 9 rows, ~12 KB of prose,
  every row a full paragraph in the "Known Blind Spots" pane. 3 open "CLOSED",
  2 "ACCEPTED", 1 "NARROWED" — a closed blind spot rendered dark is itself a
  legibility lie (the page says we can't see what we can).
- Box cards carry state + witness + posture but have no drill-in.
- Gates already layered in `map_http_handler.py`: Host-header refusal (DNS
  rebinding), trusted-client IP, operator login cookie (BIC-3,
  `_map_auth_endpoints.py`), CSRF Origin check. TLS (BIC-13b) not yet.
- SDR: capture timer writes `sdr/interference.jsonl`; TUI has an ASCII
  waterfall (`handlers/sdr.py`). moc1's RTL-SDR has no consumer since 10-03.

## Order (each slice: test first, non-author review before first deploy)
1. **Blind-spot compaction.** Rows gain `status` (open|narrowed|accepted|closed),
   `since` (date of the LATEST status change), one-line `dark` (what is STILL
   unwatched); prose stays as `detail`. Pane shows OPEN/NARROWED one line each;
   ACCEPTED+CLOSED fold into one "N accepted / closed" line; detail behind a
   click. Tests: a `closed` row gets its own class, never the dark styling;
   every row has a known status (closed enum, hfm #7); a record opening with a
   status word opens with its own; a missing `dark` shows a marker, never the
   record. **Built 2026-10-20 on a branch; non-author review found 8, all
   fixed on the branch.** The registry is `utils/fleet_blind_spots.py`,
   byte-identical with MeshAnchor alongside `fleet_truth.py` — land BOTH repos
   together (`parity_check` now fails a twinned file whose import the twin lacks).
   **Review rounds 2+3 (2026-10-20, four non-author reviews, 38 findings):**
   MA page renderer + tests ported; served list is a copy; rows carry
   `watched_in` and each NOC serves a per-row `scope` (here / elsewhere /
   unknown / n/a) — a status earned by MeshForge probes is never folded or
   struck through on MeshAnchor (measured: none of the 9 cited probes exist
   there); open rows survive the 5 s poll; both pre-push hooks block a
   present-but-MISSING twin, read exit codes WITH the RESULT line (a checker
   crash is UNOBSERVABLE, not drift), use mktemp, and check the repo being
   pushed. **The twin-import parity tier was CUT in round 3**: four holes
   fixed, five more found — and the byte tier already reports the one-sided
   landing, while MA's own suite fails on the ImportError. **Rounds 4+5:**
   accepted rows carry `watched_in` too and scope is default-dark (only
   `open` is n/a; a scope-less row from a not-yet-restarted server stays
   visible); a watched_in row must cite probes its own repo defines (that
   gate found a closed row citing a probe removed 2026-08-08); a hook whose
   checker crashed now BLOCKS (only an absent twin warns); MF's hook takes
   MESHANCHOR_REPO; fleet_truth.py's module-level imports pinned to twinned
   files. **Round 5 is UNREVIEWED — the next session reviews it (non-author
   by construction), checks /fleet in a browser, then lands.** **Landing
   order (hook-enforced, against WORKING TREES):** commit the MA half on MA
   main WITHOUT pushing → commit + push MF → push MA.
2. **Box drawer (read-only).** Click a box → SHA vs HEAD, uptime, posture,
   `@rns/` owner, subsystem cells, escalations, cron verdicts, SDR line. Data from
   `/api/fleet/truth`; presentation only.
3. **SDR snapshot strip.** Server-rendered from existing captures, SDR boxes only,
   internal only (RF occupancy + timing is site-revealing). No live FFT stream on
   a Pi; live view stays on an ssh tunnel. A present-but-unread SDR says so.
4. **Shared action registry + web actions: OFFLINE (posture+power) and SYNC.**
   One registry rendered by TUI and web; parity test fails on a web action with
   no TUI twin; calls the same CLI (`scripts/fleet_power.py`, posture declare) —
   never a second implementation (fleet_power encodes atomic declare + `via`
   ordering; skipping it brings back the 09-10 false pages). Gates: dry-run plan
   first, box name typed, audit row, rate limit, never the NOC box from its own
   page, operator cookie only over loopback until TLS. **REBOOT stays TUI/CLI.**
5. **Cloud mon (later — grill first).** Read-only, PUSH only: boxes send a
   signed, redacted summary out; the cloud holds no keys and no path in. No logs,
   IPs, positions, channel names (MF015).

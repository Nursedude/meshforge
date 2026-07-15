# Harness & cross-model review findings — July 2026

Groundable lore for the offline oracle / local tier (honest_failure_modes
point 10: a resolved incident compiles to a probe/rule, a lore entry, AND a
tier-L eval case). These four findings came out of the 2026-07-09 cross-model
frontier review arc ([[project_cross_model_audit_2026_07_09]]) and the
2026-07-13 file-size ratchet arc ([[project_opus_handoff_2026_07_13]]). The
full adversarial provenance (finders, refuted candidates, residuals) lives in
`.claude/audits/review_provenance.md`, which is NOT a corpus root — so this
doc is the version the local oracle can retrieve and cite. Each finding was
fixed with tests, RED-first, and ported to the MeshAnchor twin.

All four are one defect class: **a degraded or duplicated internal state gets
mapped to a valid-looking value, and downstream logic turns it into a false
claim** — the write-time checklist in `.claude/rules/honest_failure_modes.md`.

---

## transport-truth — message_queue delivery accounting lied (2026-07-09, Pri-2)

The gateway's SQLite message queue (`src/gateway/message_queue.py`) and
`message_routing.py` had four delivery-accounting defects where the transport
reported success or nothing while the real delivery state was different. Fixed
in the 2026-07-09 frontier review, both repos (MeshAnchor carried all four —
its files are diverged copies, not parity-locked).

- **QUEUE_SHED had zero recording sites — a shed message went QUEUED then
  nothing forever.** When the queue overflows, `_shed_overflow()` DELETEs the
  lowest-priority oldest messages. Each was recorded QUEUED at enqueue, but
  nothing recorded a terminal state on shed, so its lifecycle read
  QUEUED-then-nothing and it silently vanished from the delivery-counter
  populations. `QUEUE_SHED` existed in the closed taxonomy with no writer.
  Cure: on shed, `_dc.record(DeliveryState.DROPPED, ...)` for every shed id —
  a shed message now terminates DROPPED, not into the void.
- **`mark_acked` did SELECT-then-UPDATE → a double synthetic ACK under a
  race.** Two concurrent callers (an LXMF delivery callback racing the
  overdue-ack sweep, or a re-fired callback) could both read `ack_status =
  'pending'` and both return the origin, emitting a synthetic ACK twice on the
  origin network. Cure: the UPDATE itself is the gate —
  `UPDATE ... SET ack_status='acked' WHERE id=? AND ack_status='pending'`;
  exactly one caller gets `rowcount == 1` and wins the row.
- **`mark_delivered` was non-idempotent** — a repeated finalise could
  re-transition a terminal row. Made idempotent.
- **A routing "bounced" branch had a latent inversion** — always-False today,
  so harmless, but the condition was backwards; made explicit so a future
  change can't silently activate the inverted logic.

Quick tell: a `delivery_confirmation_stall` or confirmation-rate anomaly where
sent ≠ confirmed + failed usually means a message left a population without a
terminal record — look for an unrecorded drop/shed path.

---

## service_check wrong-state — over-elevation reported a running service NOT_INSTALLED (2026-07-09, Pri-4, #20 class)

`check_service()` in `src/utils/service_check.py` is the privileged-systemd
SSOT. Its **read-only** queries (`systemctl is-active`, `show`,
`list-unit-files`) were routed through `_sudo_cmd`. On a box **without
passwordless sudo**, `sudo systemctl is-active <svc>` fails and returns empty
output → `check_service` read that empty result as the service being absent and
reported a **RUNNING service as NOT_INSTALLED** (a #20 wrong-state
regression: the SSOT had drifted toward over-elevation). systemctl state
queries never need root.

Cure: a new `_systemctl_query_argv()` helper builds read-only invocations that
**never** prepend sudo (aligning them with the sibling read-only helpers);
**mutations** (start/stop/enable/config-write) KEEP sudo. The fleet is
passwordless-sudo so behavior is identical there, but a fresh or hardened box
no longer misreads its own services. Companion fix same pass: `_sudo_write`'s
root path became atomic (mkstemp + fsync + os.replace) so a half-written unit
file can't brick a service on the next daemon-reload (#60 class). The
MeshAnchor twin lacked the wrong-state bug (it was already sudo-free — MF had
diverged toward elevation) but shared the atomic-write fix.

Quick tell: a service you can see running with `systemctl status` but which
MeshForge calls NOT_INSTALLED → check whether the query path is being forced
through sudo on a box that prompts for a password.

---

## lint line-offset — a real violation on a duplicate line silently passed the gate (2026-07-09, lint self-review)

`scripts/lint.py`'s lookahead/lookback rules (MF001 Path.home, MF004
subprocess-timeout, MF009 configdir, MF010 daemon time.sleep) resolved a
flagged line's position in the file with `content.find(line)` — which returns
the **FIRST textual occurrence** of that line's text. Identical line text is
common in real code (`time.sleep(1)`, `result = subprocess.run(`), so a
genuine violation on a **later duplicate line** was judged against an **earlier
twin** (which might have the mitigating context nearby, e.g. a `timeout=` on
the first `subprocess.run` but not the second). The real violation **silently
passed the gate** — the worst kind of lint bug, a false green.

Cure: thread the true **per-line character offset** into `_check_line()`
(`line_offset`) so each rule's nearby-context window is anchored at the actual
line, not the first lookalike; the legacy `content.find` path remains only as a
fallback when no offset is supplied. RED-first verified in both repos (two
tests fail on the old `content.find`, pass on the fix). The MeshAnchor twin
carried the identical defect and got the identical fix.

Lesson: a linter that locates a line by its text, not its position, can grade a
violation against the wrong instance of a repeated line — always carry the
offset.

---

## MF025 file-size ratchet — the 1,500-line cap that only shrinks (2026-07-13)

Lint rule **MF025** (`scripts/lint.py`, `MF025_BASELINE`) enforces the
CLAUDE.md "split files exceeding 1,500 lines" rule mechanically in both repos.
It is a **ratchet**, not a flat cap:

- Any file over **1,500 lines** fails MF025 — UNLESS it has a frozen baseline
  entry in `MF025_BASELINE` (a dict of `path → grandfathered line count`).
- A baselined file may only **shrink**: its effective limit is
  `max(1500, baseline)`, and a companion test (`test_baseline_never_grows`)
  fails if a baselined file grows past its recorded count. The baseline can go
  DOWN but never UP — you cannot raise a limit to make a growing file pass.
- When you **split** a file back under 1,500 lines, you must **DELETE** its
  baseline entry; a `test_frozen_baseline` / stale-entry test fails if a
  baseline entry names a file that is now under the cap (stale grandfathering
  is itself a lint failure).
- As of 2026-07-14 all seven original offenders were split and
  `MF025_BASELINE == {}` (empty) in BOTH MeshForge and MeshAnchor. The
  never-grows test was rewritten to `monkeypatch.setitem` a synthetic offender
  so the ratchet logic stays covered even with an empty baseline.

Reusable split techniques (for any future >1,500-line file): extract a
cohesive config/types slice into a `<file>_config.py` sibling and re-import it
back; split a probe module into domain siblings and move any shared helper to
the base; for a class-heavy file add a mixin (an AST free-variable pass finds
inherited module globals) — direct-import the unpatched names, resolve
test-patched names lazily off the hub to keep the patch seam. Always grep
`patch("utils.<file>.X"` test seams first, and test both import orderings when
a sibling imports back from the hub.

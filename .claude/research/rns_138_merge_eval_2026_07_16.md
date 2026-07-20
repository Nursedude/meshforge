# RNS 1.3.x (→1.3.8) + LXMF 1.0.x upstream-merge evaluation

**Date:** 2026-07-16 · **Author:** Claude Fable 5 (frontier eval, window item 3)
**Scope:** Judge whether to adopt upstream RNS beyond our `1.2.5+mf.5` fork line,
against the non-negotiable wire-compat invariant, and scope the merge work.
Deliverable is **eval + plan, not the merge** (per the Fable-5 window plan).

## TL;DR — recommendation

**Adopt upstream RNS `1.3.8` (not `1.3.5`) as a deliberate, canaried arc — NOT
an emergency.** The wire-compat invariant is **cleared** (crypto primitives
untouched; the one big change — a shared-instance RPC rewrite — is *local IPC*,
not the network wire format). The merge is **moderate and well-scoped**: only
`Reticulum.py` genuinely conflicts, concentrated in ~16 RPC call sites, and
**none of our `+mf.N` patches are subsumed** — in particular the #72 wedge our
`_rpc_recv` bound fixes **still exists in 1.3.8**, so that patch must be
*re-ported onto the new mechanism*, not dropped. LXMF `1.0.1` is a near-trivial
adopt (our fork has zero functional patches) but must move in lockstep with
MeshAnchor (`canonical_message` parity) and verify compression-signalling
cross-compat. **No CVE or wire-forcing function exists → no urgency; the value
is reduced fork drift + upstream reliability fixes (AutoInterface roaming,
announce de-dup, RPC handling, hop-count).**

Target **1.3.8**, not the window's nominal 1.3.5: `1.3.6` was buggy/superseded,
`1.3.7`/`1.3.8` are strict improvements, and upstream has moved on since the
2026-06-09 survey that named 1.3.5.

---

## Evidence

### Fork state (ground truth, this session)
- Fork `Nursedude/reticulum` branch `meshforge` HEAD `83f4be33` = `1.2.5+mf.5`.
  6 mf commits over stock `1.2.5`: baseline marker (mf.0), **#68** connect-hang
  bound (`LocalInterface.py`), **#72** RPC recv bound (`Reticulum._rpc_recv`),
  **mf.3** `detach_interfaces()` bound, **mf.4** `logging_lock` RLock +
  signal-deferred teardown, **#69/mf.5** wanted-host-loss exit-75 (`Transport.py`).
- Fork `Nursedude/lxmf` HEAD `66c48cf` = `0.9.4+mf.0` — **marker only, zero
  functional patches**.
- Upstream (`markqvist`) now at **RNS 1.3.8** / **LXMF 1.0.1** (fetched this
  session; the 2026-06-09 note said 1.3.5 — it has advanced).

### Wire-compat — CLEARED (the non-negotiable invariant)
- **Crypto primitives untouched.** `git diff 1.2.5 1.3.8 -- RNS/Cryptography/`
  = **one file, +2 lines** (`Hashes.py`): a `hashlib.file_digest` availability
  guard for the new `rngit` file-hashing utility (needs Python 3.11+). No change
  to Ed25519 / X25519 / AES-256-CBC / Fernet / packet HMAC. The gateway never
  calls `file_sha256` (rngit-only), so even the 3.11 requirement is moot for us.
- **Changelog 1.2.6→1.3.8 announces no wire break.** Every release is
  maintenance or the `rngit` (git-over-Reticulum) + release-signing buildout
  (which accounts for most of the 11.8k insertions and is orthogonal to
  transport). Transport-relevant fixes: announce de-dup regression (1.3.4),
  shared-instance RPC handling (1.3.4), AutoInterface fast-roam deadlock (1.3.5),
  announce-propagation cleanup + new interface modes `internal`/`recursive_prs`/
  `announces_from_internal` (1.3.7), link/hop-count API consistency + hop-count
  serialization fix (1.3.8), channel outlet/known-dest race fixes (1.3.0).
- **The one big transport change is LOCAL IPC, not the wire.** Upstream rewrote
  the shared-instance RPC (client ↔ *local* rnsd over a Unix/localhost socket)
  from object-mode `conn.send(dict)/conn.recv()` to msgpack byte-mode
  `conn.send_bytes(mp.packb(dict))/mp.unpackb(conn.recv_bytes())`, wrapped in
  try/except. This is the client↔daemon control channel on one box — it does
  **not** touch packets/announces/path-table exchanged *between nodes*, so
  public-net interop (NomadNet/Sideband) is preserved. (Still owed the empirical
  Phase-1 interop proof before fleet-roll — no code signal of a break, but the
  invariant demands the live check.)

### Merge complexity — MODERATE, concentrated
Real dry-run merge of `1.3.8` into `meshforge` (scratch branch, aborted):
- **Only 2 conflicting files:** `RNS/Reticulum.py` (real) and `RNS/_version.py`
  (trivial marker). **`Transport.py`, `__init__.py`, `LocalInterface.py`
  auto-merged clean** → our #69/mf.5, mf.4 logging-lock, and #68 connect patches
  do **not** textually collide with upstream.
- **20 conflict hunks in `Reticulum.py`, all at RPC call sites.** Our sites route
  through the bounded `self._rpc_recv(...)` helper; upstream inlined
  `mp.unpackb(conn.recv_bytes())`. Every shared-instance method (~16:
  `_used/_retain/_unretain_destination_data`, `_retain_identity`,
  `interface_stats`, `path_table`, `rate_table`, `drop_path`, `drop_all_via`, …)
  conflicts for this reason.
- **mf.3/mf.4/#69 patches survived the merge** (present as MeshForge-marked
  regions in the merged file; not in any conflict hunk). ⚠️ *Textual* auto-merge
  ≠ *semantic* correctness — `Reticulum.py` had 557 lines of upstream churn
  around our shutdown/logging patches; the canary + wedge-probe soak must
  re-validate that mf.3/mf.4 still behave (see risks).

### The load-bearing finding — #72 is NOT subsumed
Upstream 1.3.8 `get_rpc_client()` is a bare `multiprocessing.connection.Client()`
with **no timeout**, and every call site does a raw blocking `recv_bytes()` with
**no poll**. Upstream's added try/except catches connection *errors* but does
**not** bound a *hang*: a wedged rnsd that accepts the connection and never
responds (the exact #72 scenario) still blocks `recv_bytes()` forever. So:
- **#72/mf.2's fix is still needed on 1.3.8** — re-implement the bound in the new
  shape: a `_rpc_recv` doing `conn.poll(timeout)` before `recv_bytes()` +
  `mp.unpackb()`, and route all ~16 sites through that single chokepoint
  (combining upstream's msgpack framing with our bound). This is the primary
  reconciliation task.
- By the same logic, **#68 / mf.3 / mf.4 / #69 remain necessary** (upstream added
  no equivalent connect-timeout, detach-bound, logging-RLock, or host-loss-exit).
  The merge retains them; they are re-validated by the existing fork tests
  (`meshforge_local_connect`, `meshforge_rpc_timeout`, `meshforge_detach_timeout`,
  `meshforge_log_reentrancy`, `meshforge_host_loss_exit`) — which must be updated
  for the new RPC framing where they assert on it.

### LXMF 0.9.4 → 1.0.1
- Delta is bounded (12 files, +723/-98). Notable: `LXStamper.py` (proof-of-work
  stamp work + Py3.14 worker ctx-mgr), `LXMessage.py` ("**activated compression
  support signalling**", new **reply/reaction/comment FIELD standards**, "**strip
  null bytes from display names by default**" — mirrors our own B1 hardening),
  atomic message-file writes + write-race fixes (`LXMRouter.py`), blackhole drop.
- **No fundamental message-format break**; new FIELD constants are additive
  (old peers ignore unknown fields). The one cross-version item to verify is
  **compression-signalling** (a 1.0.1 sender compressing to a 0.9.4 receiver).
- Our fork has **no functional patches** → adopting is `git merge <tag>` +
  re-mark `+mf.0`. **Must move in lockstep with MeshAnchor** (`canonical_message`
  is the shared bridge contract; the gateway's `meshforge_*` LXMF fields must
  stay compatible).

---

## Residual risks (what the plan must retire)
1. **Semantic merge of `Reticulum.py`** — textual auto-merge around mf.3/mf.4 is
   not proof; shutdown/logging behavior needs live re-validation (canary the
   clean-stop drill + the SIGTERM/logging-reentrancy tests).
2. **#72 re-port correctness** — the new `_rpc_recv` must bound `recv_bytes()`
   (poll-first), not just wrap it; verify against a deliberately wedged rnsd
   (the `meshforge_rpc_timeout` fork test, updated for byte-mode).
3. **Coordinated per-box upgrade** — the msgpack RPC rewrite means a client and
   its local rnsd MUST be the same major RPC version; a half-upgraded box (new
   client / old rnsd) has broken local RPC. RPC is box-local so each box is
   atomic, but every RNS-importing venv on a box must move with its rnsd.
   Dovetails with the standing "don't rapid-cycle rnsd fleet-wide" caution.
4. **Public-net interop proof** — Phase-1 demands an observed round-trip to
   NomadNet/Sideband on the public net before fleet-roll (no code signal of a
   break, but the invariant is empirical).
5. **MeshForge is the lead repo** for the RNS-reliability arc → land here, prove,
   then port to MeshAnchor; `parity_check.py` must stay green after.

## Proposed plan (if/when the operator greenlights the arc)
1. **RNS 1.3.8 fork branch**: `git merge 1.3.8` into `meshforge`; resolve the 20
   `Reticulum.py` RPC conflicts by adopting upstream's msgpack framing behind a
   re-ported bounded `_rpc_recv`; bump marker `1.2.5+mf.5` → `1.3.8+mf.0`.
2. **Update the 5 fork tests** for byte-mode RPC; keep all wedge/timeout asserts.
3. **Re-run Phase-1 parity**: version marker, rnsd ownership (#69), gateway/map/
   tracer, **public-net interop proof**, MF↔MA `parity_check`.
4. **Canary one box** (the standard rnsd canary), run the wedge probes +
   clean-stop drill + a multi-day soak; only then fleet-roll (coordinated
   per-box, not rapid-cycle).
5. **LXMF 1.0.1** second, in lockstep with MeshAnchor; verify compression
   signalling cross-compat; re-mark `0.9.4+mf.0` → `1.0.1+mf.0`.
6. **Update the SSOT**: `requirements/rns.txt` MF-FORK-PIN block (tag+SHA),
   `scripts/rns_version_check.py` baseline, `persistent_issues.md` fork section,
   each fork's `FORK.md`; re-mark the "stay on 1.2.5+mf.N" decision as superseded.

**Decision to record:** the 2026-06-09 "stay on the 1.2.5+mf.N line" call is
still *defensible* (no forcing function) — but the drift is now 13 upstream
releases, our #72-class fixes are confirmed still-needed-not-subsumed, and the
merge is well-scoped. Recommend scheduling the arc rather than deferring again;
the longer the drift, the larger the eventual `Reticulum.py` reconciliation.

---

## PHASE 1 EXECUTED (2026-07-17, Fable 5) — fork branch merge + tests

**Status: DONE and validated on the fork integration branch. NOT fleet-rolled.**

- **Branch:** `meshforge-138` (fork `Nursedude/reticulum`), pushed to origin.
  Merge commit `6a90d2bb` (two parents: `83f4be33` mf.5 + `dca2a928` 1.3.8) +
  FORK.md `9c8ce788`. The deployed `meshforge` branch **stays at `1.2.5+mf.5`**
  — fleet-roll is gated on canary+soak (Phase 4 below).
- **Conflicts** exactly as the eval predicted: `Reticulum.py` (20 RPC hunks) +
  `_version.py`. Everything else auto-merged; mf.3/mf.4/#68/#69 survived.
- **#72 re-ported** onto msgpack byte-mode: `_rpc_recv` now does
  `poll(RPC_TIMEOUT)` then `mp.unpackb(recv_bytes())`; all 21 client sites route
  through it, keeping upstream's per-site try/except AND the wedge bound. Server
  `conn.recv_bytes()` untouched. Confirmed the resolved `Reticulum.py` diffs
  from pure 1.3.8 ONLY by our known patches (RPC_TIMEOUT, `_rpc_recv`, mf.4
  signal-defer, #69 wanted_host).

### The unpredicted finding — mf.4 RLock had to be re-ported, not carried

Live re-validation on 1.3.8's own link/resource suite (the eval's residual
risk #1 — "shutdown/logging behavior needs live re-validation") surfaced a real
regression the textual auto-merge hid: the original mf.4 `logging_lock`
Lock→**RLock** flaked LOG_EXTREME resource transfers. **Controlled A/B on clean
1.3.8, same worktree:** plain `Lock` = 9/9 link-suite pass; `RLock` = 2/5 fail
(proof-delivery timeouts). Root cause: RLock's per-acquire overhead on the hot
logging path under EXTREME's log volume. Production never hit it (fleet runs low
log levels), but shipping a merge flakier than upstream is unacceptable.

**Cured structurally, keeping a plain Lock:** the self-deadlock mf.4 used the
RLock to tolerate — `log()`'s on-write-failure fallback re-calling `log()` under
the lock — now runs the fallback re-log *after* releasing the lock (fresh
acquire, no reentry). Normal logging is the upstream path byte-for-byte.
`meshforge_log_reentrancy` rewritten to pin **deadlock-freedom + plain-Lock**,
not the RLock impl detail. Link suite: **6/6 clean (~20s, matching upstream).**
Lesson (now in FORK.md): a carried patch must be re-validated against the new
base, not assumed correct because the merge was textually clean.

### Validation (PYTHONPATH pinned to the merged working tree)
- Fork tests: rpc_timeout 3, local_connect 4, log_reentrancy 5, host_loss_exit
  14, detach_timeout 4/5 (blocking_remote's bound proven by direct exercise; its
  pytest exit-hang is a pre-existing `daemon=False` harness quirk, identical on
  clean `meshforge`). rpc_timeout updated for byte-mode framing.
- Upstream suite: hashes/identity/channel pass; link **6/6 clean**.
- mf.3 detach bound re-validated directly (remote/local/shared all return within
  DETACH_TIMEOUT); upstream's new `BackboneInterface.deregister_listeners()` is
  correctly absorbed *inside* the bound.

### PHASE 2 EXECUTED (2026-07-17, Fable) — LXMF 1.0.1 merge + lockstep proof

**DONE on fork branch `meshforge-101` (LXMF `Nursedude/lxmf`), pushed. NOT
fleet-rolled** (deployed `meshforge` stays `0.9.4+mf.0`). Merge `94b08af`
(2 parents: `66c48cf` mf.0 + `a29c4a0` 1.0.1); FORK.md updated.

- **Clean adoption:** the fork has 0 functional patches, so all `LXMF/` library
  code is **byte-identical to upstream 1.0.1** — merge conflicted only on
  `_version.py`. Imports as `1.0.1+mf.0`, all modules compile. No test suite
  ships upstream.
- **Lockstep verified (MF + MA share this fork; canonical_message is a
  byte-locked twin — confirmed byte-identical MF↔MA):**
  - *Compression cross-compat SAFE.* 1.0.1 adds compression *signalling*
    (`compression_support_from_app_data`), not compression — `RNS.Resource`
    always defaulted `auto_compress=True`, so 0.9.4 already compressed and
    decompression is symmetric at the RNS layer. A 1.0.1 sender to a 0.9.4 peer
    sees a 2-element announce (`len<3` → True) and compresses exactly as before;
    the new behavior only lets a sender SKIP compression for a peer that
    explicitly opts out (no MF/MA peer does). Safe across a mixed-version roll —
    the eval's one cross-version worry is retired.
  - *No FIELD collision.* New numeric fields (REPLY_TO 0x30, REPLY_QUOTE 0x31,
    REACTION 0x40, COMMENT 0x41, CONTINUATION 0x42) are additive; both gateways
    key bridge fields by STRING (`meshforge_*`), which coexist with LXMF's
    numeric keys in the msgpack fields dict. No new field-key validation in the
    pack path. **Proven by round-trip** (string+numeric keys survive
    packb/unpackb, meshforge_* intact) on LXMF 1.0.1+mf.0.
  - `canonical_message` has no LXMF FIELD/version dependency.

### Still owed before fleet-roll (Phases 3–4, need hardware + days + operator)
2. **Phase-1 parity on MeshForge side**: `parity_check.py`, `rns_version_check`,
   the two RNS-wedge probes — but do NOT bump the fleet SSOT baseline
   (`requirements/rns.txt` MF-FORK-PIN, `rns_version_check.py`) until the roll,
   or every un-upgraded box fails the check.
3. **Public-net interop proof** — observed round-trip to NomadNet/Sideband.
4. **Canary ONE box** (pip-install `meshforge-138`), wedge probes + clean-stop
   drill + multi-day soak, THEN coordinated per-box roll (client+rnsd together;
   RPC is box-local so each box is atomic). Only then fast-forward `meshforge`
   and bump the SSOT.

### PHASE 1 SELF-REVIEW (2026-07-17, Fable) — 2 adversarial reviewers on the RNS resolution

Per [[feedback_review_your_own_fixes]] — the merge resolution is self-applied,
unreviewed, fleet-critical code. Two adversarial reviewers (RPC reconciliation;
mf.4/mf.3/#69), every finding verified against source before counting.

**Verdict: the merge is clean — NO stitch defect.** All 21 client recv sites
route through the bounded `_rpc_recv` (incl. the NEW upstream `is_blackholed`
site; no raw-recv hang left); server `rpc_loop` recv correctly stays raw;
`_rpc_recv` faithful; diff-clean vs pure 1.3.8 (only the 6 known patches);
mf.3 detach bound byte-identical to pre-merge (upstream's new
`deregister_listeners()` inside the bound); #69 `wanted_host` + listener check
survive 1.3.8's BackboneInterface `@rns/{name}` binding.

**One code finding, FIXED (F1, LOW/latent):** the mf.4 re-port kept the plain
Lock but still invoked `logcall()` (LOG_CALLBACK) *inside* the lock — a handler
that re-logs synchronously would self-deadlock (a coverage narrowing vs mf.4's
RLock). Unreachable on the fleet (rnsd = LOG_FILE; no `src/` LOG_CALLBACK
consumer), zero canary effect, but fixed to finish the re-port honestly: the
callback dispatch now runs OUTSIDE the lock (same structural approach as the
fallback re-log). Red-test-first (`test_log_does_not_deadlock_when_callback_re_logs`);
link suite still clean.

**Two OPERATIONAL hazards for the roll runbook (not code defects — record here):**
- **[HIGH] Wire-format change × #79 deploy-restart gap.** The shared-instance RPC
  went object-mode (pickle `send/recv`) → msgpack byte-mode (`send_bytes/
  recv_bytes`) between mf.5 and 1.3.8+mf.0. A box mid-roll where **rnsd flips to
  1.3.8 but a still-running client on OLD code** (esp. USER daemons — nomadnet,
  meshforge-map — which #79 historically did NOT restart on `git pull`) has
  **every** RPC time out at 8s *indefinitely* until that client is manually
  restarted (map collector gets no path table; `rnstatus` looks wedged).
  `_rpc_recv` bounds it to 8s-per-call (not an infinite hang — both fork versions
  carry it), which de-risks but does not remove it. **Roll gate: restart rnsd
  AND every RNS-importing process on the box together (rnsd-first), and confirm
  the #79 `sync_user_unit`/update.sh hooks cover nomadnet + meshforge-map BEFORE
  canarying.**
- **[LOW] `get_rpc_client()` connect is still unbounded** (bare `Client()`, no
  timeout — upstream, pre-existing). Connecting to a half-restarted rnsd whose
  RPC listener exists but isn't accepting can block in `connect()` before
  `_rpc_recv` is reached. Runbook note; not merge-introduced.

### PHASE 3 EXECUTED (2026-07-17, Fable) — interop proof + canary LIVE on moc3

**Interop proof (wire-compat invariant, empirical leg) — VERIFIED, 3 round-trips:**
scratch peers on VolcanoAI, A = merged trees via PYTHONPATH (`1.3.8+mf.0`/
`1.0.1+mf.0`), B = installed fleet pair (`1.2.5+mf.5`/`0.9.4+mf.0`), isolated
configdirs (`share_instance=no`, no AutoInterface — fleet rnsd untouched).
1. **Direct TCP** A↔B: LXMF DIRECT-link ping + reply both directions, ~1s.
2. **Public net**: both peers clients of `aspark.uber.space:44860` (foreign
   stock transport); announce propagation, path resolve (2 hops), LXMF
   round-trip THROUGH the public node. (Dublin testnet offline; acehoss
   IPv6-unreachable, betweentheborders down — aspark + rns.dismail.de were up.)
3. **Real fleet net**: `lab.lxmf_tracer` from VolcanoAI (1.2.5+mf.5) → moc3's
   lab-echo running 1.3.8+mf.0 — `result=ok rtt_ms=7315`.

**Canary flip moc3 (06:54–07:00 HST):**
- **THREE envs flipped** (the roll-surface finding): operator user-site
  (`~/.local/lib/python3.13/site-packages` — hosts rnsd-as-wh6gxz, gateway,
  echo), root `/usr/local/.../dist-packages`, and the **nomadnet pipx venv**
  (`~/.local/share/pipx/venvs/nomadnet`, via `pipx runpip`, no pip binary).
  ⚠️ That venv was **silently stock rns 1.1.4** — never converged to any fork
  pin, honestly invisible to `probe_rns_version_drift` (venvs out of scope).
  **Roll runbook: enumerate + flip every box's pipx venvs with the box.**
- Order held: stop clients → restart rnsd → clean-stop drill → start clients.
  Installs by SHA (`rns@6dadb335`, `lxmf@94b08af`), `--no-deps
  --force-reinstall` (deps present; avoids pip pulling stock rns via lxmf).
- **Clean-stop drill: 4.5s SIGTERM-clean** ("Deactivated successfully", incl.
  RNode serial teardown; no 15s SIGKILL) — mf.3/mf.4 behavior held on 1.3.8.
- **mf.5/#69 LIVE-FIRED, by design**: the drill's tight stop→start let a
  watchdog-spawned `rnstatus` boot-claim `@rns/default` (#69 race); rnsd came
  up client, claimant exited, mf.5 exit-75 → systemd restart into host role,
  ~30s self-heal (log: "no listener remains after 3 reconnect attempts...
  exiting (code 75)"). NRestarts=1, no crashloop. Space restarts at roll.
- Transitional skew observed as predicted: new-code `rnstatus` vs old rnsd →
  one "unpickling stack underflow" server-side; watchdog `rns_rpc_unresponsive`
  fired during the window and CLEARED 06:57:30.
- **Post-flip VERIFIED**: `@rns/default` + `/rpc` owned by rnsd; serving 3
  programs; gateway msgpack RPC ok (`rpc[rnsd.path_table_read] ok 0.000s`);
  nomadnet tmux up (its `rnstatus` ExecStartPre gate passed on 1.3.8); echo
  chokepoint preflight OK; `rns_version_drift` fired degraded = the DELIBERATE
  canary marker (do NOT converge moc3 during the soak).

**REMAINING (Phase 4)**: multi-day soak (mini + wedge probes + kilo edges +
conf_rate watch moc3), then coordinated per-box roll (rnsd + ALL clients +
pipx venvs together, rnsd-first), then ff `meshforge` branches + bump SSOT
(`requirements/rns.txt` MF-FORK-PIN, `rns_version_check`), `parity_check`
green after.

---

## ⛔ PHASE-4 ROLL BLOCKER found 2026-07-19 — read before rolling any box

**`lxmf 1.0.1+mf.0` declares `Requires-Dist: rns>=1.3.5` — unpinned and
PyPI-resolvable.** Verified on the moc3 canary from the installed METADATA.

Why that blocks the roll:

1. **Stock `rns 1.3.9` OUTRANKS the fork `1.3.8+mf.0`.** Under PEP 440 the
   release segment decides first (`1.3.8 < 1.3.9`); a `+mf.0` local tag cannot
   win. So any pip resolution permitted to upgrade `rns` will REPLACE the fork
   with stock — silently dropping the #72 `_rpc_recv` poll fix, the mf.4
   logging-lock/RLock cure, and the mf.5 exit-75 stranded-client cure. The
   version string still looks newer, which is exactly how it would pass a
   casual eyeball.
2. **The un-rolled fleet baseline `1.2.5+mf.5` does NOT satisfy `rns>=1.3.5`.**
   So installing the new LXMF on the seven un-rolled boxes *forces* an rns
   upgrade. If the rns fork's git pin is not in the SAME resolution, pip takes
   stock 1.3.9 from PyPI. That is a fleet-wide fork loss in one command.

**This is not theoretical — it already happened on the canary.** moc3's
`rns_stray_env_drift` fired 2026-07-19 11:58 + 12:33 with
`venv=1.3.9` beside `system-dist / user-site / user-pipx:nomadnet = 1.3.8+mf.0`
— stock rns had landed in `/opt/meshforge/venv`, the SERVICE venv. It cleared
at 12:34 and moc3 is coherent again (re-verified: all five env locations at
`1.3.8+mf.0` / `1.0.1+mf.0`), but the mechanism that put it there is still live.

### Required before the roll proceeds

- Install BOTH forks from `requirements/rns.txt` in ONE resolution (the file
  already carries both git pins) — never `pip install lxmf` alone, and never a
  bare `--upgrade` touching either package.
- Prefer `--no-deps` for the two fork packages so `rns>=1.3.5` cannot be
  re-resolved against PyPI at all, installing their real deps separately.
- After EVERY box, re-run the env scan across all locations (venv,
  system-dist, system-local, user-site, user-pipx, root-pipx) and confirm
  `+mf.N` on every copy BEFORE touching the next box. `rns_version_check`
  alone is not enough — it reads one consumer, not the strays.
- Consider pinning `rns==1.3.8+mf.0` (or `<1.3.9`) in the lxmf fork's own
  metadata so the footgun cannot fire from any install path. That is the
  durable cure; the procedural rules above are the interim guard.

### Soak status at the time of this finding

Canary live since 2026-07-17 (~3 days). RNS-shaped mini fires since:
`rns_rpc_unresponsive` once at 07-17T06:57 (2.5 min after cutover — the flip
itself, never recurred) and the two `rns_stray_env_drift` fires above.
`rns_version_drift` remains the DELIBERATE canary marker. No wedge, no RPC
failure, no delivery regression in the window — the FORK looks good; it is the
INSTALL PATH that is unsafe.

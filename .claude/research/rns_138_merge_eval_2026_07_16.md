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

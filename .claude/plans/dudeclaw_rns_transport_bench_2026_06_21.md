# Bench Plan — RNS Wire-Compatibility for a claw-as-Transport-Node

> **2026-06-21 · scoping doc (BELIEVED design; groundings VERIFIED from codebase + vendor READMEs).**
> Gates the Cluster-A / Tier-2 "host-free RNS transport node on a claw" decision in
> `.claude/research/dudeclaw_role_usecases_2026_06_21.md`. Companion to that report (don't rehash).

---

## 1. Objective & the decision it feeds

**Question:** Does **microReticulum** (the C++ Reticulum port that RTNode-HeltecV4 firmware uses)
interoperate **on the wire** with the MeshForge fleet's owned fork **`rns 1.2.5+mf.5`**, well enough
to act as a **Transport Node** carrying the gateway's real traffic (LXMF announces → path → Link →
**Resource**)?

**Decision gated:** PASS → pilot a *dedicated* claw RNS-transport node feeding the fleet RNS backbone.
FAIL/PARTIAL → fall back to the proven **RNode-modem** path (§3) or keep RNS on the Pi (status quo).

**Wire-compat invariant being asserted** (persistent_issues.md:39-41, non-negotiable):
unchanged crypto primitives **Ed25519 / X25519 / AES-256-CBC / Fernet** + unchanged
**packet / announce / path-table wire format**. `+mf.5` is byte-identical to stock `1.2.5` except the
`+mf.N` marker, so it stays compatible with the public Reticulum net — *a claw must honor the same.*

---

## 2. The central risk (and what's encouraging)

**Encouraging [V, vendor README]:** microReticulum implements the *same* primitives our invariant
names — Ed25519, X25519, AES/AES-CBC, HKDF, HMAC, PKCS7, **Fernet** — plus **Link** and **Resource**
and basic **Transport**. The stack is complete in principle.

**The risk [V, by absence]:** microReticulum states **no explicit Reticulum protocol/wire version**
and ships **zero documented interop testing against Python RNS**. RTNode pins microReticulum **0.2.4**
(library is now 0.4.1 — a meaningful gap). The packet/announce/path-table *format alignment* vs our
`1.2.5+mf.5` is simply **unknown** — that is the whole reason this bench exists. RTNode itself forwards
packets/announces/path-requests/proofs only (no Link/Resource *as a node feature* — correct for a
transport node; those ride end-to-end through it), with a **24-entry path table** (scale caveat).

---

## 3. Two architectures — frame the choice up front

| | **A. Host-free (what we're testing)** | **B. RNode-modem (proven fallback)** |
|---|---|---|
| Claw firmware | RTNode-HeltecV4 (microReticulum 0.2.4) | Mark Qvist's stock **RNode firmware** (LoRa modem only) |
| Who runs Reticulum | the claw itself | a co-located Pi running **our `+mf.5` rnsd** via `RNodeInterface` |
| Wire-compat risk | **UNKNOWN — this bench** | **none** (it *is* our fork) |
| Appeal | no host computer; the RTNode selling point | needs a Pi, but zero protocol risk; templates already support it |

`RNodeInterface` (with US LoRa slots, e.g. `frequency=903625000 bandwidth=250000 sf=7 cr=5`) is
**already a supported interface type** in `templates/reticulum.conf`. So Architecture B is low-risk and
available today — the bench's job is purely to decide whether A's host-free benefit is *attainable*.

---

## 4. Safe isolation rig (do this exactly — #69/#82 rails)

⚠️ **The mf.5 soak runs to 2026-06-24.** Do NOT run this on a production gateway box mid-soak, and
**never touch the production `rnsd` unit** (no restart, no config edit). The bench rnsd is a *separate
process* on a *separate everything*. Prefer a dedicated bench Pi or a non-gateway box.

**Throwaway test rnsd — distinct configdir / instance / ports** (so it can never collide with the
production shared instance `@rns/<instance>`):

```ini
# /tmp/rns_bench/config   — run: rnsd --config /tmp/rns_bench --service
[reticulum]
  enable_transport = Yes
  share_instance   = Yes
  instance_name    = bench rns        # → @rns/bench   (NOT @rns/<prod>)
  shared_instance_port  = 37430       # NOT prod 37428
  instance_control_port = 37431       # NOT prod 37429
[interfaces]
  [[Bench TCP Server]]
    type = TCPServerInterface
    enabled = yes
    listen_ip = 127.0.0.1             # or LAN-bind only for the claw; NO uplink TCPClient → air-gapped
    listen_port = 4243                # NOT prod 4242 — this is the claw's backbone target
```

**Rails (each a hard rule):**
- **Verify the fork first:** `python3 scripts/rns_version_check.py` → must report `rns 1.2.5+mf.5`
  (else `pip install --force-reinstall -r requirements/rns.txt`). A bench against the wrong binary is
  worthless.
- **Distinct names/ports only:** never reuse `@rns/<prod>`, `:37428/:37429`, or `:4242`.
- **Air-gap the bench:** the bench config has *no* `TCPClientInterface` to the regional uplink — it
  must not announce into the live fleet.
- **Guard the production socket:** run `check_rns_listener_owner(<prod instance>)` (from
  `src/lab/_lab_common.py`) before and after — confirm `@rns/<prod>` owner is still production rnsd,
  untouched.
- **Point CLI tools at the bench:** `rnstatus`/`rnpath`/`rnprobe`/`rncp` all take `--config /tmp/rns_bench`
  so they attach to `@rns/bench`, never production. (Tools live in `~/.local/bin`.)
- **Don't promote the claw to the fleet** until it passes — keep it on the isolated bench net.

---

## 5. Test ladder (phased, cheapest-and-safest first)

### Phase 0 — Pre-flight (no hardware, no fleet risk)
- `rns_version_check.py` green; spare **Heltec V4 (16 MB flash / 2 MB PSRAM)** on hand (RTNode needs the
  full 16 MB; the WireClaw build only uses 4 MB partitions → **this is a dedicated board**, not `dudeclaw-01`).
- Stand up the bench rnsd (§4); `rnstatus -c /tmp/rns_bench` shows the TCPServerInterface up, 0 peers.

### Phase 1 — ★ Pure protocol interop, software-only (the de-risker)
microReticulum has a **native (Linux/macOS) build** and a `test_interop/` dir (`packet_interop_sender`),
and a `UDPInterface` "for testing." Build microReticulum native and run it against a Python `+mf.5`
instance over localhost UDP — **no flashing, no LoRa, no fleet, instant + repeatable.**
- **T1.1 Announce interop:** microReticulum-native announces a destination → the `+mf.5` side sees it in
  `rnstatus`/`rnpath` (and vice versa). *Proves announce wire format.*
- **T1.2 Link:** establish an RNS Link microReticulum↔`+mf.5`. *Proves packet + link handshake + proof.*
- **T1.3 Resource:** transfer a multi-KB payload over the Link (the layer the gateway actually depends
  on — the wx-loss class proved Resource receive-assembly is a real failure mode). *Proves segmentation/
  reassembly wire format.*
- **PASS Phase 1** = announces discovered + Link establishes + Resource arrives intact, **both directions**.
  → This alone answers "is the wire format compatible." If it fails here, the firmware can't help.

### Phase 2 — Claw firmware on the bench (TCP leg)
Flash the spare V4 with **RTNode-HeltecV4**; via its web captive portal set **Backbone Host = `<bench
box LAN ip>`, Backbone Port = 4243**, MODE_BOUNDARY; LoRa params = the fleet slot.
- **T2.1:** claw's `TCPClientInterface` connects → `rnstatus -c /tmp/rns_bench` shows the claw peer up.
- **T2.2:** claw announces / is discovered; `rnpath -c /tmp/rns_bench` resolves a path *to* the claw.
- **T2.3:** `rnprobe` reachability to a destination *behind* the bench rnsd, from the claw side and back.
- **PASS Phase 2** = claw peers over TCP and announces/paths cross the boundary with the `+mf.5` instance.

### Phase 3 — Full transport-forwarding path (the real role)
Add a **far LoRa endpoint** — a second LoRa RNS node (another RNode, or a Pi + `RNodeInterface`) that
can reach the bench rnsd **only through the claw's LoRa→TCP bridge**. Now traffic must traverse the claw.
- **T3.1 Reachability:** `rnprobe` far-endpoint → a destination on the bench-rnsd side (path:
  LoRa-endpoint ⇒ *claw LoRa→TCP* ⇒ bench rnsd). *Proves the claw forwards announces + paths across the boundary.*
- **T3.2 Resource:** `rncp` a file across that path (or the PINGBIG/ACKBIG echo in `_lab_common.py`,
  which forces a >4 KB reply → delivered as an **RNS Resource over a Link**). *Proves the claw relays
  Link/Resource packets transparently.*
- **T3.3 LXMF end-to-end (the gateway's actual traffic):** run **`scripts/validate_rns_to_mesh.py`**
  (isolated client config → `RNS.Transport.request_path` → LXMF send → delivery confirm) so an LXMF
  message goes announce → path → Link → delivery **through the claw**.
- **PASS Phase 3** = T3.1–T3.3 all succeed → the claw is a viable `+mf.5`-compatible transport node.

### Phase 4 — Soak + scale caveats
- Run T3.1–T3.3 on a loop for **≥24 h**; watch the claw's **24-entry path table** under realistic
  announce volume (does it thrash/evict?), heap/uptime stability, and TCP reconnect behavior.
- **PASS Phase 4** = stable over the soak with no path-table-exhaustion drops.

---

## 6. Decision gate & fallbacks (honest)

- **All phases PASS** → proceed to a dedicated claw-transport pilot (Cluster-A/T2). Still: it must never
  change wire format, and re-bench on any microReticulum/RTNode bump (treat like our fork-merge gate).
- **Phase 1 FAILS (wire mismatch)** → host-free is **not viable now**. Options: (a) retest with newer
  microReticulum (0.4.1) / a newer RTNode build — protocol fixes may have landed since 0.2.4; (b) **fall
  back to Architecture B** (RNode-modem + Pi `+mf.5` rnsd — zero wire risk, available today); (c) keep
  RNS on the Pi and use the claw purely as a Meshtastic-interop relay (Cluster-A/T1, status quo).
  *Do NOT attempt to patch microReticulum's wire format to match — that's forking the network.*
- **Phase 1 passes but 3 fails (transport-forwarding gap)** → microReticulum endpoints interop, but the
  RTNode *forwarding* path doesn't carry our LXMF cleanly → Architecture B fallback.

The **fallback (B) is strong**: `RNodeInterface` is already in our templates and *is* our exact fork, so
"altitude/range LoRa backbone via a claw" is achievable regardless of this bench's outcome — the bench
only decides whether we get it **host-free**.

---

## 7. Materials, effort, reuse

- **Hardware:** 1 spare Heltec V4 (RTNode) + (Phase 3) 1 more LoRa RNS node (RNode or Pi+RNodeInterface).
- **Software reuse (no new code for Phases 0–3):** `scripts/rns_version_check.py`,
  `scripts/validate_rns_to_mesh.py` (isolated client + LXMF delivery probe), `src/lab/_lab_common.py`
  (PINGBIG/ACKBIG Resource echo, `load_or_create_identity`, `check_rns_listener_owner`), stock
  `rnstatus/rnpath/rnprobe/rncp`. `tests/e2e/harness/rns_localnet.py` is an empty placeholder (design
  context only).
- **Effort:** Phase 1 ~½ day (pure software, the high-information step — do this *first*); Phases 2–3
  ~1 day with hardware; Phase 4 a 24 h soak.

---

## 8. Open variables to record per run
- microReticulum version actually tested (0.2.4 vs 0.4.1) + RTNode commit (README pins neither precisely).
- Which layer first diverges if it fails (announce / link / resource) — that's the upstream bug report.
- Path-table eviction behavior at >24 routes.
- microReticulum's effective protocol version (undocumented — infer empirically from what interops).

## 9. Calibration
Everything here is **[B] design** until run. The config shapes, ports, tool list, reuse helpers, and the
wire invariant are **[V]** (read from the live config + repo this turn). No claim of compatibility is made
— that is precisely what Phase 1 measures. "Phase 1 passed once" ≠ compatible: require both-direction
success **and** the Phase-4 soak before calling it reliable.

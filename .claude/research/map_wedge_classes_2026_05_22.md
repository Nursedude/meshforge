# The `meshforge-map` :5000 wedge classes — GIL-bound serialization, and its lookalikes

> Issues #70 / #71 (2026-05-22), with the differential diagnosis against #17,
> #73, #75, #76 and the 2026-06-23 spin arc.
>
> `http_local_unresponsive` on port 5000 has been raised by at least six
> different faults. They share one symptom and need completely different cures,
> so the useful knowledge is not "the map wedges" — it is **which class this
> one is**. This is the repo copy of that knowledge, so the offline oracle can
> answer it on any box rather than only the one holding the operator's memory
> corpus.

---

## The class: GIL-bound serialization on a big JSON body

**Symptom**: the map service on port 5000 goes unresponsive every few hours,
under load, with no crash, no traceback and no restart. It recovers on its own
or on a watchdog restart, then does it again.

**Mechanism**: an endpoint serializes a very large response inside the request
handler. `json.dumps` and `gzip.compress` are CPU-bound C calls that **hold the
GIL** for their whole duration, so for those seconds the process serves nothing
else — not another request, not `/healthz`. Federation peers keep polling on
their own schedule, their requests queue behind the one holding the GIL, and the
watchdog's 2 s `/healthz` probe times out. The watchdog then reports the service
unresponsive and restarts it, which "fixes" it until the next big body.

Nothing is deadlocked. Nothing leaked. The process is busy in a way that is
invisible to every liveness check that needs the interpreter to answer.

**The measured instances**, all the same shape at different magnitudes:

| endpoint | raw body | serialization cost | issue |
|---|---|---|---|
| `/api/nodes/directory` | 35 MB | ~6–10 s | #70 |
| `/api/nodes/geojson` | 47 MB | ~35 s cold | #71 |
| `/api/network/topology` | 24 MB | seconds | #71 |

⚠️ A data cache does **not** fix this. The geojson collector already cached the
DATA (`_cached_geojson`); the handler still serialized and gzipped it on every
single request. Caching the *inputs* to an expensive serialization leaves the
expensive serialization exactly where it was.

---

## The cure: cache the SERIALIZED BYTES, with single-flight rebuild

`ResponseByteCache` (originally `DirectoryResponseCache`) stores the finished
`(raw, gzip)` byte pairs under a short TTL, so a cache hit skips `json.dumps`
and `gzip.compress` entirely and the GIL is never held for seconds.

Design points that matter, each earned:

- **Two locks, not one.** `_entries_lock` guards short dict critical sections;
  `_build_lock` coalesces concurrent misses into a **single-flight** rebuild.
  Without the second lock, N simultaneous cold requests each run the full
  serialization — the stampede is worse than the original bug.
- **`get_or_build(key, build_fn) -> (raw, gz, was_built)`.** Concurrent callers
  wait and inherit the originating caller's build. `was_built` lets the caller
  fire size observers only on a real miss.
- **Serve the right variant per request** from cached bytes based on
  `Accept-Encoding`, rather than re-compressing.
- **Short TTLs, chosen per endpoint**: 5 s for directory and topology, 2 s for
  geojson (a live map has tighter freshness expectations than a directory).
  Geojson is keyed by `(bbox, region, preset)`; presets whose serialized bytes
  are byte-identical collapse onto one key.
- **Never cache a failure.** A DB error returns 500 and leaves the entry empty.
- **Expose the stats** at `/api/status.directory.cache`
  (hit / miss / coalesced / entry_count / ttl_s) so the cure has an always-on
  witness instead of being invisible until it stops working.

Measured after the fix, same endpoint: **1.780 s miss → 0.056 s hit** (32x), and
0.003 s for a gzip hit.

---

## ⚠️ THE DECISION TELL: any NEW `http_local_unresponsive` is a NEW class

All three known GIL-serialization instances are cured. So a fresh
`http_local_unresponsive` on :5000 is **not** this class by default — reaching
for another response cache is the wrong first move. Work the differential:

| tell | class | cure |
|---|---|---|
| 30 s+ journal silence preceded by a big-body endpoint (`/api/nodes/*`, `/api/network/topology`) | **GIL serialization** (#70/#71) | response byte cache (all known instances already cached) |
| `[Errno 24] Too many open files`, fd count climbing | **fd leak** (#73 — an MQTT client leaked per reconnect until 1024 fds) | restart the map, then find the leak |
| `rnstatus` itself hangs / RPC wedged | **RNS class**, not a map bug at all (#68/#72) | restart `rnsd`, then RNS-using services |
| the radio's web client goes deaf while RX is healthy | **leaked `TCPInterface` starving :9443** (#75) | restart the map; a persistent TCP drains the PhoneAPI stream |
| an endpoint "available" forever that never worked | **dead probe** (#76 — `/json/*` is ESP32-only and meshtasticd never served it) | tri-state the probe: ok / absent / down |
| a request blocking on a slow collection | **serving coupled to collection** (2026-06-23 spin) | bound the collect; serving must never block on it |

**Distinguishing GIL pile-up from a deadlock**: a deadlock usually leaves a
traceback at shutdown; a GIL pile-up leaves **silence** — the process is running
and simply never gets a chance to log. Check
`journalctl -u meshforge-map --since '<event_time> -5s'` and look for the silent
gap and which endpoint precedes it.

---

## The invariant underneath all of them

The operator's reading, after the sixth instance (2026-06-23): *"if it's
persistent, that itself says something about our approach."* Patching instances
never retires a class. Every one of these — #17 TCP contention, #70/#71 GIL
serialization, #73 fd leak, #75 interface leak, #76 dead probe, the spin, the
ttyACM0 seizure, the false `HOST_FROZEN` — is one shape:

> **a degraded or ambiguous state mapped to a confident action.**

"geojson slow" → block the request. "tcp empty" → grab whatever USB radio
exists. "no SSH banner in 800 ms" → declare the host frozen.

Two invariants now carry it instead of vigilance, and both are lint/guard-pinned
rather than remembered:

1. **Serving never blocks on collection** — lint MF023 (AST, function-scoped)
   plus `TestServingNeverBlocksOnCollection`.
2. **Detectors never map an ambiguous state to a definitive one** —
   `TestDetectorHonesty`; e.g. `HOST_FROZEN` now requires kernel hung-task
   corroboration (`kstack==1`) before it will page, because a merely slow box
   answering no banner is not a frozen one.

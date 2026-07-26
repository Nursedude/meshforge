"""Parse the dude-claw's free-text NATS telemetry into structured fields and
assemble the last-tick record that /api/status + the /fleet rollup read.

The claw firmware answers two ``tool_exec`` calls with human-readable strings
(no structured JSON — a firmware/fork change is deferred). We parse them ONCE
at capture time on the claw-brain box (``scripts/claw_metrics_push.py``) and
persist ``claw_last_tick.json`` so the display surfaces read clean fields, not
free text, and never make a synchronous NATS call inside an HTTP handler.

Honest-failure contract (``.claude/rules/honest_failure_modes.md``):
  * a field absent from the string parses to ``None`` (unknown) — never a
    fabricated ``0`` that would read as a real measurement;
  * a failed/absent NATS reply (or an ``ok`` reply whose result we cannot
    parse) yields ``None`` for that half plus an explicit ``errors`` entry —
    a degraded capture must not be mistakable for healthy telemetry;
  * ``reachable`` (did the device answer ``device_info``) is the liveness fact
    consumers must read; ``ok`` tracks it. An ACCESSORY the device does not
    have — no BLE scanner, no battery gauge — is reported in
    ``degraded_optional``, NOT folded into ``ok``: pinning a BLE-less claw at
    ``ok: false`` forever (as this module did until 2026-07-19) teaches every
    reader to ignore the flag, which is how a real failure hides.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ._util import iso_or_none

# device_info: "Free heap: 17764 bytes, Total heap: 210492 bytes,
#   Uptime: 109368 seconds, WiFi: connected (rssi -37 dBm), IP: <ip>,
#   Chip: ESP32-S3 rev 2, 2 cores, 240 MHz"
_RE_HEAP_FREE = re.compile(r"Free heap:\s*(\d+)", re.IGNORECASE)
_RE_HEAP_TOTAL = re.compile(r"Total heap:\s*(\d+)", re.IGNORECASE)
_RE_UPTIME = re.compile(r"Uptime:\s*(\d+)", re.IGNORECASE)
_RE_WIFI = re.compile(r"WiFi:\s*(connected|disconnected)", re.IGNORECASE)
_RE_RSSI = re.compile(r"rssi\s*(-?\d+)\s*dBm", re.IGNORECASE)
_RE_IP = re.compile(r"IP:\s*([0-9.]+)")
_RE_CHIP = re.compile(r"Chip:\s*([^,]+)")

# ble_stats: "ble_adv_age_s: 0 (advs 767422, uniq 32+, last rssi -59 dBm,
#   restarts 0/0, window 48/320ms)"
_RE_BLE_AGE = re.compile(r"ble_adv_age_s:\s*(\d+)", re.IGNORECASE)
_RE_BLE_ADVS = re.compile(r"\badvs\s*(\d+)", re.IGNORECASE)
_RE_BLE_UNIQ = re.compile(r"\buniq\s*([^\s,)]+)", re.IGNORECASE)
_RE_BLE_LAST_RSSI = re.compile(r"last rssi\s*(-?\d+)\s*dBm", re.IGNORECASE)
_RE_BLE_RESTARTS = re.compile(r"\brestarts\s*([^\s,)]+)", re.IGNORECASE)
_RE_BLE_WINDOW = re.compile(r"\bwindow\s*([^\s,)]+)", re.IGNORECASE)


def _int(rx: re.Pattern, s: str) -> Optional[int]:
    m = rx.search(s)
    return int(m.group(1)) if m else None


def _str(rx: re.Pattern, s: str) -> Optional[str]:
    m = rx.search(s)
    return m.group(1).strip() if m else None


# battery_read: "Battery: 4.06 V (adc 829 mV)". The FIRST voltage is the pack
# reading; the parenthesised adc millivolts is a raw sample, never the answer.
_RE_BATTERY_V = re.compile(r"([\d.]+)\s*V\b", re.IGNORECASE)


def parse_battery(result: Any) -> Optional[Dict[str, Any]]:
    """Parse a ``battery_read`` result into ``{volts, raw}``.

    Returns ``None`` when there is no parseable voltage — the caller records
    that as a witness and leaves the reading unknown. NEVER returns 0.0 for an
    unreadable pack: a fabricated 0 V would breach every low-battery spec and
    read as a dying node (honest_failure_modes #1 — the degraded value must not
    overlap the healthy domain).
    """
    if not isinstance(result, str) or not result.strip():
        return None
    m = _RE_BATTERY_V.search(result)
    if not m:
        return None
    return {"volts": float(m.group(1)), "raw": result.strip()[:120]}


# lora_stats: "mesh_heard_age_s: 4 (heard 158224 pkts, crc_err 1461, runts 1,
#   last from=!79be01d3 to=!ffffffff ch=0x08 rssi=-41 snr=6.5)"
_RE_LORA_AGE = re.compile(r"mesh_heard_age_s:\s*(\d+)", re.IGNORECASE)
_RE_LORA_HEARD = re.compile(r"heard\s*(\d+)\s*pkts", re.IGNORECASE)
_RE_LORA_CRC = re.compile(r"crc_err\s*(\d+)", re.IGNORECASE)
_RE_LORA_RUNTS = re.compile(r"runts\s*(\d+)", re.IGNORECASE)
_RE_LORA_FROM = re.compile(r"last from=(![0-9a-f]+)", re.IGNORECASE)
_RE_LORA_RSSI = re.compile(r"rssi=(-?\d+)", re.IGNORECASE)
_RE_LORA_SNR = re.compile(r"snr=(-?[\d.]+)", re.IGNORECASE)


def parse_lora_stats(result: Any) -> Optional[Dict[str, Any]]:
    """Parse a ``lora_stats`` result — the claw's OVER-THE-AIR witness.

    This is the only reading in the fleet that is independent of any box's own
    self-report: a separate radio, on separate silicon, saying what it actually
    heard on the channel. ``heard_age_s`` is the load-bearing field (how long
    since ANY packet), with the counters carried for RF-quality context.

    Returns ``None`` when ``mesh_heard_age_s`` is absent — an unparseable reply
    must not become "heard something just now". Absent counters stay ``None``
    (unknown), never 0: a fabricated 0 crc_err would read as a clean channel.
    """
    if not isinstance(result, str) or not result.strip():
        return None
    age = _int(_RE_LORA_AGE, result)
    if age is None:
        return None
    snr_m = _RE_LORA_SNR.search(result)
    return {
        "heard_age_s": age,
        "heard_pkts": _int(_RE_LORA_HEARD, result),
        "crc_err": _int(_RE_LORA_CRC, result),
        "runts": _int(_RE_LORA_RUNTS, result),
        "last_from": _str(_RE_LORA_FROM, result),
        "last_rssi_dbm": _int(_RE_LORA_RSSI, result),
        "last_snr": float(snr_m.group(1)) if snr_m else None,
    }


def parse_device_info(result: Any) -> Optional[Dict[str, Any]]:
    """Parse the ``device_info`` result string into structured fields.

    Returns ``None`` if the input is empty/non-string (nothing to read);
    otherwise a dict whose absent fields are ``None`` (unknown, never 0).
    """
    if not isinstance(result, str) or not result.strip():
        return None
    wifi_m = _RE_WIFI.search(result)
    wifi_connected: Optional[bool] = (
        wifi_m.group(1).lower() == "connected" if wifi_m else None
    )
    # rssi only carries a real value while connected; never invent one.
    rssi = _int(_RE_RSSI, result) if wifi_connected else None
    return {
        "heap_free_bytes": _int(_RE_HEAP_FREE, result),
        "heap_total_bytes": _int(_RE_HEAP_TOTAL, result),
        "uptime_s": _int(_RE_UPTIME, result),
        "wifi_connected": wifi_connected,
        "wifi_rssi_dbm": rssi,
        "chip": _str(_RE_CHIP, result),
        "ip": _str(_RE_IP, result),
    }


def parse_ble_stats(result: Any) -> Optional[Dict[str, Any]]:
    """Parse the ``ble_stats`` result string into structured fields.

    ``uniq``/``restarts``/``window`` stay strings ("32+", "0/0", "48/320ms")
    — coercing "32+" to an int would silently drop the ">=" the firmware means.
    """
    if not isinstance(result, str) or not result.strip():
        return None
    return {
        "adv_age_s": _int(_RE_BLE_AGE, result),
        "advs": _int(_RE_BLE_ADVS, result),
        "uniq": _str(_RE_BLE_UNIQ, result),
        "last_rssi_dbm": _int(_RE_BLE_LAST_RSSI, result),
        "restarts": _str(_RE_BLE_RESTARTS, result),
        "window": _str(_RE_BLE_WINDOW, result),
    }


def _extract(reply: Any, parser, err_key: str,
             errors: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Pull + parse one NATS reply, recording a witness on any failure.

    A swallowed failure that left no artifact would let a dead half read as
    "no data" instead of "we tried and couldn't" — so every miss writes an
    ``errors[err_key]`` entry (honest_failure_modes #9).
    """
    if reply is None:
        errors[err_key] = "no reply"
        return None
    if not isinstance(reply, dict):
        errors[err_key] = f"malformed reply: {type(reply).__name__}"
        return None
    if not reply.get("ok"):
        errors[err_key] = str(reply.get("error") or "reply not ok")[:160]
        return None
    parsed = parser(reply.get("result"))
    if parsed is None:
        errors[err_key] = "unparseable result"
        return None
    return parsed


#: Halves whose failure means THE DEVICE did not answer. ``device_info`` is the
#: identity/health call every claw firmware supports, so failing it is the
#: reachability fact. Everything else is an ACCESSORY: a claw with no BLE radio
#: and a claw with no battery gauge are correctly-built devices, not broken ones.
_REQUIRED_HALVES = ("device_info",)


def build_tick(now: float, host: str, device: str,
               device_info_reply: Any, ble_stats_reply: Any,
               battery_reply: Any = None,
               lora_reply: Any = None) -> Dict[str, Any]:
    """Assemble the ``claw_last_tick.json`` record from the NATS replies.

    ``reachable`` is the load-bearing fact: did the DEVICE answer — ANY half?
    Consumers that need "is this node alive" must read THAT, not ``ok``.

    It was derived from ``device_info`` alone until 2026-07-26, and that made a
    single timed-out request speak for the whole node: ``probe_claw_device_dark``
    paged *"the DEVICE is silent ... look at wifi, the USB feed, or the node
    itself"* about a claw with ~22 days of monotone uptime, from a tick that
    ALSO carried ble adv_age_s 0, battery 4.18 V, and lora reporting a packet
    heard 3 s earlier. A timed-out request is an observation-channel failure,
    not evidence about the device (honest_failure_modes #1/#2) — and the
    refuting evidence was already in the same record. ``answered`` names the
    halves that replied, so the proof travels with the claim.

    The residual is deliberately left visible rather than traded away: a
    chronically failing ``device_info`` still shows as ``ok: false`` + an
    ``errors`` witness here, and the ``claw_ble_soak`` cron still fails on it,
    so "the device answers but this half doesn't" keeps a watcher instead of
    becoming a blind spot.

    ``ok`` means "the capture reached the device and its REQUIRED half read
    cleanly". It is deliberately NOT an AND over all halves any more: the
    firmware answers in free text, so "no BLE scanner on this device"
    (permanent, correct) is indistinguishable from "BLE scanner wedged"
    (a real fault) at this layer — and the old ``ble is not None`` clause
    resolved that ambiguity the worst possible way, pinning BLE-less
    dudeclaw-02 at ``ok: false`` in every tick forever (observed 2026-07-19).
    A permanently-false flag is not a conservative default: it trains every
    reader, human and probe, to ignore it, so a REAL failure hides inside it.

    Accessory state is therefore REPORTED rather than folded in: consumers that
    care about BLE or the battery gauge read ``degraded_optional`` (and
    ``errors``), which name exactly what missed. Nothing is swallowed (#9) —
    the ambiguity is surfaced at the layer that can resolve it instead of being
    collapsed into a boolean here (honest_failure_modes #1/#3).

    Every miss still records an ``errors`` witness regardless of whether it
    affects ``ok`` — nothing is swallowed (#9); ``degraded_optional`` names the
    accessory halves that failed so a real BLE/battery regression stays visible
    instead of being averaged away.
    """
    errors: Dict[str, str] = {}
    device_info = _extract(device_info_reply, parse_device_info,
                           "device_info", errors)
    ble = _extract(ble_stats_reply, parse_ble_stats, "ble_stats", errors)
    battery = (_extract(battery_reply, parse_battery, "battery", errors)
               if battery_reply is not None else None)
    lora = (_extract(lora_reply, parse_lora_stats, "lora_stats", errors)
            if lora_reply is not None else None)
    required_errors = [k for k in errors if k in _REQUIRED_HALVES]
    degraded_optional = sorted(k for k in errors if k not in _REQUIRED_HALVES)
    # Which halves the DEVICE actually answered. ``_extract`` returns non-None
    # ONLY for a reply that came back with ok:true and parsed, so every entry
    # here is positive proof the node was alive this tick — evidence that was
    # already sitting in this record while ``reachable`` ignored it.
    answered = [name for name, val in (("device_info", device_info),
                                       ("ble", ble),
                                       ("battery", battery),
                                       ("lora", lora))
                if val is not None]
    reachable = bool(answered)
    return {
        "captured_at": now,
        "captured_iso": iso_or_none(now),
        "host": host,
        "device": device,
        "reachable": reachable,
        # NOT an alias of ``reachable`` any more (2026-07-26): they answer
        # different questions. ``reachable`` = did the DEVICE answer at all;
        # ``ok`` = did the REQUIRED half read cleanly. Aliasing them is what
        # let one timed-out request speak for the whole node.
        "ok": device_info is not None and not required_errors,
        "device_info": device_info,
        "ble": ble,
        "battery": battery,
        "lora": lora,
        "answered": answered,
        "errors": errors,
        "degraded_optional": degraded_optional,
    }


# The basename of the persisted last-tick capture. Owned HERE because this
# module owns the tick's shape (build_tick); consumers (the pusher's writer
# path, /api/status's reader in _map_status_endpoints, the rollup card,
# kilo's claw adapter) import it rather than re-hardcoding
# (honest_failure_modes #5). The full path formula lives with consumers
# that may resolve home differently — they test-pin each other.
CLAW_TICK_BASENAME = "claw_last_tick.json"

# Glob matching every SECONDARY tick (the secondary_tick_basename family)
# — the reader-side twin of that formula, owned next to it so a rename is
# one edit, not a hunt.
SECONDARY_TICK_GLOB = "claw_last_tick.*.json"


def _safe_instance_suffix(name: str) -> str:
    """Sanitize a device/instance name into a path-safe filename suffix.

    ONE formula, shared by every "additional claw on the same brain box"
    naming helper below (secondary_tick_basename AND instance_basename) so
    the tick writer, the mini-instance artifact writer, and the pusher's
    R-tier reader can never drift on how a device name becomes a filename
    (honest_failure_modes #5). Refuses an empty/whitespace name LOUD.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(name).strip())
    if not safe:
        raise ValueError("empty device/instance name")
    return safe


def secondary_tick_basename(device: str) -> str:
    """Basename for an ADDITIONAL claw's tick on the same brain box
    (multi-claw, W5.1: dudeclaw-02 enrollment).

    The PRIMARY claw keeps ``CLAW_TICK_BASENAME`` — the box-level display
    surfaces (/api/status.claw, the /fleet rollup card) read exactly that
    file and stay single-claw for now. Secondary ticks match kilo's glob
    (``claw_last_tick.*.json``). The device name in the filename is for
    the operator's eyes only — IDENTITY always comes from the tick's own
    ``device`` field, never the filename.
    """
    return f"claw_last_tick.{_safe_instance_suffix(device)}.json"


def instance_basename(basename: str, instance: str) -> str:
    """Insert a ``.<instance>`` suffix before the extension of a mini-claw
    ARTIFACT basename, so a SECOND standalone-preset instance (dudeclaw-02)
    gets its own state/rules/history/brief/annotation files and never
    collides with the primary claw's #80 single-instance flock.

        instance_basename("mini_dudeai_claw_state.json", "dudeclaw-02")
        -> "mini_dudeai_claw_state.dudeclaw-02.json"
        instance_basename("mini_dudeai_claw_history.jsonl", "dudeclaw-02")
        -> "mini_dudeai_claw_history.dudeclaw-02.jsonl"

    Two consumers of this ONE formula: ``presets.standalone.build_engine``
    (the writer) and ``claw_metrics_push._probe_tier`` (the R-tier reader) —
    they MUST agree or the glyph reads a state file the daemon never writes.
    An empty instance is refused loud (caller must decide primary-vs-secondary
    BEFORE calling; the primary keeps the un-suffixed basename).
    """
    safe = _safe_instance_suffix(instance)
    root, dot, ext = basename.rpartition(".")
    if not dot:  # extension-less basename: just append
        return f"{basename}.{safe}"
    return f"{root}.{safe}.{ext}"


# ─── brain-tier decision (display_tier glyph, firmware 0.4.0+dudeclaw.15) ────

# The cron-verdict name of the frontier Claude cadence run (the claw-brain's
# fleet manager box, wired via cron_verdict.sh) — consumer-of-record evidence
# that the frontier tier actually THOUGHT recently, not merely that an API
# endpoint answers pings.
CADENCE_VERDICT_NAME = "mini_cadence"

# The claw-mini daemon ticks every ~30 s and rewrites its state file; a
# state file older than this proves nothing (daemon dead or box wedged).
RULES_FRESH_S = 900.0


def compute_brain_tier(verdict_jobs: Any, ollama_ok: bool,
                       rules_age_s: Optional[float],
                       rules_fresh_s: float = RULES_FRESH_S,
                       ) -> "tuple[Optional[str], str]":
    """Highest PROVEN cognition tier -> ``('F'|'L'|'R', note)`` or
    ``(None, note)`` when nothing is provable.

    The ladder claims only what a live probe demonstrated this run:

    * ``'F'`` — the frontier cadence's cron verdict is ``OK`` and the SLO
      endpoint itself judges it not stale (staleness derives from the cron's
      own schedule — no second hardcoded window here, #80 rule 5). A missing
      or ambiguous ``stale`` field is NOT proven — unobservable freshness
      must never read as fresh.
    * ``'L'`` — frontier unproven, but the local Ollama answered.
    * ``'R'`` — no LLM tier proven, but the claw-mini rule daemon's state
      file is fresh: the deterministic engine is the brain of record.
    * ``None`` — nothing provable; the caller pushes NOTHING and the glass
      decays to SOLO, which honestly reads "no fresh brain claim".
    """
    for job in verdict_jobs or []:
        if isinstance(job, dict) and job.get("name") == CADENCE_VERDICT_NAME:
            if job.get("status") == "OK" and job.get("stale") is False:
                return "F", f"cadence verdict OK (age {job.get('age_s', '?')}s)"
            break
    if ollama_ok:
        return "L", "frontier unproven; ollama answered"
    if rules_age_s is not None and 0 <= rules_age_s <= rules_fresh_s:
        return "R", f"no LLM proven; claw-mini state fresh ({rules_age_s:.0f}s)"
    return None, "no tier provable (glass decays to SOLO)"

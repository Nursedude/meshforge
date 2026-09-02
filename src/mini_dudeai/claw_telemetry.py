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

from ._util import iso_or_none, log_warning

# device_info: "Version: 0.4.0+dudeclaw.21, MAC: AA:BB:CC:DD:EE:FF,
#   Free heap: 17764 bytes, Total heap: 210492 bytes,
#   Min free heap: 9012 bytes, Max alloc block: 6144 bytes,
#   Reset reason: PANIC, Uptime: 109368 seconds,
#   WiFi: connected (rssi -37 dBm), IP: <ip>,
#   Chip: ESP32-S3 rev 2, 2 cores, 240 MHz"
#
# NOTE: these are field-anchored on purpose. "Free heap:" is a SUBSTRING of
# "Min free heap:", and these patterns are IGNORECASE — an unanchored
# `Free heap:` would happily match inside the Min field the moment field
# order changed, silently reporting the low-water mark as the live value.
# Anchoring each to a field boundary (start-of-string or comma) makes the
# reader independent of field order instead of accidentally correct.
_FIELD = r"(?:^|,)\s*"
_RE_HEAP_FREE = re.compile(_FIELD + r"Free heap:\s*(\d+)", re.IGNORECASE)
_RE_HEAP_TOTAL = re.compile(_FIELD + r"Total heap:\s*(\d+)", re.IGNORECASE)
# Low-water mark since boot: distinguishes "comfortably at 25 kB" from
# "grazing zero between samples" — a poll can never catch the latter.
_RE_HEAP_MIN = re.compile(_FIELD + r"Min free heap:\s*(\d+)", re.IGNORECASE)
# Largest obtainable block: a healthy free total with a small max-alloc is
# fragmentation, which fails allocations that "should" fit.
_RE_HEAP_MAXALLOC = re.compile(_FIELD + r"Max alloc block:\s*(\d+)",
                               re.IGNORECASE)
# Why it last came back. Absent on firmware predating the field -> None,
# which must read as "unknown", never as "clean boot".
_RE_RESET_REASON = re.compile(_FIELD + r"Reset reason:\s*([^,]+)",
                              re.IGNORECASE)
_RE_UPTIME = re.compile(_FIELD + r"Uptime:\s*(\d+)", re.IGNORECASE)
_RE_WIFI = re.compile(r"WiFi:\s*(connected|disconnected)", re.IGNORECASE)
_RE_RSSI = re.compile(r"rssi\s*(-?\d+)\s*dBm", re.IGNORECASE)
_RE_IP = re.compile(r"IP:\s*([0-9.]+)")
_RE_CHIP = re.compile(r"Chip:\s*([^,]+)")
# Field-anchored like the rest: the firmware puts Version FIRST (so a truncated
# reply keeps the field an OTA push must read back), but this must not depend
# on that — anchor, don't assume position.
_RE_VERSION = re.compile(_FIELD + r"Version:\s*([^,]+)", re.IGNORECASE)
# Board identity, added firmware-side in +dudeclaw.21. Kept as the firmware
# emits it (uppercase, colon-separated) because the whole point is a BYTE-FOR
# -BYTE comparison against the host's `/dev/serial/by-id/..._<MAC>-if00` name:
# normalizing here would hide a real mismatch behind a helpful-looking fixup.
# Anchored and shape-checked — a MAC is exactly six hex pairs, and a partial
# match (a truncated reply clipping the tail) must read as absent, never as a
# short address that would compare unequal and look like the WRONG BOARD.
_RE_MAC = re.compile(
    _FIELD + r"MAC:\s*((?:[0-9A-F]{2}:){5}[0-9A-F]{2})(?![0-9A-F:])",
    re.IGNORECASE)

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


def _rssi_or_absent(token: str) -> "tuple[Optional[int], bool]":
    """``(rssi_dbm, rssi_absent)`` — 0 and -0 are sentinels, never measurements.

    dudeclaw ``.19`` emits ``@-0`` for an RSSI it does not have — observed live
    on dudeclaw-02 2026-09-01 (``rssi=-0``, and ``@-0`` on 4 of 5 watch/direct
    ids) while both ``.20`` claws on the same segment reported real values and
    ``snr`` was fine on all three. The firmware half is fixed in ``.20``; this
    is the reader half, and it matters because ``.19`` devices exist now and
    will again.

    The bug this closes is the SIGN being thrown away: ``int(float("-0"))`` is
    ``0``, a perfectly plausible integer, and it was stored with
    ``parse_error: False``. 0 dBm is 1 mW at the receiver — no LoRa link
    produces it — so the degraded reading landed at the STRONGEST end of the
    healthy domain, which is the honest_failure_modes #1 class in the one field
    a digipeater would be sited from. Refusing both 0 and -0 is safe in either
    direction: if some firmware ever means "0 dBm" literally, it is still not a
    measurement.

    ⚠️ **THE MECHANISM — it is not a random sentinel, it fires at the WEAK
    end.** Two claws on the same segment, same minute, same watched ids::

        .19 (dudeclaw-02)  !32962f10@-0    !851a9fe7@-0    !896b1917@-62
        .20 (dudeclaw-01)  !32962f10@-108  !851a9fe7@-108  !896b1917@-52

    Every id that reads ``-0`` on ``.19`` reads about **-108 dBm** on ``.20``;
    the ids ``.19`` reports correctly are the strong ones. So ``.19`` fails to
    capture RSSI precisely for MARGINAL packets — and pre-fix those became
    ``0``, the strongest value in the range. The reading did not merely go
    missing, it INVERTED: the weakest links presented as the best ones, in the
    field whose whole purpose is judging which links are good enough to site a
    digipeater on. That inversion is why this is worth a witness rather than a
    silent ``None``.

    Truncation is NOT the cause and was ruled out rather than assumed: both
    firmwares' replies run ~340 chars against the 1408 buffer, carry no ``cut=``
    marker, and end on a complete token. A clipped ``@-104`` becoming ``@-1``
    (see ``_stats_truncated``) is a real but DIFFERENT mechanism, and it is not
    what is happening here.

    OPEN, deliberately not coded for: one unreproduced ``rssi=-1`` on ``.19``'s
    header (2026-09-01 20:05), absent from 8+ consecutive raw samples taken
    minutes later and not explained by truncation. -1 dBm is as implausible as
    -0, but ONE sighting with no mechanism does not justify widening this
    refusal — inventing a "> -10 dBm is implausible" threshold would be a guess
    wearing a fix's clothes. The check if it recurs: capture the raw reply at
    the time and look for ``cut=``.

    An UNPARSEABLE token keeps the previous contract — ``(None, False)`` — so
    the caller's existing ``parse_error`` path is unchanged.

    Deliberately NOT applied to the BLE/WiFi RSSI fields: no ``-0`` has been
    observed there, they come from different firmware paths, and the WiFi one
    already refuses to invent a value when disconnected. Widening this without
    evidence would be a guess wearing a fix's clothes.
    """
    try:
        value = int(float(token))
    except (TypeError, ValueError):
        return None, False
    if value == 0:
        return None, True
    return value, False


def _rssi_field(rx: re.Pattern, s: str) -> "tuple[Optional[int], bool]":
    """``_rssi_or_absent`` for a scalar regex field. Absent field -> (None, False)."""
    m = rx.search(s)
    if not m:
        return None, False
    return _rssi_or_absent(m.group(1))


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
#   hop_start0 0, hop_malformed 12,
#   last from=!79be01d3 to=!ffffffff ch=0x08 rssi=-41 snr=6.5)"
_RE_LORA_AGE = re.compile(r"mesh_heard_age_s:\s*(\d+)", re.IGNORECASE)
_RE_LORA_HEARD = re.compile(r"heard\s*(\d+)\s*pkts", re.IGNORECASE)
_RE_LORA_CRC = re.compile(r"crc_err\s*(\d+)", re.IGNORECASE)
_RE_LORA_RUNTS = re.compile(r"runts\s*(\d+)", re.IGNORECASE)
# F2 rejection counters, added firmware-side in +dudeclaw.21.
#
# `hop_start0` counts headers the OLD (pre-F2) arithmetic would have scored as
# a DIRECT link and F2 now scores UNKNOWN — so it is the only thing that can
# tell "F2 protected nothing here" apart from "F2 never fired here". The
# 2026-09-01 before/after measured F2-LOST 0 on every claw and could not
# distinguish those two; a correct fix and a wholly inert one read identically.
#
# `hop_malformed` is a DIFFERENT rejection — start < limit, a foreign or
# corrupt stack — and is deliberately NOT summed with the first. The old
# arithmetic rejected these too, so counting them as F2 catches would overstate
# what F2 does, which is the same collapse-two-degraded-states-into-one defect
# F2 itself exists to remove.
#
# None on pre-.21 firmware: UNKNOWN, never 0. Zero is a real and meaningful
# reading here ("the encoding does not occur on this segment") and must not be
# forged by a claw that simply cannot report it (honest_failure_modes #2).
_RE_LORA_HOP_START0 = re.compile(r"\bhop_start0\s*(\d+)", re.IGNORECASE)
_RE_LORA_HOP_MALFORMED = re.compile(r"\bhop_malformed\s*(\d+)", re.IGNORECASE)
_RE_LORA_FROM = re.compile(r"last from=(![0-9a-f]+)", re.IGNORECASE)
_RE_LORA_RSSI = re.compile(r"rssi=(-?\d+)", re.IGNORECASE)
_RE_LORA_SNR = re.compile(r"snr=(-?[\d.]+)", re.IGNORECASE)
# watch=!32962f10:12/45@-104,!ddfb8065:never  (firmware >= the 2026-07-29 build)
_RE_LORA_WATCH = re.compile(r"watch=([^\s]+)", re.IGNORECASE)
_RE_LORA_WATCH_DROPPED = re.compile(r"watch_dropped=(\d+)", re.IGNORECASE)
# direct=!32962f10:12@-104,!ddfb8065:never   (firmware >= the 2026-08-30 build)
_RE_LORA_DIRECT = re.compile(r"direct=([^\s]+)", re.IGNORECASE)
# hops=N on the last-packet line; -1 means the header was malformed.
_RE_LORA_HOPS = re.compile(r"\bhops=(-?\d+)", re.IGNORECASE)
# ` cut=1` — the firmware's POSITIVE truncation witness (F1, +dudeclaw.20 and
# later). Its PRESENCE proves the stats tail was clipped; its ABSENCE is
# ambiguous, because every earlier build stays silent when it truncates. So
# this maps to True/None, never True/False: "no marker" is not "complete"
# (honest_failure_modes #2 — unobservable is never healthy).
_RE_LORA_CUT = re.compile(r"\bcut=1\b", re.IGNORECASE)


def _stats_truncated(result: str) -> Optional[bool]:
    """True when the reply carries the truncation witness, else None (unknown)."""
    return True if _RE_LORA_CUT.search(result) else None


def _parse_direct(result: str) -> Optional[Dict[str, Any]]:
    """Per-id DIRECT reception (hops == 0), or None when the field is absent.

    The same three-state discipline as ``_parse_watch``, for the same reason:

      * field ABSENT      -> None. Firmware predates it. We do not know whether
                             a direct link exists — which is NOT the same as
                             knowing there isn't one.
      * id present, never -> ``direct: False`` with age None. The id IS tracked
                             and has never been heard at hops == 0. If it is
                             simultaneously watch-heard, something else is
                             REPEATING it, and that is the finding.
      * id present, aged  -> age + the RSSI of that direct packet, which is the
                             only RSSI in this module that describes a LINK to
                             that node rather than to whoever last repeated it.
    """
    m = _RE_LORA_DIRECT.search(result)
    if not m:
        return None
    out: Dict[str, Any] = {}
    for tok in m.group(1).split(","):
        tok = tok.strip()
        if not tok or ":" not in tok:
            continue
        node, _, rest = tok.partition(":")
        node = node.strip()
        if not node:
            continue
        rest = rest.strip()
        if rest.lower().startswith("never"):
            out[node] = {"age_s": None, "rssi_dbm": None, "direct": False,
                         "rssi_absent": False, "parse_error": False}
            continue
        age_part, _, rssi_part = rest.partition("@")
        try:
            age = int(age_part)
        except ValueError:
            # Garbled is kept with parse_error, never dropped: an absent key
            # is indistinguishable from "not tracked" to a consumer doing
            # .get(id) (honest_failure_modes #9).
            out[node] = {"age_s": None, "rssi_dbm": None, "direct": None,
                         "rssi_absent": False, "parse_error": True}
            continue
        rssi, rssi_absent = _rssi_or_absent(rssi_part)
        out[node] = {"age_s": age, "rssi_dbm": rssi, "direct": True,
                     "rssi_absent": rssi_absent, "parse_error": False}
    if out and _stats_truncated(result):
        # F1 reader half — see the note in _parse_watch. `direct` is the field
        # the truncation actually forged (it is emitted LAST, so it is the one
        # that loses bytes), and its RSSI is the number a digipeater gets sited
        # from. `direct` becomes None, not False: we do not know.
        for node in out:
            out[node] = {"age_s": None, "rssi_dbm": None, "direct": None,
                         "rssi_absent": False, "parse_error": True}
    return out or None


def _parse_watch(result: str) -> Optional[Dict[str, Any]]:
    """Per-id last-heard from the WATCH LIST, or None when absent.

    THE DISTINCTION, and the reason this is not a plain dict of ints: there are
    THREE states, and merging any two of them recreates the blind spot this
    field exists to close.

      * field ABSENT      -> None. Old firmware, or no ids configured. We do not
                             know anything about our own transmitter.
      * id present, never -> age_s None + ``never: True``. The id IS watched and
                             has NOT been heard since radio start. This is the
                             signal (a dead PA / mute gateway), and it must never
                             render as 0, which reads as "heard just now".
      * id present, aged  -> age_s int. Heard that many seconds ago.

    mesh_heard_age_s cannot make this distinction: with neighbours chattering at
    6-8 pkt/min it sits at ~5 s while our own radio is silent, so every
    silence check reads clean through a total TX failure (2026-07-29 finding).
    """
    m = _RE_LORA_WATCH.search(result)
    if not m:
        return None
    out: Dict[str, Any] = {}
    for tok in m.group(1).split(","):
        tok = tok.strip()
        if not tok or ":" not in tok:
            continue
        node, _, rest = tok.partition(":")
        node = node.strip()
        if not node:
            continue
        if rest.strip().lower().startswith("never"):
            out[node] = {"age_s": None, "pkts": 0, "rssi_dbm": None,
                         "never": True, "rssi_absent": False,
                         "parse_error": False}
            continue
        age_part, _, tail = rest.partition("/")
        pkts_part, _, rssi_part = tail.partition("@")
        try:
            age = int(age_part)
        except ValueError:
            # A GARBLED reading is not an ABSENT id. Omitting it would drop the
            # key, and a consumer doing watched.get(id) would then see None —
            # indistinguishable from "this id is not watched at all". So it is
            # kept with parse_error set: unknown is stated, not implied by
            # absence (honest_failure_modes #9 — every swallow leaves a witness).
            out[node] = {"age_s": None, "pkts": None, "rssi_dbm": None,
                         "never": False, "rssi_absent": False,
                         "parse_error": True}
            continue
        try:
            pkts = int(pkts_part)
        except ValueError:
            pkts = None
        rssi, rssi_absent = _rssi_or_absent(rssi_part)
        out[node] = {"age_s": age, "pkts": pkts, "rssi_dbm": rssi,
                     "never": False, "rssi_absent": rssi_absent,
                     "parse_error": False}
    if out and _stats_truncated(result):
        # F1 reader half. The reply was clipped and nothing tells us WHICH
        # token lost bytes — a clipped `@-104` becomes `@-1`, a perfectly
        # valid -1 dBm that no parser can distinguish from a real reading. So
        # NO entry in a truncated reply may present as clean. Values are
        # dropped rather than kept beside the flag, matching the garbled-token
        # branch above: pessimistic when blind. With the reply buffer grown to
        # 1408 this should not fire at all, which is what makes the trade cheap
        # — losing 11 good readings to refuse 1 forged one.
        for node in out:
            out[node] = {"age_s": None, "pkts": None, "rssi_dbm": None,
                         "never": False, "rssi_absent": False,
                         "parse_error": True}
    return out or None


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
    last_rssi, last_rssi_absent = _rssi_field(_RE_LORA_RSSI, result)
    return {
        "heard_age_s": age,
        "heard_pkts": _int(_RE_LORA_HEARD, result),
        "crc_err": _int(_RE_LORA_CRC, result),
        "runts": _int(_RE_LORA_RUNTS, result),
        "hop_start0": _int(_RE_LORA_HOP_START0, result),
        "hop_malformed": _int(_RE_LORA_HOP_MALFORMED, result),
        "last_from": _str(_RE_LORA_FROM, result),
        "last_rssi_dbm": last_rssi,
        # True when the firmware reported the -0 sentinel rather than a
        # reading, so "no RSSI" stays distinguishable from a real one
        # downstream (honest_failure_modes #9 — the swallow leaves a witness).
        "last_rssi_absent": last_rssi_absent,
        "last_snr": float(snr_m.group(1)) if snr_m else None,
        # None (not {}) when the firmware predates the watch list or no ids are
        # configured — "we cannot see our own transmitter" must not read as
        # "our transmitter is fine".
        "watched": _parse_watch(result),
        "watch_dropped": _int(_RE_LORA_WATCH_DROPPED, result),
        # DIRECT-only reception (firmware 2026-08-30). `watched` says traffic
        # bearing an id reached this claw by ANY path — in a flood mesh that
        # may be a nearer node rebroadcasting, so its RSSI is the RELAY's
        # signal. `direct` is the originator's own transmission (hops == 0),
        # which is the only reading that characterises a LINK. A node that is
        # watch-heard strong and direct-never is being repeated, and siting a
        # digipeater off the first number puts it in the wrong place.
        # None on firmware predating the field — unknown, never "no direct link".
        "direct": _parse_direct(result),
        # Hop distance of the LAST packet: 0 = direct, -1 = malformed header
        # (never treated as direct), None = firmware predates the field.
        "last_hops": _int(_RE_LORA_HOPS, result),
        # True when the firmware witnessed its own truncation (F1,
        # +dudeclaw.20+); None on every earlier build, which truncates
        # silently. Never False — absence of the marker is not proof of a
        # complete reply, and a consumer that treats it as one recreates
        # exactly the confident-wrong-dB defect F1 was raised against.
        "stats_truncated": _stats_truncated(result),
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
        # None on firmware that predates these fields — an unknown low-water
        # mark or reset reason is NOT a healthy one (honest_failure_modes #2).
        "heap_min_free_bytes": _int(_RE_HEAP_MIN, result),
        "heap_max_alloc_bytes": _int(_RE_HEAP_MAXALLOC, result),
        "reset_reason": _str(_RE_RESET_REASON, result),
        "uptime_s": _int(_RE_UPTIME, result),
        "wifi_connected": wifi_connected,
        "wifi_rssi_dbm": rssi,
        "chip": _str(_RE_CHIP, result),
        "ip": _str(_RE_IP, result),
        # None on firmware predating the field (added 2026-08-30) — which is
        # itself the finding, not a blank: a claw whose running image cannot be
        # named is a claw an OTA push cannot verify, and firmware drift across
        # the claws has been unobservable until now. Unknown, never assumed
        # current (honest_failure_modes #2).
        "version": _str(_RE_VERSION, result),
        # None on firmware predating +dudeclaw.21. That is UNKNOWN identity,
        # never "matches" — a claw whose MAC cannot be read is a claw whose
        # port assignment is still one-sided, which is the whole reason the
        # field exists (honest_failure_modes #2).
        "mac": _str(_RE_MAC, result),
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


def _watch_verdicts(lora: Any, device_info: Any,
                    segments: Any = None, claw_segment: Any = None):
    """Apply the listening-window gate to the watch list, if both halves exist.

    Kept deliberately dumb: no config lookup here. The conservative default
    window is the right behaviour for a capture that must never page on a
    reboot, and a caller with per-node intervals can re-run classify_watch with
    them. Never raises into the tick — a gate that crashes the capture would
    cost more than the verdict is worth.

    ``segments``/``claw_segment`` are threaded through from the caller (which
    owns config reading) rather than looked up here, keeping that contract. They
    are PASSED rather than defaulted because a gate that ships unwired is a
    reader with no writer (honest_failure_modes #4) — the 2026-07-30 finding it
    exists to prevent would have stayed live with the code merged.
    """
    try:
        from mini_dudeai.claw_rf_watch import classify_watch
        watched = (lora or {}).get("watched") if isinstance(lora, dict) else None
        if not watched:
            return None
        uptime = None
        if isinstance(device_info, dict):
            uptime = device_info.get("uptime_s")
        return classify_watch(
            watched, uptime,
            segments=segments if isinstance(segments, dict) else None,
            claw_segment=claw_segment if isinstance(claw_segment, str) else None)
    except Exception as e:
        # A swallow with no witness is how a gate silently stops gating. The tick
        # must not die for a verdict, but the failure has to be findable.
        #
        # ⚠️ This line called `logger.warning(...)` until 2026-07-29, and `logger`
        # was never defined in this module — so the WITNESS raised NameError from
        # inside the except clause, which nothing catches. It escaped
        # _watch_verdicts, killed build_tick's dict assembly, and (since
        # claw_metrics_push only guards NatsError) took the whole capture cron
        # down. The handler written to keep the tick alive was the one thing that
        # could kill it. Found by the 2026-07-29 review pass.
        #
        # log_warning is this package's own journald-prefixed helper — the
        # pattern the rest of mini_dudeai already uses — so the witness cannot
        # depend on a binding this module does not have.
        log_warning("watch verdict gate failed (%s: %s) — tick carries no "
                    "verdicts this cycle" % (e.__class__.__name__, e))
        return None


def build_tick(now: float, host: str, device: str,
               device_info_reply: Any, ble_stats_reply: Any,
               battery_reply: Any = None,
               lora_reply: Any = None,
               segments: Any = None, claw_segment: Any = None) -> Dict[str, Any]:
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
        # The uptime GATE, applied here so the tick carries a JUDGED verdict and
        # no consumer has to re-derive it (and get the gate wrong). `never` alone
        # is not a finding: seconds after this field first shipped, 3 of 4 watched
        # fleet radios read `never` purely because the claw had been up 10 s.
        # None when there is no watch list or no uptime to size the window with.
        "watch_verdicts": _watch_verdicts(lora, device_info,
                                          segments, claw_segment),
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

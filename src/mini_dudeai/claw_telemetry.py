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
    parse) yields ``None`` for that half plus an explicit ``errors`` entry,
    and forces the top-level ``ok`` False — a degraded capture must not be
    mistakable for healthy telemetry.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, Optional

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


def build_tick(now: float, host: str, device: str,
               device_info_reply: Any, ble_stats_reply: Any) -> Dict[str, Any]:
    """Assemble the ``claw_last_tick.json`` record from the two NATS replies.

    ``ok`` is True only when BOTH halves were read cleanly; any failure leaves
    that half ``None`` + an ``errors`` entry and forces ``ok`` False.
    """
    errors: Dict[str, str] = {}
    device_info = _extract(device_info_reply, parse_device_info,
                           "device_info", errors)
    ble = _extract(ble_stats_reply, parse_ble_stats, "ble_stats", errors)
    try:
        captured_iso = datetime.datetime.fromtimestamp(now).isoformat(
            timespec="seconds")
    except (OverflowError, OSError, ValueError):
        captured_iso = None
    return {
        "captured_at": now,
        "captured_iso": captured_iso,
        "host": host,
        "device": device,
        "ok": not errors and device_info is not None and ble is not None,
        "device_info": device_info,
        "ble": ble,
        "errors": errors,
    }

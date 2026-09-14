"""
Starlink dish telemetry — read-only, zero new dependencies.

WHY THIS EXISTS
---------------
``wan_path_degraded`` can say "loss beyond the ISP" but never *why*. On a
fibre uplink that distinction rarely matters; on a satellite one it is the
whole story — a dish drops out briefly and often (obstruction, satellite
handover) where fibre fails rarely and long. This module reads the dish's own
telemetry so an uplink complaint can name its cause.

HOW IT TALKS TO THE DISH (and why it looks like this)
-----------------------------------------------------
The dish serves gRPC on ``192.168.100.1:9200``. We deliberately do NOT add
``grpcio`` (a heavy wheel on a nine-Pi fleet) and we do NOT vendor a
third-party ``.proto``:

* **Transport** is ``curl --http2-prior-knowledge``. gRPC over HTTP/2 is a
  POST of ``application/grpc+proto`` whose body is a 5-byte prefix
  (1 compression flag + 4-byte big-endian length) followed by the protobuf
  message. curl is already present fleet-wide and speaks HTTP/2.
* **Schema** came from the DISH ITSELF, not from memory or a third party.
  Its diagnostics web app at ``http://192.168.100.1/`` bundles the generated
  protobuf client; the field numbers below were read out of that bundle
  (verified 2026-09-13 against firmware ``2026.08.31.mr85832``).
  ⚠️ gRPC *reflection* is implemented on the dish but answers
  ``NOT_FOUND: "proto: not found"`` — it cannot be used to discover these.
* **Decoding** is a minimal wire-format walk (below). protobuf's wire format
  carries field number + wire type on every field, so a response can be
  walked WITHOUT a schema; the field numbers are only needed to give the
  values names.

⚠️ **Field numbers are firmware-coupled.** They are stable in practice but
they are not a contract. Never widen a number's meaning on a hunch — re-read
the bundle (``/static/js/script.js.gz``). A first pass at this file took the
numbers from a global grep and got ``pop_ping_latency_ms`` and
``pop_ping_drop_rate`` WRONG, because those names also occur in other
messages; they must be read from the ``DishGetStatusResponse`` block itself.

HONEST FAILURE MODES
--------------------
Every optional reading is ``None`` when unknown, never ``0.0``. That is not
style: ``fraction_obstructed=0.0`` means *a perfectly clear sky*, so a parse
failure that defaulted to zero would report the healthiest possible value for
a dish we could not reach (honest_failure_modes #1 — a degraded value must
not overlap the healthy domain). ``DishStatus.state`` is tri-state and the
caller is expected to branch on it.

Usage:
    from utils.starlink_dish import get_dish_status

    st = get_dish_status()
    if st.state == "ok":
        print(st.pop_ping_latency_ms, st.fraction_obstructed)
    else:
        print(f"dish telemetry unavailable: {st.detail}")
"""

from __future__ import annotations

import logging
import math
import shutil
import socket
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DISH_HOST = "192.168.100.1"
DISH_PORT = 9200
GRPC_METHOD = "/SpaceX.API.Device.Device/Handle"
DEFAULT_TIMEOUT = 8
#: How long to spend deciding the dish is simply not here. The dish sits one
#: hop away on the LAN, so a reachable one accepts in single-digit
#: milliseconds; anything slower is absence, not slowness.
REACHABILITY_TIMEOUT = 1.0

# ── field numbers, read from the dish's own JS bundle (see module docstring) ──
_REQ_GET_STATUS = 1004          # Request.get_status
_RESP_DISH_GET_STATUS = 2004    # Response.dish_get_status

# DishGetStatusResponse
_S_DEVICE_INFO = 1
_S_DEVICE_STATE = 2
_S_POP_PING_DROP_RATE = 1003
_S_OBSTRUCTION_STATS = 1004
_S_ALERTS = 1005
_S_DOWNLINK_BPS = 1007
_S_UPLINK_BPS = 1008
_S_POP_PING_LATENCY_MS = 1009
_S_BORESIGHT_AZ_DEG = 1011      # scalar float  — phased-array pointing
_S_BORESIGHT_EL_DEG = 1012      # scalar float
_S_ETH_SPEED_MBPS = 1016
_S_SNR_ABOVE_NOISE = 1018       # scalar bool

_DEVICE_INFO_ID = 1
_DEVICE_INFO_HW = 2
_DEVICE_INFO_SW = 3
_DEVICE_STATE_UPTIME_S = 1

_OBSTR_FRACTION = 1
_OBSTR_VALID_S = 4
_OBSTR_CURRENTLY = 5
_OBSTR_TIME_OBSTRUCTED = 9

# DishAlerts — bools; only the ones an operator would act on.
_ALERT_NAMES = {
    1: "motors_stuck",
    2: "thermal_shutdown",
    3: "thermal_throttle",
    4: "unexpected_location",
    5: "mast_not_near_vertical",
    6: "slow_ethernet_speeds",
    7: "roaming",
    9: "is_heating",
    10: "power_supply_thermal_throttle",
    11: "is_power_save_idle",
}


@dataclass
class DishStatus:
    """A dish reading. ``state`` is the only field always meaningful.

    state:
        "ok"          — we spoke to the dish and parsed a status reply
        "unreachable" — no answer (curl failed / timed out / gRPC error)
        "malformed"   — answered, but the reply was not a status we can read
        "unsupported" — curl is missing, so this box cannot ask at all
    """

    state: str
    detail: str = ""
    # Every reading below is None when unknown — never a healthy-looking zero.
    dish_id: Optional[str] = None
    hardware_version: Optional[str] = None
    software_version: Optional[str] = None
    uptime_s: Optional[int] = None
    pop_ping_latency_ms: Optional[float] = None
    pop_ping_drop_rate: Optional[float] = None
    downlink_throughput_bps: Optional[float] = None
    uplink_throughput_bps: Optional[float] = None
    eth_speed_mbps: Optional[int] = None
    # Alignment / link physics. The dish is a phased array: it steers
    # electronically, so boresight is where the BEAM is pointed, not where the
    # housing faces. Together with the obstruction window these are what make
    # a satellite uplink fail differently from a wire — a beam that must track
    # across the sky can be clipped by geometry that is invisible on the ground.
    boresight_azimuth_deg: Optional[float] = None
    boresight_elevation_deg: Optional[float] = None
    snr_above_noise_floor: Optional[bool] = None
    fraction_obstructed: Optional[float] = None
    currently_obstructed: Optional[bool] = None
    obstruction_valid_s: Optional[float] = None
    time_obstructed_s: Optional[float] = None
    alerts: Dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def as_dict(self) -> Dict[str, Any]:
        """Serialise for a truth surface. ``state`` always present; unknown
        readings stay JSON ``null`` rather than becoming 0 — a consumer must
        be able to tell "clear sky" from "we could not ask"."""
        return {
            "state": self.state,
            "detail": self.detail,
            "dish_id": self.dish_id,
            "hardware_version": self.hardware_version,
            "software_version": self.software_version,
            "uptime_s": self.uptime_s,
            "pop_ping_latency_ms": self.pop_ping_latency_ms,
            "pop_ping_drop_rate": self.pop_ping_drop_rate,
            "downlink_throughput_bps": self.downlink_throughput_bps,
            "uplink_throughput_bps": self.uplink_throughput_bps,
            "eth_speed_mbps": self.eth_speed_mbps,
            "boresight_azimuth_deg": self.boresight_azimuth_deg,
            "boresight_elevation_deg": self.boresight_elevation_deg,
            "snr_above_noise_floor": self.snr_above_noise_floor,
            "fraction_obstructed": self.fraction_obstructed,
            "currently_obstructed": self.currently_obstructed,
            "obstruction_valid_s": self.obstruction_valid_s,
            "active_alerts": self.active_alerts,
        }

    @property
    def active_alerts(self) -> list:
        """Alert names currently true. Empty list when state != 'ok' is NOT a
        claim of health — check ``state`` first."""
        return sorted(k for k, v in self.alerts.items() if v)


# ── minimal protobuf wire-format reader (no schema, no dependency) ───────────

def _read_varint(buf: bytes, pos: int):
    """Return (value, new_pos). Raises ValueError on a truncated varint."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        if shift > 63:
            raise ValueError("varint too long")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def decode_fields(buf: bytes) -> Dict[int, list]:
    """Walk a protobuf message into {field_number: [raw values]}.

    Values are ``bytes`` for wire type 2, ``int`` for varints, and the raw
    4/8-byte strings for fixed32/fixed64 (callers unpack what they expect).
    Repeated fields keep every occurrence — a caller that wants "the last one"
    must say so, rather than silently getting whichever we happened to keep.
    """
    out: Dict[int, list] = {}
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field_no = key >> 3
        wire = key & 0x07
        if wire == 0:
            val, pos = _read_varint(buf, pos)
        elif wire == 1:
            if pos + 8 > len(buf):
                raise ValueError("truncated fixed64")
            val = buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            if pos + length > len(buf):
                raise ValueError("truncated length-delimited field")
            val = buf[pos:pos + length]
            pos += length
        elif wire == 5:
            if pos + 4 > len(buf):
                raise ValueError("truncated fixed32")
            val = buf[pos:pos + 4]
            pos += 4
        else:
            # Wire types 3/4 are deprecated groups; 6/7 do not exist. Refuse
            # rather than guess a length — a wrong guess silently shifts every
            # field after it and yields plausible nonsense.
            raise ValueError(f"unsupported wire type {wire} for field {field_no}")
        out.setdefault(field_no, []).append(val)
    return out


def _one(fields: Dict[int, list], no: int):
    vals = fields.get(no)
    return vals[-1] if vals else None


def _as_float(raw) -> Optional[float]:
    """fixed32 -> float. Returns None rather than 0.0 when absent/wrong size."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 4:
        return None
    return struct.unpack("<f", bytes(raw))[0]


def _as_double(raw) -> Optional[float]:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 8:
        return None
    return struct.unpack("<d", bytes(raw))[0]


def _as_text(raw) -> Optional[str]:
    if not isinstance(raw, (bytes, bytearray)):
        return None
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scalar_num(fields: Dict[int, list], no: int) -> Optional[float]:
    """A numeric scalar, applying proto3 default semantics.

    ⚠️ Only valid when the CONTAINING message decoded. proto3 does not
    serialise a scalar equal to its default, so an absent field inside a
    message we successfully read means 0.0 — not "unknown". Reporting it as
    unknown is its own small lie (found live 2026-09-13: a genuinely zero
    ``pop_ping_drop_rate`` rendered as "unknown"). When the container itself
    is missing, the caller leaves the field None instead of calling this.
    """
    raw = _one(fields, no)
    if raw is None:
        return 0.0
    return _as_number(raw)


def _scalar_bool(fields: Dict[int, list], no: int) -> bool:
    """A bool scalar with proto3 default semantics. Same caveat as above."""
    raw = _one(fields, no)
    return bool(raw) if isinstance(raw, int) else False


def _as_number(raw) -> Optional[float]:
    """A numeric field may arrive as fixed32 (float), fixed64 (double) or a
    varint, depending on the declared type. Try the shape we were given."""
    if isinstance(raw, int):
        return float(raw)
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) == 4:
            return _as_float(raw)
        if len(raw) == 8:
            return _as_double(raw)
    return None


# ── transport ────────────────────────────────────────────────────────────────

def _frame(message: bytes) -> bytes:
    """Wrap a protobuf message in a gRPC length-prefixed frame."""
    return b"\x00" + struct.pack(">I", len(message)) + message


def _encode_key(field_no: int, wire: int) -> bytes:
    key = (field_no << 3) | wire
    out = bytearray()
    while True:
        byte = key & 0x7F
        key >>= 7
        if key:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _unframe(buf: bytes) -> Optional[bytes]:
    """Strip the 5-byte gRPC prefix. None if the frame is absent/short."""
    if len(buf) < 5:
        return None
    length = struct.unpack(">I", buf[1:5])[0]
    body = buf[5:5 + length]
    if len(body) != length:
        return None
    return body


def _dish_reachable(host: str, port: int,
                    timeout: float = REACHABILITY_TIMEOUT) -> bool:
    """Cheap TCP probe: is anything listening at all?

    WHY THIS EXISTS (CI caught it, 2026-09-13): most boxes have no dish, and
    without this the full request timeout is spent discovering that. The TUI's
    map view budgets 20s, and the handler-dispatch contract test exercises
    every menu action — so on a machine with no dish that single pane burned
    the whole budget and took the suite's timeout with it. My local run passed
    only because a dish happens to sit on this LAN: a verdict that depended on
    un-pinned machine state, which is no verdict at all.

    Uses connect_ex so an absent host is a return value, not an exception.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _grpc_call(payload: bytes, host: str, port: int, timeout: int):
    """POST one gRPC message, return (body, error). Exactly one is None.

    Uses curl because it is already fleet-wide and speaks HTTP/2; adding
    grpcio for two calls would put a heavy wheel on every Pi.
    """
    curl = shutil.which("curl")
    if not curl:
        return None, "curl not found — this box cannot query the dish"
    if not _dish_reachable(host, port):
        return None, f"nothing listening on {host}:{port}"

    url = f"http://{host}:{port}{GRPC_METHOD}"
    with tempfile.TemporaryDirectory(prefix="mf-dish-") as tmp:
        req = Path(tmp) / "req.grpc"
        resp = Path(tmp) / "resp.bin"
        hdr = Path(tmp) / "hdr.txt"
        req.write_bytes(_frame(payload))
        cmd = [
            curl, "-s", "--http2-prior-knowledge",
            "-H", "content-type: application/grpc+proto",
            "-H", "te: trailers",
            "--data-binary", f"@{req}",
            "-D", str(hdr), "-o", str(resp),
            # --max-time bounds the WHOLE transfer; without a connect
            # timeout an unroutable address spends all of it in SYN retries.
            "--connect-timeout", str(int(REACHABILITY_TIMEOUT) or 1),
            "--max-time", str(timeout),
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            return None, f"timed out after {timeout}s"
        except OSError as exc:
            return None, f"curl failed to start: {exc}"

        if proc.returncode != 0:
            return None, f"curl exit {proc.returncode} (dish unreachable?)"

        headers = hdr.read_text(errors="replace") if hdr.exists() else ""
        status = _grpc_status(headers)
        # grpc-status is the authority, not the HTTP code: gRPC reports
        # application errors as HTTP 200 with a non-zero grpc-status.
        if status is None:
            return None, "no grpc-status in reply (not a gRPC endpoint?)"
        if status != 0:
            return None, f"grpc-status {status}: {_grpc_message(headers)}"
        return (resp.read_bytes() if resp.exists() else b""), None


def _header_value(headers: str, name: str) -> Optional[str]:
    prefix = name.lower() + ":"
    for line in headers.splitlines():
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return None


def _grpc_status(headers: str) -> Optional[int]:
    raw = _header_value(headers, "grpc-status")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _grpc_message(headers: str) -> str:
    return _header_value(headers, "grpc-message") or ""


# ── public API ───────────────────────────────────────────────────────────────

def parse_status_response(body: bytes) -> DishStatus:
    """Decode a Response frame into a DishStatus. Pure — unit-testable."""
    try:
        message = _unframe(body)
        if message is None:
            return DishStatus("malformed", "reply was not a complete gRPC frame")
        top = decode_fields(message)
        raw_status = _one(top, _RESP_DISH_GET_STATUS)
        if raw_status is None:
            seen = ",".join(str(k) for k in sorted(top)) or "none"
            return DishStatus(
                "malformed",
                f"no dish_get_status ({_RESP_DISH_GET_STATUS}) in reply; fields: {seen}",
            )
        s = decode_fields(raw_status)
    except (ValueError, struct.error) as exc:
        return DishStatus("malformed", f"protobuf decode failed: {exc}")

    st = DishStatus("ok")
    # The status message decoded, so proto3 defaults apply to its scalars.
    st.pop_ping_drop_rate = _scalar_num(s, _S_POP_PING_DROP_RATE)
    st.pop_ping_latency_ms = _scalar_num(s, _S_POP_PING_LATENCY_MS)
    st.downlink_throughput_bps = _scalar_num(s, _S_DOWNLINK_BPS)
    st.uplink_throughput_bps = _scalar_num(s, _S_UPLINK_BPS)
    eth = _one(s, _S_ETH_SPEED_MBPS)
    st.eth_speed_mbps = int(eth) if isinstance(eth, int) else 0
    st.boresight_azimuth_deg = _scalar_num(s, _S_BORESIGHT_AZ_DEG)
    st.boresight_elevation_deg = _scalar_num(s, _S_BORESIGHT_EL_DEG)
    st.snr_above_noise_floor = _scalar_bool(s, _S_SNR_ABOVE_NOISE)

    info_raw = _one(s, _S_DEVICE_INFO)
    if isinstance(info_raw, (bytes, bytearray)):
        try:
            info = decode_fields(info_raw)
            st.dish_id = _as_text(_one(info, _DEVICE_INFO_ID))
            st.hardware_version = _as_text(_one(info, _DEVICE_INFO_HW))
            st.software_version = _as_text(_one(info, _DEVICE_INFO_SW))
        except ValueError as exc:
            logger.debug("device_info undecodable: %s", exc)

    state_raw = _one(s, _S_DEVICE_STATE)
    if isinstance(state_raw, (bytes, bytearray)):
        try:
            dstate = decode_fields(state_raw)
            up = _one(dstate, _DEVICE_STATE_UPTIME_S)
            st.uptime_s = int(up) if isinstance(up, int) else None
        except ValueError as exc:
            logger.debug("device_state undecodable: %s", exc)

    obs_raw = _one(s, _S_OBSTRUCTION_STATS)
    if isinstance(obs_raw, (bytes, bytearray)):
        try:
            obs = decode_fields(obs_raw)
            # obstruction_stats was present, so its absent scalars are
            # proto3 defaults, not unknowns.
            st.fraction_obstructed = _scalar_num(obs, _OBSTR_FRACTION)
            st.obstruction_valid_s = _scalar_num(obs, _OBSTR_VALID_S)
            st.time_obstructed_s = _scalar_num(obs, _OBSTR_TIME_OBSTRUCTED)
            st.currently_obstructed = _scalar_bool(obs, _OBSTR_CURRENTLY)
        except ValueError as exc:
            logger.debug("obstruction_stats undecodable: %s", exc)

    alerts_raw = _one(s, _S_ALERTS)
    if isinstance(alerts_raw, (bytes, bytearray)):
        try:
            al = decode_fields(alerts_raw)
            # The alerts message was present, so every alert we know about
            # has a definite value — an unset bool is False, not unknown.
            for no, name in _ALERT_NAMES.items():
                st.alerts[name] = _scalar_bool(al, no)
        except ValueError as exc:
            logger.debug("alerts undecodable: %s", exc)

    return st


def get_dish_status(host: str = DISH_HOST, port: int = DISH_PORT,
                    timeout: int = DEFAULT_TIMEOUT) -> DishStatus:
    """Ask the dish for its current status.

    Never raises. An unreachable dish is ``state="unreachable"`` — which is
    NOT a claim that the uplink is healthy, only that we could not ask.
    """
    payload = _encode_key(_REQ_GET_STATUS, 2) + b"\x00"  # get_status: {} (empty)
    body, err = _grpc_call(payload, host, port, timeout)
    if err is not None:
        state = "unsupported" if "curl not found" in err else "unreachable"
        logger.debug("dish query failed: %s", err)
        return DishStatus(state, err)
    return parse_status_response(body or b"")


def format_status(st: DishStatus) -> str:
    """Human-readable block for the TUI pane. Degraded states say so first."""
    if st.state != "ok":
        return f"Starlink dish: {st.state.upper()} — {st.detail}"

    def num(val, fmt, suffix="", scale=1.0):
        return f"{val * scale:{fmt}}{suffix}" if val is not None else "unknown"

    lines = [
        f"Dish        {st.dish_id or 'unknown'}",
        f"Hardware    {st.hardware_version or 'unknown'}",
        f"Software    {st.software_version or 'unknown'}",
        f"Uptime      {f'{st.uptime_s // 3600} h' if st.uptime_s is not None else 'unknown'}",
        "",
        f"Latency     {num(st.pop_ping_latency_ms, '.1f', ' ms')}",
        f"Ping drop   {num(st.pop_ping_drop_rate, '.2f', ' %', 100.0)}",
        f"Downlink    {num(st.downlink_throughput_bps, '.2f', ' Mbps', 1e-6)}",
        f"Uplink      {num(st.uplink_throughput_bps, '.2f', ' Mbps', 1e-6)}",
        f"Ethernet    {st.eth_speed_mbps if st.eth_speed_mbps is not None else 'unknown'} Mbps",
        "",
        f"Obstructed  {num(st.fraction_obstructed, '.3f', ' %', 100.0)}"
        f" (now: {'unknown' if st.currently_obstructed is None else ('YES' if st.currently_obstructed else 'no')})",
        f"Obs window  {num(st.obstruction_valid_s, '.0f', ' s')} observed",
        "",
        f"Boresight   az {num(st.boresight_azimuth_deg, '.1f', ' deg')}"
        f" / el {num(st.boresight_elevation_deg, '.1f', ' deg')}",
        f"SNR         {'above noise floor' if st.snr_above_noise_floor else 'BELOW noise floor' if st.snr_above_noise_floor is not None else 'unknown'}",
    ]
    active = st.active_alerts
    lines.append("")
    lines.append(f"Alerts      {', '.join(active) if active else 'none active'}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import sys

    status = get_dish_status()
    print(format_status(status))
    # Exit code carries the tri-state so a shell caller can branch on it:
    # 0 ok, 1 answered-but-unreadable, 2 could not ask. Unreachable is NOT 0.
    sys.exit({"ok": 0, "malformed": 1}.get(status.state, 2))


# ── obstruction map ──────────────────────────────────────────────────────────
# The dish publishes a square grid of per-direction SNR. Field numbers read
# from the dish's own bundle; semantics corroborated against Starlink's own
# docs and the community starlink-grpc-tools implementation (2026-09-13).
_REQ_GET_OBSTRUCTION_MAP = 2008   # Request.dish_get_obstruction_map
_RESP_OBSTRUCTION_MAP = 2008      # Response.dish_get_obstruction_map (same no.)

_MAP_NUM_ROWS = 1
_MAP_NUM_COLS = 2
_MAP_SNR = 3                      # packed repeated float
_MAP_MIN_ELEVATION_DEG = 4
_MAP_MAX_THETA_DEG = 5
_MAP_REFERENCE_FRAME = 6

#: ObstructionMapReferenceFrame
FRAME_UNKNOWN, FRAME_EARTH, FRAME_UT = 0, 1, 2
_FRAME_NAMES = {
    FRAME_UNKNOWN: "unknown",
    FRAME_EARTH: "earth-aligned (top = true north)",
    FRAME_UT: "dish-aligned (relative to the terminal)",
}

#: A cell the dish has never seen a satellite through.
NO_DATA = -1.0


@dataclass
class ObstructionMap:
    """A per-direction SNR grid, row-major, ``num_rows`` x ``num_cols``.

    Cell values are 0.0-1.0 where observed, and exactly ``-1.0`` where the
    dish has never seen a satellite in that direction.

    ⚠️ **-1.0 conflates two different things** and this module refuses to
    paper over it. A cell may be -1.0 because the sky there has not been
    surveyed YET, or because no satellite will EVER pass through it from this
    latitude — the constellation's inclination and the dish's elevation mask
    leave permanently unvisited directions. So the surveyed fraction is a
    COVERAGE figure, never a progress bar: a map that stops at 32% may be
    complete. Reporting "68% remaining" would be the same defect class as
    treating an unobserved value as a healthy one.
    """

    state: str
    detail: str = ""
    num_rows: Optional[int] = None
    num_cols: Optional[int] = None
    min_elevation_deg: Optional[float] = None
    max_theta_deg: Optional[float] = None
    reference_frame: Optional[int] = None
    cells: Optional[list] = None

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    @property
    def frame_name(self) -> str:
        return _FRAME_NAMES.get(self.reference_frame, "unknown")

    def census(self) -> Dict[str, int]:
        """Count cells by kind, splitting in-field from geometric corners.

        The grid is a SQUARE holding a DISC of sky: corner cells lie outside
        the field of view and are -1.0 forever. Counting them as "unsurveyed"
        would understate coverage by about 21% on a 123x123 map.
        """
        out = {"in_field": 0, "surveyed": 0, "clear": 0, "obstructed": 0,
               "partial": 0, "no_data": 0, "outside_field": 0}
        if not self.ok or not self.cells or not self.num_rows or not self.num_cols:
            return out
        rows, cols = self.num_rows, self.num_cols
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        radius = min(rows, cols) / 2.0
        for idx, val in enumerate(self.cells):
            row, col = divmod(idx, cols)
            if ((col - cx) ** 2 + (row - cy) ** 2) ** 0.5 > radius:
                out["outside_field"] += 1
                continue
            out["in_field"] += 1
            if val < 0:
                out["no_data"] += 1
                continue
            out["surveyed"] += 1
            if val >= 0.999:
                out["clear"] += 1
            elif val <= 0.001:
                out["obstructed"] += 1
            else:
                out["partial"] += 1
        return out


    def cell_bearing(self, row: int, col: int):
        """(azimuth_deg, elevation_deg) for a cell, or None if unmappable.

        The grid's radius maps linearly to the zenith angle: the disc edge is
        ``max_theta_deg`` from straight up, and the dish reports
        ``min_elevation_deg`` as its complement (80 + 10 = 90 on the live
        dish, which is the check that this mapping is the intended one).

        ⚠️ Azimuth is only a compass bearing in FRAME_EARTH, where the
        top-centre pixel points at TRUE NORTH. In FRAME_UT the angle is
        relative to the terminal's own heading, so returning a "bearing"
        would send someone to look at the wrong tree — refuse instead.
        """
        if self.reference_frame != FRAME_EARTH:
            return None
        if not self.num_rows or not self.num_cols or self.max_theta_deg is None:
            return None
        cx = (self.num_cols - 1) / 2.0
        cy = (self.num_rows - 1) / 2.0
        radius = min(self.num_rows, self.num_cols) / 2.0
        dx, dy = col - cx, row - cy
        r = (dx * dx + dy * dy) ** 0.5
        if r > radius:
            return None
        # Screen-up is north, and azimuth runs clockwise (N->E->S->W).
        azimuth = math.degrees(math.atan2(dx, -dy)) % 360.0
        theta = (r / radius) * self.max_theta_deg if radius else 0.0
        return azimuth, 90.0 - theta

    def obstruction_bearings(self, limit: int = 8):
        """Obstructed cells as (azimuth, elevation, snr), worst first.

        This is the actionable end of the map: "something blocks the sky at
        bearing X, elevation Y" is a thing an operator can walk outside and
        look at. Returns [] when the frame is not earth-aligned — an
        unmappable bearing is withheld, never guessed.
        """
        if not self.ok or not self.cells or not self.num_cols:
            return []
        found = []
        for idx, val in enumerate(self.cells):
            if val < 0 or val > 0.5:
                continue
            row, col = divmod(idx, self.num_cols)
            bearing = self.cell_bearing(row, col)
            if bearing is not None:
                found.append((bearing[0], bearing[1], val))
        found.sort(key=lambda t: t[2])
        return found[:limit]


def parse_obstruction_map(body: bytes) -> ObstructionMap:
    """Decode a Response frame into an ObstructionMap. Pure."""
    try:
        message = _unframe(body)
        if message is None:
            return ObstructionMap("malformed", "reply was not a complete gRPC frame")
        top = decode_fields(message)
        raw = _one(top, _RESP_OBSTRUCTION_MAP)
        if raw is None:
            seen = ",".join(str(k) for k in sorted(top)) or "none"
            return ObstructionMap(
                "malformed",
                f"no dish_get_obstruction_map ({_RESP_OBSTRUCTION_MAP}); fields: {seen}")
        m = decode_fields(raw)
    except (ValueError, struct.error) as exc:
        return ObstructionMap("malformed", f"protobuf decode failed: {exc}")

    rows = _one(m, _MAP_NUM_ROWS)
    cols = _one(m, _MAP_NUM_COLS)
    packed = _one(m, _MAP_SNR)
    if not isinstance(rows, int) or not isinstance(cols, int):
        return ObstructionMap("malformed", "map has no usable dimensions")
    if not isinstance(packed, (bytes, bytearray)) or len(packed) % 4:
        return ObstructionMap("malformed", "snr payload is not a float32 array")

    cells = list(struct.unpack("<%df" % (len(packed) // 4), bytes(packed)))
    # Dimensions and payload must agree. A mismatch means we are reading the
    # grid with the wrong stride, which yields a picture that LOOKS like a sky
    # map and is wrong everywhere — far worse than refusing.
    if len(cells) != rows * cols:
        return ObstructionMap(
            "malformed",
            f"{rows}x{cols} declared but {len(cells)} cells received")

    frame = _one(m, _MAP_REFERENCE_FRAME)
    return ObstructionMap(
        "ok",
        num_rows=rows,
        num_cols=cols,
        min_elevation_deg=_scalar_num(m, _MAP_MIN_ELEVATION_DEG),
        max_theta_deg=_scalar_num(m, _MAP_MAX_THETA_DEG),
        reference_frame=frame if isinstance(frame, int) else FRAME_UNKNOWN,
        cells=cells,
    )


def get_obstruction_map(host: str = DISH_HOST, port: int = DISH_PORT,
                        timeout: int = DEFAULT_TIMEOUT) -> ObstructionMap:
    """Fetch the obstruction map. Never raises; degraded states are explicit."""
    payload = _encode_key(_REQ_GET_OBSTRUCTION_MAP, 2) + b"\x00"
    body, err = _grpc_call(payload, host, port, timeout)
    if err is not None:
        state = "unsupported" if "curl not found" in err else "unreachable"
        logger.debug("obstruction map query failed: %s", err)
        return ObstructionMap(state, err)
    return parse_obstruction_map(body or b"")


#: Glyphs, densest-ink-for-worst-news. ' ' outside the field is intentional:
#: the disc's edge should read as the horizon, not as missing data.
_GLYPH_OUTSIDE = " "
_GLYPH_NO_DATA = "·"   # · never seen a satellite this way
_GLYPH_CLEAR = "░"     # ░ observed clear
_GLYPH_PARTIAL = "▒"   # ▒ partially obstructed
_GLYPH_OBSTRUCTED = "█"  # █ obstructed


def render_obstruction_map(m: ObstructionMap, width: int = 61) -> str:
    """ASCII sky map, downsampled to ``width`` columns.

    Terminal cells are about twice as tall as wide, so rows are sampled at
    half the column rate to keep the sky disc looking round rather than
    squashed into an ellipse.

    In FRAME_EARTH the grid is earth-aligned with the top-centre pixel toward
    TRUE NORTH, so the picture is north-up and directly comparable to what an
    operator sees standing at the dish. In FRAME_UT it is relative to the
    terminal's own orientation — the header says which, because reading a
    dish-aligned map as though it were north-up would point you at the wrong
    tree.
    """
    if not m.ok or not m.cells or not m.num_rows or not m.num_cols:
        return f"obstruction map: {m.state.upper()} — {m.detail}"

    rows, cols = m.num_rows, m.num_cols
    width = max(9, min(width, cols))
    height = max(5, width // 2)
    cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
    radius = min(rows, cols) / 2.0

    lines = []
    for oy in range(height):
        src_row = int(oy * rows / height)
        out = []
        for ox in range(width):
            src_col = int(ox * cols / width)
            if ((src_col - cx) ** 2 + (src_row - cy) ** 2) ** 0.5 > radius:
                out.append(_GLYPH_OUTSIDE)
                continue
            val = m.cells[src_row * cols + src_col]
            if val < 0:
                out.append(_GLYPH_NO_DATA)
            elif val <= 0.001:
                out.append(_GLYPH_OBSTRUCTED)
            elif val >= 0.999:
                out.append(_GLYPH_CLEAR)
            else:
                out.append(_GLYPH_PARTIAL)
        lines.append("".join(out).rstrip())

    c = m.census()
    pct = (100.0 * c["surveyed"] / c["in_field"]) if c["in_field"] else 0.0
    header = [
        f"Sky map  {rows}x{cols}  frame: {m.frame_name}",
        f"         elevation mask {m.min_elevation_deg:.0f} deg"
        f" · field half-angle {m.max_theta_deg:.0f} deg"
        if m.min_elevation_deg is not None and m.max_theta_deg is not None else "",
        "",
    ]
    footer = [
        "",
        f"  {_GLYPH_CLEAR} clear {c['clear']}   {_GLYPH_PARTIAL} partial"
        f" {c['partial']}   {_GLYPH_OBSTRUCTED} obstructed {c['obstructed']}"
        f"   {_GLYPH_NO_DATA} no data {c['no_data']}",
        f"  surveyed {c['surveyed']}/{c['in_field']} in-field cells ({pct:.1f}%)",
        "  NOTE: 'no data' means no satellite has been seen that way. Some of",
        "  those directions are never visited from this latitude, so this is a",
        "  COVERAGE figure, not a progress bar — it may never reach 100%.",
    ]
    bearings = m.obstruction_bearings()
    if bearings:
        footer.append("")
        footer.append("  Obstructions (walk outside and look):")
        for az, el, snr in bearings:
            footer.append(f"    bearing {az:5.1f} deg  elevation {el:4.1f} deg"
                          f"  (snr {snr:.2f})")
    elif m.reference_frame != FRAME_EARTH and m.census()["obstructed"]:
        footer.append("")
        footer.append("  Obstructed cells exist but the map is not earth-aligned,")
        footer.append("  so no compass bearing can be given for them.")
    return "\n".join([ln for ln in header if ln != ""] + [""] + lines + footer)

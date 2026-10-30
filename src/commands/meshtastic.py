"""
Meshtastic Commands

Provides unified interface for Meshtastic operations.
Used by both TUI and CLI interfaces.
"""

import subprocess
import logging
import os
import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from .base import CommandResult
from utils.paths import get_real_user_home
from utils.tx_guard import assert_cli_args_allowed

# utils.message_listener is imported where used: at module level it pulls the
# meshtastic package (~200 ms) into every TUI handler importing this module.

logger = logging.getLogger(__name__)

# Connection settings (module-level state)
_connection_type = "localhost"  # localhost, serial, ble
_connection_value = "localhost"


@dataclass
class ConnectionConfig:
    """Meshtastic connection configuration."""
    type: str  # "localhost", "serial", "ble"
    value: str  # hostname/IP, serial port, or BLE address


def _find_cli() -> Optional[str]:
    """Find the meshtastic CLI executable."""
    try:
        result = subprocess.run(
            ['which', 'meshtastic'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Check common locations - use get_real_user_home() not Path.home()
    common_paths = [
        '/usr/local/bin/meshtastic',
        '/usr/bin/meshtastic',
        str(get_real_user_home() / '.local' / 'bin' / 'meshtastic'),
    ]
    for path in common_paths:
        if Path(path).exists():
            return path

    return None


def _get_connection_args() -> List[str]:
    """Get connection arguments based on current settings — never [].

    A transport-less CLI VID-autodetects a serial port and can open an
    RNode rnsd holds (10-30 audit), so every branch names one.
    """
    _load_persisted_connection()
    if _connection_type == "localhost":
        return ["--host", _connection_value]
    elif _connection_type == "serial":
        # Re-checked at USE, not just at save: ttyACM numbering swaps on
        # replug/reboot, so a port saved as "the Meshtastic radio" can become
        # the RNode rnsd holds (10-30 review r4 #2). Never hand that to the CLI.
        # REFUSED, never re-routed: quietly sending to localhost instead
        # would land a write/send on a different radio (r5 #2). A config we
        # cannot read is blind, not clear (r5 #3).
        configs = _rns_config_paths()
        blind = [str(c) for c in configs if Path(c).exists() and not os.access(c, os.R_OK)]
        if blind:
            raise TransportRefused(
                f"The saved Meshtastic serial port {_connection_value} cannot be "
                f"checked: the Reticulum config {', '.join(blind)} is unreadable "
                f"here, so it may be an RNode. Run as admin, or re-choose in "
                f"Settings > Meshtastic Connection.")
        declared = _rns_declared_ports(configs)
        if _real(_connection_value) in declared:
            raise TransportRefused(
                f"The saved Meshtastic serial port {_connection_value} is NOW a "
                f"Reticulum interface port ({declared[_real(_connection_value)]}) "
                f"— an RNode (USB renumbering?). Nothing was sent. Re-choose it "
                f"in Settings > Meshtastic Connection.")
        return ["--port", _connection_value]
    elif _connection_type == "ble" and (_connection_value or "").strip():
        # An EMPTY address falls through to the default rather than
        # "--ble ''" (= whichever radio is nearby).
        return ["--ble", _connection_value.strip()]
    return ["--host", "localhost"]


class TransportRefused(Exception):
    """The configured transport must not be used (a saved serial port that is
    now an RNode, or cannot be proven not to be). Callers report it and send
    NOTHING — never fall back to another radio."""


def _guard_target():
    """(host, port) for the TX guard — the SAME target the argv carries,
    split at the last ':' (a saved remote is stored as host:port; passing it
    whole gave the guard "h:4403:4403", r5 #1). Serial/BLE -> (None, None)."""
    if _connection_type != "localhost":
        return None, None
    host, sep, port = _connection_value.rpartition(":")
    if sep and port.isdigit() and ":" not in host:
        return host, int(port)
    return _connection_value, None


def cli_transport_args() -> List[str]:
    """The EXPLICIT transport every meshtastic CLI argv must carry.

    With no --host/--port the CLI auto-detects a serial port by USB VID
    (whitelistVids {0x239a, 0x303a}); an ESP32-S3 RNode is 0x303a and rnsd
    holds its port without an exclusive lock, so a bare `meshtastic --info`
    can open the RNode (TCSAFLUSH, DTR/RTS, wake bytes). Returns the
    configured connection — the one chosen in Settings > Meshtastic
    Connection, persisted — default --host localhost. It never picks a USB
    port (operator 10-30). tests/test_meshtastic_cli_transport_guard.py
    enforces every call site. Raises TransportRefused when the saved serial
    port is (or may be) an RNode — the caller sends nothing.
    """
    return _get_connection_args()


# -- the persisted CLI connection (Settings > Meshtastic Connection) --------
#: Until 10-30 a serial choice could not be saved at all, so a USB-only box
#: depended on the auto-pick that could hit an RNode. One small settings file.
_CLI_SETTINGS = "meshtastic_cli"
_persisted_loaded = False
_persisted_seen_stamp = None
#: True once set_connection() chose a target IN THIS PROCESS (that wins over
#: the saved file). Auto-detect never sets it (r5 #7).
_explicit_in_process = False


def _persisted_stamp():
    """mtime of the saved file (None if absent) — a long-lived process
    reloads when another process saves a new choice (r5 #5)."""
    try:
        from utils.common import CONFIG_DIR
        return os.path.getmtime(Path(CONFIG_DIR) / f"{_CLI_SETTINGS}.json")
    except (OSError, ImportError):
        return None


def _read_persisted() -> Dict[str, Any]:
    from utils.common import SettingsManager
    return SettingsManager(_CLI_SETTINGS, defaults={}).all()


def _write_persisted(conn_type: str, value: str) -> bool:
    from utils.common import SettingsManager
    sm = SettingsManager(_CLI_SETTINGS, defaults={})
    sm.update({"type": conn_type, "value": value})
    return bool(sm.save())


def _load_persisted_connection() -> None:
    """Apply the saved connection (again whenever the file changes); an
    in-process choice (set_connection) always wins."""
    global _connection_type, _connection_value, _persisted_loaded, _persisted_seen_stamp
    if _explicit_in_process:
        return
    stamp = _persisted_stamp()
    if _persisted_loaded and stamp == _persisted_seen_stamp:
        return
    _persisted_loaded, _persisted_seen_stamp = True, stamp
    try:
        saved = _read_persisted() or {}
    except Exception as e:   # a broken settings file must not break the CLI
        logger.warning("meshtastic CLI connection not loaded (%s) — using localhost", e)
        return
    t, v = saved.get("type"), saved.get("value")
    if t in ("localhost", "serial", "ble") and isinstance(v, str) and v.strip():
        _connection_type, _connection_value = t, v.strip()
    else:
        _connection_type, _connection_value = "localhost", "localhost"


def _redact_secrets(argv):
    """argv for LOGGING: the value after `--ch-set psk` and a `--seturl` URL
    (a channel URL encodes every key) become <redacted>. Keys reached the
    DEBUG log in full once custom PSKs started to succeed (reader pair 2,
    2026-09-30) — the same class scripts/mesh_psk_safe.py exists for."""
    out = list(argv)
    for i, a in enumerate(out[:-1]):
        if a == "--seturl" or (a == "psk" and i > 0 and out[i - 1] == "--ch-set"):
            out[i + 1] = "<redacted>"
    return out


def _run_command(args: List[str], timeout: int = 60, auto_detect: bool = True) -> CommandResult:
    """
    Run a meshtastic CLI command.

    Args:
        args: Command arguments (without 'meshtastic' prefix)
        timeout: Command timeout in seconds
        auto_detect: If True, retry with auto-detection on connection failure

    Returns:
        CommandResult with output
    """
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    # RF egress chokepoint at the RUNNER, so a new transmitting call site is
    # covered the day it is written (--traceroute / --request-position key the
    # radio exactly as --sendtext does). Pass the REAL connection target: with
    # host=None the guard records every CLI send as the local radio, which
    # both misnames a remote target in the blocked-attempts record and lets a
    # local-radio allowlist entry cover a send actually aimed elsewhere.
    _load_persisted_connection()   # the guard must judge the SAVED target (r4 #1)
    try:
        transport = _get_connection_args()
    except TransportRefused as e:
        return CommandResult.fail(str(e), error="transport_refused")
    host, port = _guard_target()
    assert_cli_args_allowed(args, host, port, detail="commands.meshtastic._run_command")

    full_args = [cli_path] + transport + args
    cmd_str = ' '.join(_redact_secrets(full_args))
    logger.debug(f"Running: {cmd_str}")

    try:
        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            return CommandResult.ok(
                message="Command completed",
                data={'stdout': result.stdout, 'stderr': result.stderr},
                raw=result.stdout
            )
        else:
            error_text = result.stderr or result.stdout

            # Connection refused: auto-detect ONLY from the unconfigured
            # default. A saved/explicit target that refuses FAILS as itself —
            # it must never be quietly re-run on the local radio (r4 #3).
            if auto_detect and 'Connection refused' in error_text and _is_unconfigured():
                logger.info("Connection refused, attempting auto-detect...")
                detect_result = auto_detect_connection()
                if detect_result.success:
                    # Retry with new connection
                    return _run_command(args, timeout, auto_detect=False)

                # Provide helpful message for connection failure. Use
                # ``not_available`` (not ``fail``) — semantically right
                # for "device unreachable" + ``fail()`` doesn't accept
                # ``fix_hint`` and would raise TypeError here, caught
                # only by the outer ``except Exception`` which would
                # then surface the Python error string to the operator
                # instead of this helpful message. Pattern-audit
                # Finding #11 (2026-05-19).
                # auto_detect's own text: the USB listing and "choose it in
                # Settings" (r4 #4) — not a generic shell hint (MF018).
                return CommandResult.not_available(
                    message="Cannot connect to Meshtastic device (meshtasticd "
                            "did not answer).\n\n" + detect_result.message,
                    fix_hint="Check the device, or start meshtasticd (Service "
                             "Control); a USB radio is chosen in Settings > "
                             "Meshtastic Connection",
                )

            return CommandResult.fail(
                message=f"Command failed: {error_text}",
                error=error_text,
                raw=result.stdout
            )

    except KeyboardInterrupt:
        return CommandResult.fail(
            "Command aborted by user",
            error="interrupted"
        )
    except subprocess.TimeoutExpired:
        return CommandResult.fail(
            f"Command timed out after {timeout}s",
            error="timeout"
        )
    except FileNotFoundError:
        return CommandResult.not_available(
            "Meshtastic CLI not found",
            fix_hint="pip install meshtastic"
        )
    except Exception as e:
        return CommandResult.fail(
            f"Command error: {str(e)}",
            error=str(e)
        )


# Connection Management

def set_connection(conn_type: str, value: str) -> CommandResult:
    """
    Set the connection type and value.

    Args:
        conn_type: "localhost", "serial", or "ble"
        value: hostname/IP, serial port, or BLE address
    """
    global _connection_type, _connection_value

    if conn_type not in ("localhost", "serial", "ble"):
        return CommandResult.fail(f"Invalid connection type: {conn_type}")

    _connection_type = conn_type
    _connection_value = value
    global _explicit_in_process
    _explicit_in_process = True

    return CommandResult.ok(
        f"Connection set to {conn_type}: {value}",
        data={'type': conn_type, 'value': value}
    )


def get_connection() -> ConnectionConfig:
    """Get current connection configuration."""
    return ConnectionConfig(type=_connection_type, value=_connection_value)


def test_connection() -> CommandResult:
    """Test the current connection by requesting node info."""
    result = get_node_info()
    if result.success:
        return CommandResult.ok(
            "Connection successful",
            data=result.data
        )
    return CommandResult.fail(
        "Connection failed",
        error=result.error
    )


def _is_unconfigured() -> bool:
    """The untouched default (nothing saved, nothing set in-process)."""
    _load_persisted_connection()
    return (not _explicit_in_process
            and (_connection_type, _connection_value) == ("localhost", "localhost"))


#: hostname or IPv4, optional :port. Never a leading '-' (a CLI flag). No
#: IPv6: the CLI splits --host on ':' and cannot parse it (r5 #6).
_TCP_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})(?::(\d{1,5}))?$")


#: Reticulum configs whose interface ports are rnsd's (RNodeInterface,
#: KISSInterface, SerialInterface). Resolved per call (MF001: real home).
def _rns_config_paths() -> List[Path]:
    home = get_real_user_home()
    return [Path("/etc/reticulum/config"), home / ".reticulum" / "config",
            home / ".config" / "reticulum" / "config"]


def _meshtasticd_tcp_up() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 4403), timeout=1.0):
            return True
    except OSError:
        return False


def _real(path) -> str:
    try:
        return os.path.realpath(str(path))
    except OSError:
        return str(path)


def _rns_declared_ports(configs) -> Dict[str, str]:
    """realpath -> config file, for every `port = /dev/...` a readable
    Reticulum config declares (used to REFUSE and to LABEL, never to pick)."""
    out: Dict[str, str] = {}
    for cfg in configs:
        try:
            text = Path(cfg).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"^\s*port\s*=\s*(/\S+)", text, re.M):
            out[_real(mm.group(1))] = str(cfg)
    return out


def save_connection(conn_type: str, value: str, *, rns_configs=None,
                    dev_root=None) -> CommandResult:
    """Validate, persist and apply the CLI connection (Settings > Meshtastic
    Connection). A serial port must be an existing node under /dev and NOT a
    port a Reticulum config declares (an RNode). Nothing is written on a
    refusal; a failed write is never reported as saved."""
    value = (value or "").strip()
    if conn_type not in ("localhost", "serial", "ble") or not value:
        return CommandResult.fail(f"Not saved: invalid connection {conn_type!r} {value!r}")
    if conn_type == "localhost":
        mm = _TCP_HOST_RE.match(value)
        if not mm or (mm.group(1) and not 1 <= int(mm.group(1)) <= 65535):
            return CommandResult.fail(
                f"Not saved: {value!r} is not host or host:port (IPv6 is not "
                f"supported by the meshtastic CLI)")
    if conn_type == "serial":
        root = Path(dev_root) if dev_root else Path("/dev")
        p = Path(value)
        if not p.is_absolute() or ".." in p.parts or not str(p).startswith(str(root) + "/"):
            return CommandResult.fail(f"Not saved: {value} is not a device under {root}/")
        if not p.exists():
            return CommandResult.fail(f"Not saved: {value} does not exist (is the radio plugged in?)")
        configs = list(rns_configs if rns_configs is not None else _rns_config_paths())
        blind = [str(c) for c in configs if Path(c).exists() and not os.access(c, os.R_OK)]
        if blind:
            # Exists but unreadable = blind, not clear (r4 #5).
            return CommandResult.fail(
                f"Not saved: the Reticulum config {', '.join(blind)} could not be "
                f"read, so MeshForge cannot prove {value} is not an RNode. Run "
                f"the TUI as admin to choose a serial port.")
        declared = _rns_declared_ports(configs)
        if _real(p) in declared:
            return CommandResult.fail(
                f"Not saved: {value} is a Reticulum interface port in "
                f"{declared[_real(p)]} — that is an RNode rnsd uses, not a "
                f"Meshtastic radio.")
    if conn_type == "serial":
        # Save the STABLE name when one exists: ttyUSB*/ttyACM* renumber on
        # replug, /dev/serial/by-id/* does not (r4 #2).
        byid = root / "serial" / "by-id"
        try:
            for link in sorted(byid.iterdir()):
                if _real(link) == _real(value):
                    value = str(link)
                    break
        except OSError:
            pass
    if not _write_persisted(conn_type, value):
        return CommandResult.fail("Not saved: the settings file could not be written.")
    set_connection(conn_type, value)
    return CommandResult.ok(f"Meshtastic CLI connection saved: {conn_type} {value}",
                            data={"type": conn_type, "value": value})


def auto_detect_connection(*, dev_dir=None, rns_configs=None) -> CommandResult:
    """
    Auto-detect the connection: TCP 4403 (meshtasticd) — or NOTHING.

    It never picks a USB serial port (operator 10-30). The old fallback took
    the FIRST ttyUSB/ttyACM, and _run_command reaches here on any
    "Connection refused" and re-runs the command with an explicit --port:
    on the manager box that was the RNode rnsd holds. USB ports are LISTED
    (Reticulum ones marked) so the operator can choose one once in Settings.

    Returns:
        CommandResult with data={'type': ..., 'value': ...} on success
    """
    if _meshtasticd_tcp_up():
        # The default already IS localhost; setting it would mark the box
        # "configured" and disable later auto-detect (r5 #7).
        return CommandResult.ok(
            "Using TCP connection to meshtasticd (port 4403)",
            data={'type': 'localhost', 'value': 'localhost', 'method': 'tcp'}
        )
    dev = Path(dev_dir) if dev_dir else Path('/dev')
    usb = sorted(list(dev.glob('ttyUSB*')) + list(dev.glob('ttyACM*')))
    declared = _rns_declared_ports(rns_configs if rns_configs is not None
                                   else _rns_config_paths())
    if usb:
        lines = [f"  {p}" + (f"  (Reticulum interface port — an RNode, not offered)"
                             if _real(p) in declared else "") for p in usb]
        return CommandResult.fail(
            "meshtasticd is not answering on port 4403.\n\n"
            "MeshForge never picks a USB serial port by itself — it can be an "
            "RNode. If your Meshtastic radio is on USB, choose it once in "
            "Settings > Meshtastic Connection > Serial Port.\n\n"
            "USB serial ports on this box:\n" + "\n".join(lines),
            error="no_device")
    return CommandResult.fail(
        "No Meshtastic device found.\n\n"
        "For USB radios: Connect device to USB port\n"
        "For HAT radios: Start meshtasticd (Service Control)",
        error="no_device"
    )


def ensure_connection() -> CommandResult:
    """
    Ensure we have a valid connection, auto-detecting if needed.

    Call this before running commands to ensure proper connection.
    """
    _load_persisted_connection()   # see the saved choice first (r4 #8)
    if not _is_unconfigured():
        # A configured target is tested AS ITSELF; on failure it is reported,
        # never swapped for an auto-detected one (r4 #3).
        test_result = test_connection()
        if test_result.success:
            return CommandResult.ok(
                f"Connected via {_connection_type}: {_connection_value}",
                data={'type': _connection_type, 'value': _connection_value}
            )
        return CommandResult.fail(
            f"The configured Meshtastic connection ({_connection_type}: "
            f"{_connection_value}) is not answering. Change it in Settings > "
            f"Meshtastic Connection.", error=test_result.error)

    # Auto-detect
    return auto_detect_connection()


# Information Commands

def get_node_info() -> CommandResult:
    """Get local node information."""
    return _run_command(["--info"])


def list_nodes() -> CommandResult:
    """List all known nodes in the mesh."""
    return _run_command(["--nodes"])


def get_nodes() -> CommandResult:
    """
    Get all known nodes in the mesh with structured data.

    Returns:
        CommandResult with data={'nodes': [{'id': ..., 'name': ..., ...}]}
    """
    result = list_nodes()
    if not result.success:
        return result

    # Parse the raw output into structured node data
    nodes = []
    raw = result.raw or ''

    try:
        # Parse meshtastic --nodes output
        # Format varies but typically includes table with node info
        lines = raw.strip().split('\n')

        for line in lines:
            # Skip header lines and empty lines
            if not line.strip() or line.startswith('─') or line.startswith('|'):
                continue
            if 'User' in line and 'ID' in line:
                continue  # Header row

            # Try to parse node entries
            # Typical format: !ba4bf9d0  NodeName  ...
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith('!'):
                node = {
                    'id': parts[0],
                    'name': parts[1] if len(parts) > 1 else 'Unknown',
                    'snr': 'N/A',
                    'last_heard': 'N/A'
                }

                # Try to extract SNR if present
                for i, part in enumerate(parts):
                    if 'snr' in part.lower() or (i > 1 and part.replace('-', '').replace('.', '').isdigit()):
                        try:
                            node['snr'] = f"{float(part):.1f} dB"
                        except ValueError:
                            pass

                nodes.append(node)

        return CommandResult.ok(
            f"Found {len(nodes)} nodes",
            data={'nodes': nodes},
            raw=raw
        )
    except Exception as e:
        logger.warning(f"Failed to parse nodes: {e}")
        # Return raw data on parse failure
        return CommandResult.ok(
            "Nodes retrieved (unparsed)",
            data={'nodes': [], 'raw': raw},
            raw=raw
        )


def get_settings(setting: str = "all") -> CommandResult:
    """
    Get node settings.

    Args:
        setting: Specific setting or "all" for all settings
    """
    return _run_command(["--get", setting])


def get_channel_info(channel_index: int = 0) -> CommandResult:
    """Get channel information."""
    return _run_command(["--ch-index", str(channel_index), "--info"])


# Location Commands

def set_position(lat: float, lon: float, alt: float = 0) -> CommandResult:
    """
    Set node position.

    Args:
        lat: Latitude in decimal degrees
        lon: Longitude in decimal degrees
        alt: Altitude in meters
    """
    return _run_command([
        "--setlat", str(lat),
        "--setlon", str(lon),
        "--setalt", str(alt)
    ])


def request_position(dest: str, channel_index: int = 0) -> CommandResult:
    """
    Request position from a remote node.

    Args:
        dest: Destination node ID (e.g., !ba4bf9d0)
        channel_index: Channel to use
    """
    return _run_command([
        "--request-position",
        "--dest", dest,
        "--ch-index", str(channel_index)
    ])


# Messaging Commands

# Recommended hop limits based on message type and network conditions
# Per Meshtastic docs: https://meshtastic.org/docs/overview/mesh-algo/
HOP_LIMIT_DM_INITIAL = 7       # First DM uses flood to discover path
HOP_LIMIT_DM_ESTABLISHED = 3   # Subsequent DMs use discovered next-hop
HOP_LIMIT_BROADCAST = 3        # Public channel broadcasts
HOP_LIMIT_EMERGENCY = 7        # Emergency/SAR messages


def _parse_dest_num(dest: Optional[str]) -> Optional[int]:
    """The node number ``send_message`` sends to — hex first, then decimal,
    else None (= broadcast). ONE parse, shared by the byte-limit check and the
    send, so both judge the same destination (F18 review)."""
    if not dest:
        return None
    try:
        return int(dest.lstrip('!'), 16)
    except ValueError:
        try:
            return int(dest)
        except ValueError:
            return None


def send_message(
    text: str,
    dest: Optional[str] = None,
    channel_index: int = 0,
    ack: bool = False,
    hop_limit: Optional[int] = None,
    want_ack: bool = True
) -> CommandResult:
    """
    Send a text message with configurable routing.

    Args:
        text: Message text
        dest: Destination node ID (None for broadcast)
        channel_index: Channel to use (0 for DM, 1+ for public channels)
        ack: Request delivery acknowledgment (blocks until ack received)
        hop_limit: Override hop limit (1-7). None = use smart defaults:
                   - DM (dest specified): 7 for discovery, 3 once path known
                   - Broadcast: 3
        want_ack: Request ack without blocking (recipient sends ack)

    Returns:
        CommandResult with send status

    Note on routing (from Meshtastic mesh-algo):
        - DMs use next-hop routing: first message floods to find path,
          subsequent messages use shortest discovered path
        - Higher hop_limit helps initial path discovery but creates traffic
        - Lower hop_limit reduces congestion once path is established
    """
    # Byte budget before EITHER send path (bridge matrix F18): when the direct
    # send refuses an oversize text, the CLI fallback below would air it.
    from gateway.base_handler import mesh_onair_limit
    dest_num = _parse_dest_num(dest)
    budget = mesh_onair_limit(dest_num)
    size = len(text.encode('utf-8'))
    if size > budget:
        return CommandResult.fail(
            f"Message is {size} bytes; one radio packet carries at most "
            f"{budget} bytes for a {'DM' if budget < mesh_onair_limit(None) else 'broadcast'}. "
            f"Shorten it, or send it through messaging (which splits it).")

    # Build command args
    args = ["--ch-index", str(channel_index)]

    # Smart hop limit defaults based on message type
    if hop_limit is not None:
        # User specified - validate and use
        if not 1 <= hop_limit <= 7:
            return CommandResult.fail(f"hop_limit must be 1-7, got {hop_limit}")
    else:
        # Smart defaults based on destination
        if dest and dest != '!ffffffff':
            # DM - use higher hop for better path discovery
            hop_limit = HOP_LIMIT_DM_INITIAL
        else:
            # Broadcast - lower hop to reduce congestion
            hop_limit = HOP_LIMIT_BROADCAST

    logger.debug(f"Sending with intended hop_limit={hop_limit} (device setting applies)")

    # Try HTTP protobuf first — no CLI subprocess, no TCP contention,
    # no fromradio reads. This prevents "waiting for delivery" hangs on
    # the meshtasticd web client at :9443.
    try:
        from gateway.meshtastic_protobuf_client import send_text_direct

        if send_text_direct(
            text=text,
            destination=dest_num,
            channel_index=channel_index,
            want_ack=want_ack,
            hop_limit=hop_limit,
        ):
            is_dm = dest and dest != '!ffffffff'
            routing_note = (
                'DM: uses next-hop routing (flood then direct)' if is_dm
                else 'Broadcast: flooded to mesh'
            )
            return CommandResult.ok(
                message="Message sent via HTTP protobuf",
                data={
                    'hop_limit_intended': hop_limit,
                    'destination': dest,
                    'channel': channel_index,
                    'routing_note': routing_note,
                    'tx_method': 'http_protobuf',
                },
            )
    except Exception as e:
        logger.debug(f"HTTP protobuf TX unavailable, falling back to CLI: {e}")

    # Fallback: meshtastic CLI (creates TCP connection to 4403). RF egress is
    # guarded inside _run_command — the shared runner inspects the args for
    # transmitting flags, so this site needs no guard of its own.
    # Add destination for DMs
    if dest:
        args.extend(["--dest", dest])

    # Add message
    args.extend(["--sendtext", text])

    # Acknowledgment options
    if ack:
        # --ack blocks until ack received (can timeout)
        args.append("--ack")
    elif want_ack and dest:
        # For DMs, request ack but don't block
        # Note: meshtastic CLI --ack is blocking, so we skip for non-blocking
        pass

    result = _run_command(args)

    # Add routing info to result
    if result.success:
        result.data = result.data or {}
        result.data['hop_limit_intended'] = hop_limit
        result.data['destination'] = dest
        result.data['channel'] = channel_index
        result.data['routing_note'] = (
            'DM: uses next-hop routing (flood then direct)' if dest
            else 'Broadcast: flooded to mesh'
        )
        result.data['tx_method'] = 'cli'

    return result


def send_dm(
    text: str,
    dest: str,
    ack: bool = False,
    high_reliability: bool = False
) -> CommandResult:
    """
    Send a direct message with optimized settings.

    Args:
        text: Message text
        dest: Destination node ID (required)
        ack: Block until delivery confirmed
        high_reliability: Use max hops for difficult paths

    Returns:
        CommandResult with send status
    """
    if not dest or dest == '!ffffffff':
        return CommandResult.fail("DM requires a specific destination")

    hop_limit = HOP_LIMIT_EMERGENCY if high_reliability else HOP_LIMIT_DM_INITIAL

    return send_message(
        text=text,
        dest=dest,
        channel_index=0,  # DMs use channel 0
        ack=ack,
        hop_limit=hop_limit,
        want_ack=True
    )


def send_broadcast(
    text: str,
    channel_index: int = 1,
    hop_limit: int = HOP_LIMIT_BROADCAST
) -> CommandResult:
    """
    Send a broadcast message to a channel.

    Args:
        text: Message text
        channel_index: Channel number (1+ for public channels)
        hop_limit: Hop limit (default 3 to reduce congestion)

    Returns:
        CommandResult with send status
    """
    return send_message(
        text=text,
        dest=None,  # Broadcast
        channel_index=channel_index,
        ack=False,
        hop_limit=hop_limit,
        want_ack=False
    )


def request_telemetry(dest: str) -> CommandResult:
    """Request telemetry from a remote node."""
    return _run_command(["--request-telemetry", "--dest", dest])


def traceroute(dest: str) -> CommandResult:
    """Perform traceroute to a node."""
    return _run_command(["--traceroute", dest], timeout=120)


# Network Configuration

def configure_wifi(ssid: str, password: str, enable: bool = True) -> CommandResult:
    """
    Configure WiFi settings.

    Args:
        ssid: WiFi network name
        password: WiFi password
        enable: Enable WiFi after configuration
    """
    return _run_command([
        "--set", "network.wifi_ssid", ssid,
        "--set", "network.wifi_psk", password,
        "--set", "network.wifi_enabled", "1" if enable else "0"
    ])


def set_channel_name(channel_index: int, name: str) -> CommandResult:
    """Set channel name."""
    return _run_command([
        "--ch-index", str(channel_index),
        "--ch-set", "name", name,
        "--info"
    ])


def set_channel_psk(channel_index: int, psk: str) -> CommandResult:
    """
    Set channel PSK (encryption key).

    Args:
        channel_index: Channel index
        psk: PSK value (hex, "random", or "none")
    """
    return _run_command([
        "--ch-index", str(channel_index),
        "--ch-set", "psk", psk,
        "--info"
    ])


# Node Control

def set_owner(name: str, dest: Optional[str] = None) -> CommandResult:
    """
    Set node owner name (long name).

    Args:
        name: Owner name (max 40 chars)
        dest: Remote node ID (None for local node)
    """
    args = ["--set-owner", name]
    if dest:
        args = ["--dest", dest] + args
    return _run_command(args)


def set_owner_short(name: str, dest: Optional[str] = None) -> CommandResult:
    """
    Set node owner short name (4 characters).

    Args:
        name: Short name (max 4 chars, will be uppercase)
        dest: Remote node ID (None for local node)
    """
    # Ensure short name is max 4 chars and uppercase
    short_name = name[:4].upper()
    args = ["--set-owner-short", short_name]
    if dest:
        args = ["--dest", dest] + args
    return _run_command(args)


def reboot() -> CommandResult:
    """Reboot the node."""
    return _run_command(["--reboot"])


def shutdown() -> CommandResult:
    """Shutdown the node."""
    return _run_command(["--shutdown"])


def factory_reset() -> CommandResult:
    """Factory reset the node (dangerous!)."""
    return _run_command(["--factory-reset"])


def reset_nodedb() -> CommandResult:
    """Reset the node database."""
    return _run_command(["--reset-nodedb"])


# Hop Limit / Routing Configuration

def get_hop_limit() -> CommandResult:
    """
    Get the current device hop limit setting.

    Returns:
        CommandResult with data={'hop_limit': int}
    """
    result = _run_command(["--get", "lora.hop_limit"])
    if not result.success:
        return result

    # Parse output like "lora.hop_limit: 3"
    try:
        raw = result.raw or ''
        for line in raw.split('\n'):
            if 'hop_limit' in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    hop_limit = int(parts[-1].strip())
                    return CommandResult.ok(
                        f"Device hop limit: {hop_limit}",
                        data={'hop_limit': hop_limit}
                    )
    except (ValueError, IndexError) as e:
        logger.debug(f"Could not parse hop_limit: {e}")

    return CommandResult.ok("Hop limit retrieved", data={'hop_limit': 3, 'raw': result.raw})


def set_hop_limit(hop_limit: int) -> CommandResult:
    """
    Set the device hop limit.

    Args:
        hop_limit: Hop limit value (1-7)
            - 1-2: Short range, low traffic (urban dense)
            - 3-4: Medium range, balanced (default)
            - 5-7: Long range, high traffic (rural/SAR)

    Returns:
        CommandResult with status
    """
    if not 1 <= hop_limit <= 7:
        return CommandResult.fail(f"hop_limit must be 1-7, got {hop_limit}")

    result = _run_command(["--set", "lora.hop_limit", str(hop_limit)])
    if result.success:
        result.message = f"Device hop limit set to {hop_limit}"
        result.data = {'hop_limit': hop_limit}
    return result


def get_device_role() -> CommandResult:
    """
    Get the current device role.

    Returns:
        CommandResult with data={'role': str, 'description': str}
    """
    result = _run_command(["--get", "device.role"])
    if not result.success:
        return result

    # Parse and add description
    roles = {
        'CLIENT': 'Standard client, rebroadcasts messages',
        'CLIENT_MUTE': 'Silent client, no rebroadcast (saves battery)',
        'ROUTER': 'Infrastructure router, always rebroadcasts, hop_limit=7',
        'ROUTER_CLIENT': 'Router + local functions',
        'REPEATER': 'Dedicated repeater, minimal processing',
        'TRACKER': 'Location tracking device',
        'SENSOR': 'Sensor reporting device',
        'TAK': 'ATAK/TAK integration mode',
        'CLIENT_HIDDEN': 'Hidden from node list',
        'LOST_AND_FOUND': 'Recovery mode',
        'TAK_TRACKER': 'TAK + tracking',
        'ROUTER_LATE': 'Router that rebroadcasts only after other nodes had their chance',
        'CLIENT_BASE': 'Client base station, rebroadcasts favourited nodes',
    }

    try:
        raw = result.raw or ''
        for line in raw.split('\n'):
            if 'role' in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    role = _role_name(parts[-1].strip())
                    return CommandResult.ok(
                        f"Device role: {role}",
                        data={
                            'role': role,
                            'description': roles.get(role, 'Unknown role'),
                        }
                    )
    except Exception as e:
        logger.debug(f"Could not parse role: {e}")

    return CommandResult.ok("Role retrieved", data={'role': 'UNKNOWN', 'raw': result.raw})


def set_device_role(role: str) -> CommandResult:
    """
    Set the device role.

    Args:
        role: One of CLIENT, CLIENT_MUTE, ROUTER, ROUTER_CLIENT, REPEATER, etc.

    Returns:
        CommandResult with status

    Note: Role affects routing behavior:
        - CLIENT: Normal rebroadcast
        - CLIENT_MUTE: No rebroadcast (good for listeners)
        - ROUTER: Always rebroadcast, uses hop_limit=7
    """
    valid_roles = [
        'CLIENT', 'CLIENT_MUTE', 'ROUTER', 'ROUTER_CLIENT',
        'REPEATER', 'TRACKER', 'SENSOR', 'TAK', 'CLIENT_HIDDEN',
        'LOST_AND_FOUND', 'TAK_TRACKER'
    ]

    role = role.upper()
    if role not in valid_roles:
        return CommandResult.fail(
            f"Invalid role '{role}'. Valid: {', '.join(valid_roles)}"
        )

    return _run_command(["--set", "device.role", role])


def diagnose_messaging() -> CommandResult:
    """
    Diagnose messaging configuration and connection.

    Returns:
        CommandResult with comprehensive diagnostic data
    """
    diagnostics = {
        'connection': {},
        'device': {},
        'routing': {},
        'pubsub': {},
        'recommendations': [],
    }

    # Connection test
    conn_result = test_connection()
    diagnostics['connection']['status'] = 'ok' if conn_result.success else 'error'
    diagnostics['connection']['error'] = conn_result.error if not conn_result.success else None

    if not conn_result.success:
        diagnostics['recommendations'].append(
            "Cannot connect to meshtastic. Check: systemctl status meshtasticd"
        )
        return CommandResult.ok("Diagnostics complete (connection failed)", data=diagnostics)

    # Get device settings
    hop_result = get_hop_limit()
    if hop_result.success and hop_result.data:
        diagnostics['device']['hop_limit'] = hop_result.data.get('hop_limit')

    role_result = get_device_role()
    if role_result.success and role_result.data:
        diagnostics['device']['role'] = role_result.data.get('role')
        diagnostics['device']['role_description'] = role_result.data.get('description')

    # Analyze routing configuration
    hop_limit = diagnostics['device'].get('hop_limit', 3)
    role = diagnostics['device'].get('role', 'CLIENT')

    if role == 'CLIENT_MUTE':
        diagnostics['routing']['rebroadcast'] = False
        diagnostics['routing']['note'] = 'CLIENT_MUTE: Messages received but not rebroadcast'
    elif role == 'ROUTER':
        diagnostics['routing']['rebroadcast'] = True
        diagnostics['routing']['note'] = 'ROUTER: Always rebroadcasts, uses max hops'
    else:
        diagnostics['routing']['rebroadcast'] = True
        diagnostics['routing']['note'] = f'Standard routing with hop_limit={hop_limit}'

    # Recommendations
    if hop_limit < 3 and role not in ('ROUTER', 'ROUTER_CLIENT'):
        diagnostics['recommendations'].append(
            f"Low hop_limit ({hop_limit}) may limit message reach. Consider: --set lora.hop_limit 5"
        )

    if role == 'CLIENT_MUTE':
        diagnostics['recommendations'].append(
            "CLIENT_MUTE role prevents message rebroadcast. "
            "Good for listening, but won't help mesh. Consider CLIENT role."
        )

    # Check pubsub (for RX)
    from utils.message_listener import diagnose_pubsub
    diagnostics['pubsub'] = diagnose_pubsub()

    return CommandResult.ok(
        "Messaging diagnostics complete",
        data=diagnostics
    )


# Bluetooth

def ble_scan() -> CommandResult:
    """Scan for Bluetooth devices."""
    # BLE scan doesn't use connection args
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    try:
        result = subprocess.run(
            [cli_path, "--ble-scan"],
            capture_output=True,
            text=True,
            timeout=60
        )
        # Gate on returncode — meshtastic --ble-scan exits non-zero
        # when the BLE adapter is missing / hcitool can't open it /
        # bluez isn't loaded. Pre-2026-05-19 the wrapper reported
        # "BLE scan complete" regardless, leaving the caller to
        # interpret an empty `devices` field as "no nearby devices"
        # when in fact the scan never ran. Pattern-audit Finding #12.
        if result.returncode != 0:
            return CommandResult.fail(
                f"BLE scan failed: {result.stderr or result.stdout}",
                error=result.stderr or "ble_scan_failed",
                raw=result.stdout,
            )
        return CommandResult.ok(
            "BLE scan complete",
            data={'devices': result.stdout},
            raw=result.stdout
        )
    except KeyboardInterrupt:
        return CommandResult.fail("BLE scan aborted by user", error="interrupted")
    except Exception as e:
        return CommandResult.fail(f"BLE scan failed: {e}")


# Utility

def is_available() -> bool:
    """Check if meshtastic CLI is available."""
    return _find_cli() is not None


def get_cli_path() -> Optional[str]:
    """Get the path to the meshtastic CLI."""
    return _find_cli()


def get_cli_help() -> CommandResult:
    """Get meshtastic CLI help."""
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    try:
        result = subprocess.run(
            [cli_path, "-h"],
            capture_output=True,
            text=True,
            timeout=15
        )
        return CommandResult.ok(
            "Help retrieved",
            raw=result.stdout
        )
    except Exception as e:
        return CommandResult.fail(f"Failed to get help: {e}")


def _role_name(raw: str) -> str:
    """The CLI's `--get device.role` prints the enum NUMBER ("1"), which read
    "Unknown role" on every box — and with it the messaging diagnose screen
    claimed a CLIENT_MUTE radio rebroadcasts (2026-09-25). Map numbers through
    Meshtastic's own protobuf enum so the names cannot drift from firmware."""
    raw = raw.strip()
    if raw.isdigit():
        try:
            from meshtastic.protobuf import config_pb2
            return config_pb2.Config.DeviceConfig.Role.Name(int(raw))
        except (ImportError, ValueError):
            return f"UNKNOWN({raw})"
    return raw.upper()

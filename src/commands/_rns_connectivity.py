"""RNS connectivity checks and node discovery.

Extracted from commands/rns.py for file size compliance (CLAUDE.md #6).

Provides:
  check_connectivity()        — Check RNS network connectivity
  test_path()                 — Test path to an RNS destination
  get_path_info()             — Get detailed path info for a destination
  _init_rns_client()          — Initialize RNS as client to running rnsd
  list_known_destinations()   — List known RNS destinations
  discover_nodes()            — Actively discover RNS nodes
"""

import logging
import re
import subprocess
import time
from pathlib import Path

from commands.base import CommandResult
from utils.paths import ReticulumPaths

logger = logging.getLogger(__name__)


def _get_rns():
    """Get RNS and _HAS_RNS from parent module for test-patch compatibility.

    Tests patch commands.rns.RNS and commands.rns._HAS_RNS, so we must read
    from there at call time rather than caching our own module-level copy.
    """
    from commands import rns as _parent
    return _parent.RNS, _parent._HAS_RNS


# ============================================================================
# CONNECTIVITY & DIAGNOSTICS
# ============================================================================

def check_connectivity() -> CommandResult:
    """
    Check RNS network connectivity.

    Returns:
        CommandResult with connectivity status
    """
    RNS, _HAS_RNS = _get_rns()
    # Lazy imports to avoid circular dependency with commands.rns
    from commands.rns import (
        get_status, read_config, validate_config,
        _parse_share_instance, get_identity_path,
    )

    connectivity = {
        'rnsd_running': False,
        'can_import_rns': False,
        'config_valid': False,
        'interfaces_enabled': 0,
        'issues': [],
        'warnings': [],
    }

    # Check rnsd
    status = get_status()
    connectivity['rnsd_running'] = status.data.get('rnsd_running', False)
    if not connectivity['rnsd_running']:
        # Check if NomadNet is holding the port (common conflict)
        nomadnet_running = False
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            nomadnet_running = result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass

        if nomadnet_running:
            connectivity['issues'].append(
                "rnsd not running — NomadNet is holding port 37428 (shared instance conflict)"
            )
        else:
            connectivity['issues'].append("rnsd daemon not running")

    # Check RNS import
    if not _HAS_RNS:
        connectivity['issues'].append("RNS Python module not installed")
    else:
        connectivity['can_import_rns'] = True
        connectivity['rns_version'] = RNS.__version__ if hasattr(RNS, '__version__') else 'unknown'

    # Check config
    config_result = read_config()
    if config_result.success:
        content = config_result.data.get('content', '')
        valid, errors = validate_config(content)
        connectivity['config_valid'] = valid
        if not valid:
            connectivity['issues'].extend(errors)

        # Count enabled interfaces
        for iface in config_result.data.get('interfaces', []):
            if iface.get('settings', {}).get('enabled', 'yes') == 'yes':
                connectivity['interfaces_enabled'] += 1

        if connectivity['interfaces_enabled'] == 0:
            connectivity['issues'].append("No interfaces enabled")

        # Check share_instance setting (required for gateway to connect)
        share_instance = _parse_share_instance(content)
        connectivity['share_instance'] = share_instance
        if not share_instance:
            connectivity['warnings'].append(
                "share_instance not enabled in [reticulum] config — "
                "gateway and other RNS clients cannot connect to rnsd"
            )
    else:
        connectivity['issues'].append(f"Config error: {config_result.message}")

    # Check identities (warnings, not blocking issues)
    config_dir = ReticulumPaths.get_config_dir()
    rns_identity = config_dir / 'identity'
    gw_identity = get_identity_path()
    if not rns_identity.exists():
        connectivity['warnings'].append("RNS identity not created")
    if not gw_identity.exists():
        connectivity['warnings'].append("Gateway identity not created")

    # Overall status
    is_ok = (
        connectivity['rnsd_running'] and
        connectivity['can_import_rns'] and
        connectivity['config_valid'] and
        connectivity['interfaces_enabled'] > 0
    )

    if is_ok:
        return CommandResult.ok(
            f"RNS connectivity OK ({connectivity['interfaces_enabled']} interfaces)",
            data=connectivity
        )
    else:
        return CommandResult.fail(
            f"RNS issues: {len(connectivity['issues'])}",
            data=connectivity
        )


def test_path(destination_hash: str, timeout: int = 10) -> CommandResult:
    """
    Test path to an RNS destination.

    Args:
        destination_hash: Hex string of destination hash
        timeout: Timeout in seconds

    Returns:
        CommandResult with path status
    """
    RNS, _HAS_RNS = _get_rns()
    # Note: Validate hash format before attempting RNS import to give
    # better error messages when RNS has cryptography issues
    if not re.match(r'^[0-9a-fA-F]{32}$', destination_hash):
        return CommandResult.fail(
            "Invalid hash format",
            error="Hash must be 32 hex characters"
        )

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        dest_bytes = bytes.fromhex(destination_hash)

        # Check if path exists
        has_path = RNS.Transport.has_path(dest_bytes)

        if has_path:
            return CommandResult.ok(
                "Path exists",
                data={
                    'destination': destination_hash,
                    'has_path': True
                }
            )

        # Request path
        RNS.Transport.request_path(dest_bytes)

        # Wait for path
        start = time.time()
        while time.time() - start < timeout:
            if RNS.Transport.has_path(dest_bytes):
                return CommandResult.ok(
                    f"Path discovered in {time.time() - start:.1f}s",
                    data={
                        'destination': destination_hash,
                        'has_path': True,
                        'discovery_time': time.time() - start
                    }
                )
            time.sleep(0.1)

        return CommandResult.fail(
            f"No path found within {timeout}s",
            data={
                'destination': destination_hash,
                'has_path': False,
                'timeout': timeout
            }
        )

    except Exception as e:
        # Catch pyo3 PanicException and other RNS errors
        return CommandResult.fail(f"Path test failed: {e}")


def get_path_info(destination_hash: str) -> CommandResult:
    """
    Get detailed path information for an RNS destination.

    Queries the running RNS instance for path metrics including
    hop count, next hop, and interface used.

    Args:
        destination_hash: Hex string of destination hash (32 hex chars)

    Returns:
        CommandResult with path details (hops, next_hop, interface, etc.)
    """
    RNS, _HAS_RNS = _get_rns()
    if not re.match(r'^[0-9a-fA-F]{32}$', destination_hash):
        return CommandResult.fail(
            "Invalid hash format",
            error="Hash must be 32 hex characters"
        )

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        dest_bytes = bytes.fromhex(destination_hash)
        has_path = RNS.Transport.has_path(dest_bytes)

        if not has_path:
            return CommandResult.fail(
                "No path known",
                data={
                    'destination': destination_hash,
                    'has_path': False,
                    'note': 'Use rnprobe or test_path() to discover paths'
                }
            )

        info = {
            'destination': destination_hash,
            'has_path': True,
            'hops': None,
            'next_hop': None,
            'expires': None,
            'interface': None,
        }

        # Query path table for detailed info
        if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
            path_entry = RNS.Transport.path_table.get(dest_bytes)
            if path_entry and isinstance(path_entry, (list, tuple)):
                # Path table entry format: (timestamp, next_hop, interface, hops, expires, ...)
                # Format may vary by RNS version
                if len(path_entry) > 0:
                    info['timestamp'] = path_entry[0] if isinstance(path_entry[0], (int, float)) else None
                if len(path_entry) > 1:
                    next_hop = path_entry[1]
                    if isinstance(next_hop, bytes):
                        info['next_hop'] = next_hop.hex()
                if len(path_entry) > 2:
                    iface = path_entry[2]
                    if hasattr(iface, 'name'):
                        info['interface'] = iface.name
                    elif isinstance(iface, str):
                        info['interface'] = iface
                if len(path_entry) > 3:
                    if isinstance(path_entry[3], int):
                        info['hops'] = path_entry[3]
                if len(path_entry) > 4:
                    if isinstance(path_entry[4], (int, float)):
                        info['expires'] = path_entry[4]

        # Check if identity is known
        if hasattr(RNS.Identity, 'recall') and callable(RNS.Identity.recall):
            try:
                identity = RNS.Identity.recall(dest_bytes)
                info['identity_known'] = identity is not None
            except Exception:
                info['identity_known'] = False

        return CommandResult.ok(
            f"Path info for {destination_hash[:8]}...",
            data=info
        )

    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        return CommandResult.fail(f"Path info failed: {e}")


# ============================================================================
# RNS CLIENT INITIALIZATION
# ============================================================================

def _init_rns_client():
    """Initialize RNS as a client connecting to the running rnsd instance.

    Creates a client-only config with no interfaces to avoid
    "Address already in use" errors when rnsd already owns the ports.
    See: .claude/foundations/persistent_issues.md Issue #12
    """
    RNS, _HAS_RNS = _get_rns()
    import tempfile

    client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
    client_config_dir.mkdir(exist_ok=True)
    client_config_file = client_config_dir / "config"

    client_config_file.write_text(
        "# MeshForge RNS Client Config (auto-generated)\n"
        "# Connects to existing rnsd without creating interfaces\n\n"
        "[reticulum]\n"
        "share_instance = Yes\n"
        "shared_instance_port = 37428\n"
        "instance_control_port = 37429\n"
    )

    return RNS.Reticulum(configdir=str(client_config_dir))


# ============================================================================
# RNS NODE DISCOVERY
# ============================================================================

def list_known_destinations() -> CommandResult:
    """
    List known RNS destinations from the running rnsd instance.

    This queries the rnsd daemon for all destinations it has heard about
    via announces or path requests.

    Returns:
        CommandResult with list of known destinations
    """
    RNS, _HAS_RNS = _get_rns()
    # Lazy import to avoid circular dependency
    from commands.rns import get_status

    # First check if rnsd is running (using improved detection)
    status = get_status()
    if not status.data.get('rnsd_running'):
        # Also check shared instance as fallback (domain socket, TCP, or UDP)
        try:
            from utils.service_check import check_rns_shared_instance
            if not check_rns_shared_instance():
                return CommandResult.fail(
                    "rnsd not running",
                    fix_hint="Start with: rnsd or sudo systemctl start rnsd"
                )
            # Shared instance is reachable, continue
        except ImportError:
            pass  # No service_check available, proceed anyway
        except Exception as e:
            logger.debug(f"RNS availability check error: {e}")

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        # Connect as client to avoid "Address already in use" when rnsd owns ports
        reticulum = _init_rns_client()

        nodes = []

        # Method 1: Check Transport path table
        if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
            for dest_hash, path_data in RNS.Transport.path_table.items():
                try:
                    hash_hex = dest_hash.hex() if isinstance(dest_hash, bytes) else str(dest_hash)
                    # Path data format varies by RNS version
                    hops = 0
                    if isinstance(path_data, tuple) and len(path_data) > 1:
                        hops = path_data[1] if isinstance(path_data[1], int) else 0

                    nodes.append({
                        'hash': hash_hex,
                        'short_hash': hash_hex[:8],
                        'hops': hops,
                        'source': 'path_table'
                    })
                except Exception as e:
                    logger.debug(f"Error parsing path entry: {e}")

        # Method 2: Check known destinations
        if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
            known_dests = RNS.Identity.known_destinations
            if isinstance(known_dests, dict):
                for dest_hash, identity in known_dests.items():
                    try:
                        hash_hex = dest_hash.hex() if isinstance(dest_hash, bytes) else str(dest_hash)
                        # Check if already added from path_table
                        if not any(n['hash'] == hash_hex for n in nodes):
                            nodes.append({
                                'hash': hash_hex,
                                'short_hash': hash_hex[:8],
                                'hops': -1,  # Unknown
                                'source': 'known_destinations'
                            })
                    except Exception as e:
                        logger.debug(f"Error parsing known destination: {e}")

        # Method 3: Check destination table
        if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
            for dest in RNS.Transport.destinations:
                try:
                    if hasattr(dest, 'hash'):
                        hash_hex = dest.hash.hex() if isinstance(dest.hash, bytes) else str(dest.hash)
                        if not any(n['hash'] == hash_hex for n in nodes):
                            name = dest.name if hasattr(dest, 'name') else ''
                            nodes.append({
                                'hash': hash_hex,
                                'short_hash': hash_hex[:8],
                                'name': name,
                                'hops': -1,
                                'source': 'destinations'
                            })
                except Exception as e:
                    logger.debug(f"Error parsing destination: {e}")

        if nodes:
            return CommandResult.ok(
                f"Found {len(nodes)} RNS destinations",
                data={
                    'nodes': nodes,
                    'count': len(nodes)
                }
            )
        else:
            return CommandResult.ok(
                "No known RNS destinations",
                data={
                    'nodes': [],
                    'count': 0,
                    'note': "Nodes appear when they announce or when you request paths"
                }
            )

    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        return CommandResult.fail(
            f"Failed to query RNS: {e}",
            error=str(e)
        )


def discover_nodes(timeout: int = 30) -> CommandResult:
    """
    Actively discover RNS nodes on the network.

    This sends out path requests and waits for announces to discover
    new nodes on the network.

    Args:
        timeout: How long to wait for discoveries (seconds)

    Returns:
        CommandResult with discovered nodes
    """
    RNS, _HAS_RNS = _get_rns()
    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        # Connect as client to avoid "Address already in use" when rnsd owns ports
        reticulum = _init_rns_client()

        initial_count = 0
        if hasattr(RNS.Identity, 'known_destinations'):
            initial_count = len(RNS.Identity.known_destinations or {})

        discovered = []

        # Set up a simple announce handler to catch new nodes
        class DiscoveryHandler:
            def __init__(self):
                self.aspect_filter = None  # All aspects
                self.nodes = []

            def received_announce(self, dest_hash, announced_identity, app_data):
                try:
                    hash_hex = dest_hash.hex()
                    name = ""
                    if app_data:
                        try:
                            name = app_data.decode('utf-8', errors='ignore').strip()
                            name = ''.join(c for c in name if c.isprintable())
                        except Exception as e:
                            logger.debug(f"Failed to decode announce app_data: {e}")

                    self.nodes.append({
                        'hash': hash_hex,
                        'short_hash': hash_hex[:8],
                        'name': name,
                        'source': 'announce'
                    })
                except Exception as e:
                    logger.debug(f"Error processing announce: {e}")

        handler = DiscoveryHandler()
        RNS.Transport.register_announce_handler(handler)

        logger.info(f"Listening for RNS announces for {timeout} seconds...")

        # Wait for timeout
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.5)

            # Check for new discoveries
            if handler.nodes:
                for node in handler.nodes:
                    if not any(d['hash'] == node['hash'] for d in discovered):
                        discovered.append(node)
                        logger.info(f"Discovered: {node['short_hash']} ({node.get('name', 'unnamed')})")
                handler.nodes = []

        if discovered:
            return CommandResult.ok(
                f"Discovered {len(discovered)} nodes",
                data={
                    'nodes': discovered,
                    'count': len(discovered),
                    'duration': timeout
                }
            )
        else:
            return CommandResult.ok(
                "No new nodes discovered",
                data={
                    'nodes': [],
                    'count': 0,
                    'duration': timeout,
                    'note': "Try longer timeout or check if other RNS nodes are announcing"
                }
            )

    except Exception as e:
        return CommandResult.fail(f"Discovery failed: {e}")

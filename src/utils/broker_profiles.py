"""
MeshForge MQTT Broker Profiles

Provides broker configuration templates following Meshtastic MQTT conventions:
- MeshForge Private Broker (localhost mosquitto, custom PSK)
- Meshtastic Public Broker (mqtt.meshtastic.org)
- Custom Broker (user-defined)

Also generates mosquitto.conf files for the private broker mode, enabling
MeshForge to act as its own MQTT broker for bridging:

    Meshtastic LF -> Private Broker -> RNS Gateway -> Meshtastic ST

Topic structure follows Meshtastic conventions:
    msh/{region}/2/e/{channel}/!{node_id}   (encrypted protobuf)
    msh/{region}/2/json/{channel}/!{node_id} (JSON, if enabled)

See: https://meshtastic.org/docs/software/integrations/mqtt/
"""

import json
import logging
import os
import secrets
import stat
import string
import subprocess
from dataclasses import dataclass, field, asdict, replace
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from utils.mqtt_defaults import (
    MESHTASTIC_PUBLIC_BROKER,
    MESHTASTIC_PUBLIC_KEY,
    MESHTASTIC_PUBLIC_PASSWORD,
    MESHTASTIC_PUBLIC_PORT,
    MESHTASTIC_PUBLIC_USERNAME,
)

from utils.paths import get_real_user_home, atomic_write_text, chown_to_operator
from utils.service_check import check_service as _check_service, apply_config_and_restart as _apply_config_and_restart, enable_service as _enable_service

logger = logging.getLogger(__name__)


class BrokerType(Enum):
    """Broker profile types."""
    PRIVATE = "private"    # MeshForge-managed mosquitto
    PUBLIC = "public"      # mqtt.meshtastic.org
    CUSTOM = "custom"      # User-defined broker


@dataclass
class BrokerProfile:
    """MQTT broker configuration profile.

    Follows Meshtastic MQTT conventions for topic structure,
    authentication, and encryption settings.
    """
    name: str
    broker_type: str  # BrokerType value as string for JSON serialization
    host: str
    port: int
    username: str = ""
    password: str = ""
    use_tls: bool = False
    root_topic: str = "msh/US/2/e"
    channel: str = "LongFast"
    encryption_key: str = "AQ=="  # Default Meshtastic key (NOT recommended for private)
    region: str = "US"
    json_enabled: bool = True
    uplink_enabled: bool = True
    downlink_enabled: bool = True
    # Private broker specific
    allow_anonymous: bool = False
    acl_enabled: bool = True
    # Metadata
    description: str = ""
    is_active: bool = False

    @property
    def topic_filter(self) -> str:
        """Build MQTT topic filter string for subscription."""
        return f"{self.root_topic}/{self.channel}/#"

    @property
    def json_topic_filter(self) -> str:
        """Build JSON topic filter (parallel to encrypted topic)."""
        json_root = self.root_topic.replace("/e", "/json")
        return f"{json_root}/{self.channel}/#"

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        if self.broker_type == BrokerType.PRIVATE.value:
            return f"Private: {self.name}"
        elif self.broker_type == BrokerType.PUBLIC.value:
            return f"Public: {self.host}"
        return f"Custom: {self.name} ({self.host})"

    def to_mqtt_config(self) -> Dict[str, Any]:
        """Convert to MQTTNodelessSubscriber config format."""
        return {
            "broker": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "root_topic": self.root_topic,
            "channel": self.channel,
            "key": self.encryption_key,
            "use_tls": self.use_tls,
            "auto_reconnect": True,
            "reconnect_delay": 2 if self.host in ("localhost", "127.0.0.1") else 5,
            "max_reconnect_delay": 30 if self.host in ("localhost", "127.0.0.1") else 60,
        }

    def to_intent(self) -> Dict[str, Any]:
        """The saved-settings INTENT (monitoring.mqtt_settings) for this
        profile — a direct field map, so nothing is parsed back out of a
        topic string. The JSON leg is derived by the owner (a local broker
        listens on every channel). TLS reads "auto" when it matches the port."""
        return {
            "broker": self.host,
            "port": self.port,
            "tls": "auto" if self.use_tls == (self.port == 8883) else self.use_tls,
            "username": self.username or "",
            "password": self.password or "",
            "root_topic": self.root_topic,
            "channel": self.channel,
            "key": self.encryption_key or "AQ==",
        }

    def to_meshtastic_cli_args(self) -> List[str]:
        """Generate meshtastic CLI args to configure the radio's MQTT module.

        Returns list of --set arguments for the meshtastic CLI.
        """
        args = [
            "--set", "mqtt.enabled", "true",
            "--set", "mqtt.address", self.host,
        ]
        if self.username:
            args.extend(["--set", "mqtt.username", self.username])
        if self.password:
            args.extend(["--set", "mqtt.password", self.password])
        if self.root_topic != "msh":
            args.extend(["--set", "mqtt.root", self.root_topic.split("/")[0]])
        args.extend([
            "--set", "mqtt.encryption_enabled", "true",
            "--set", "mqtt.json_enabled", str(self.json_enabled).lower(),
            "--set", "mqtt.tls_enabled", str(self.use_tls).lower(),
        ])
        return args


def generate_password(length: int = 16) -> str:
    """Generate a secure random password for MQTT authentication."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# =============================================================================
# Profile Factory Functions
# =============================================================================

def create_private_profile(
    name: str = "meshforge",
    channel: str = "LongFast",
    region: str = "US",
    username: str = "meshforge",
    password: str = "",
) -> BrokerProfile:
    """Create a MeshForge private broker profile.

    This is the recommended setup for bridging Meshtastic <-> RNS.
    Runs mosquitto locally with authentication and proper ACLs.

    IMPORTANT: Do NOT use the default Meshtastic key (AQ==) on private brokers.
    This generates a random password if none is provided.

    Args:
        name: Profile name
        channel: Meshtastic channel name
        region: Region code (US, EU_868, etc.)
        username: MQTT username
        password: MQTT password (generated if empty)
    """
    if not password:
        password = generate_password()

    return BrokerProfile(
        name=name,
        broker_type=BrokerType.PRIVATE.value,
        host="localhost",
        port=1883,
        username=username,
        password=password,
        use_tls=False,  # Localhost doesn't need TLS
        root_topic=f"msh/{region}/2/e",
        channel=channel,
        encryption_key="AQ==",  # Meshtastic default - radio handles encryption
        region=region,
        json_enabled=True,
        uplink_enabled=True,
        downlink_enabled=True,
        allow_anonymous=False,
        acl_enabled=True,
        description=(
            "MeshForge private broker (localhost mosquitto). "
            "Authenticated access, ACL-controlled topics. "
            "Ideal for Meshtastic <-> RNS gateway bridging."
        ),
    )


def create_public_profile(
    region: str = "US",
    channel: str = "LongFast",
) -> BrokerProfile:
    """Create a Meshtastic public broker profile.

    Connects to mqtt.meshtastic.org for nodeless monitoring.
    Uses default Meshtastic credentials and encryption key.

    Note: Public broker enforces zero-hop policy (messages don't
    propagate further into the mesh from MQTT downlink).
    """
    return BrokerProfile(
        name="Meshtastic Public",
        broker_type=BrokerType.PUBLIC.value,
        host=MESHTASTIC_PUBLIC_BROKER,
        port=MESHTASTIC_PUBLIC_PORT,
        username=MESHTASTIC_PUBLIC_USERNAME,
        password=MESHTASTIC_PUBLIC_PASSWORD,
        use_tls=True,
        root_topic=f"msh/{region}/2/e",
        channel=channel,
        encryption_key=MESHTASTIC_PUBLIC_KEY,
        region=region,
        json_enabled=False,  # Public broker mainly uses encrypted protobuf
        uplink_enabled=False,  # Read-only monitoring
        downlink_enabled=False,
        allow_anonymous=False,
        acl_enabled=False,
        description=(
            "Meshtastic public broker (mqtt.meshtastic.org). "
            "Nodeless monitoring - observe the mesh without local radio. "
            "Read-only, zero-hop policy enforced."
        ),
    )


def create_custom_profile(
    name: str,
    host: str,
    port: int = 1883,
    username: str = "",
    password: str = "",
    use_tls: bool = False,
    root_topic: str = "msh/US/2/e",
    channel: str = "LongFast",
    region: str = "US",
) -> BrokerProfile:
    """Create a custom broker profile.

    For users running their own MQTT infrastructure or connecting
    to community/regional brokers.
    """
    return BrokerProfile(
        name=name,
        broker_type=BrokerType.CUSTOM.value,
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        root_topic=root_topic,
        channel=channel,
        encryption_key="AQ==",
        region=region,
        json_enabled=True,
        uplink_enabled=True,
        downlink_enabled=True,
        allow_anonymous=not username,
        acl_enabled=False,
        description=f"Custom broker at {host}:{port}",
    )


# =============================================================================
# Mosquitto Configuration Generator
# =============================================================================

MOSQUITTO_CONF_TEMPLATE = """\
# MeshForge Private MQTT Broker Configuration
# Generated by MeshForge broker profile manager
#
# Meshtastic MQTT topic convention:
#   msh/{{region}}/2/e/{{channel}}/!{{node_id}}   (encrypted protobuf)
#   msh/{{region}}/2/json/{{channel}}/!{{node_id}} (JSON)
#
# See: https://meshtastic.org/docs/software/integrations/mqtt/

# =============================================================================
# Listeners
# =============================================================================

# Plain TCP listener for local Meshtastic devices and MeshForge
listener {port}
protocol mqtt

# Bind to all interfaces (change to 127.0.0.1 for local-only)
# For LAN gateway nodes, use 0.0.0.0
bind_address {bind_address}

# =============================================================================
# Authentication
# =============================================================================

# Require username/password (recommended for private brokers)
allow_anonymous {allow_anonymous}

# Password file (generated with mosquitto_passwd)
{password_file_line}

# =============================================================================
# Access Control
# =============================================================================

# ACL file for topic-level permissions
{acl_file_line}

# =============================================================================
# Persistence
# =============================================================================

persistence true
persistence_location /var/lib/mosquitto/

# =============================================================================
# Logging
# =============================================================================

log_dest syslog
log_type warning
log_type error
log_type notice

# =============================================================================
# Connection limits
# =============================================================================

# Max connections (adjust based on mesh size)
max_connections 100

# Keepalive timeout (seconds)
max_keepalive 120

# Max queued messages per client
max_queued_messages 1000

# Max message size (Meshtastic packets are small, 256 bytes typical)
message_size_limit 4096
"""

MOSQUITTO_ACL_TEMPLATE = """\
# MeshForge MQTT Broker ACL
# Generated by MeshForge broker profile manager
#
# Controls which users can read/write which topics.
# Topic pattern: msh/{{region}}/2/e/{{channel}}/!{{node_id}}

# =============================================================================
# MeshForge user - full access to mesh topics
# =============================================================================
user {username}
topic readwrite msh/#
topic readwrite meshforge/#
topic read $SYS/#

# =============================================================================
# Meshtastic gateway nodes - access to their channel topics
# =============================================================================
# Add gateway node users here:
# user gateway1
# topic readwrite msh/{region}/2/e/{channel}/#
# topic readwrite msh/{region}/2/json/{channel}/#

# =============================================================================
# Read-only monitoring users
# =============================================================================
# user monitor
# topic read msh/#
# topic read $SYS/#
"""


def generate_mosquitto_conf(profile: BrokerProfile) -> str:
    """Generate mosquitto.conf content for a private broker profile.

    Args:
        profile: BrokerProfile with private broker settings

    Returns:
        String content for mosquitto.conf
    """
    allow_anon = "true" if profile.allow_anonymous else "false"

    if profile.allow_anonymous:
        password_file_line = "# password_file not needed (anonymous access)"
        acl_file_line = "# acl_file not needed (anonymous access)"
    else:
        password_file_line = "password_file /etc/mosquitto/meshforge_passwd"
        acl_file_line = "acl_file /etc/mosquitto/meshforge_acl"

    # Mosquitto broker binds to all interfaces — intentional for gateway nodes
    # that need to accept MQTT connections from mesh radios on the LAN.
    # Change to "127.0.0.1" if only local clients connect.
    bind_address = "0.0.0.0"

    return MOSQUITTO_CONF_TEMPLATE.format(
        port=profile.port,
        bind_address=bind_address,
        allow_anonymous=allow_anon,
        password_file_line=password_file_line,
        acl_file_line=acl_file_line,
    )


def generate_mosquitto_acl(profile: BrokerProfile) -> str:
    """Generate ACL file content for a private broker profile.

    Args:
        profile: BrokerProfile with private broker settings

    Returns:
        String content for mosquitto ACL file
    """
    return MOSQUITTO_ACL_TEMPLATE.format(
        username=profile.username or "meshforge",
        region=profile.region,
        channel=profile.channel,
    )


# =============================================================================
# Profile Storage
# =============================================================================

PROFILES_FILENAME = "broker_profiles.json"


def _get_profiles_path() -> Path:
    """Get path to broker profiles config file."""
    return get_real_user_home() / ".config" / "meshforge" / PROFILES_FILENAME


@dataclass
class ProfileStoreStatus:
    """What the profile store on disk IS — never inferred from an empty dict.

    state: ``absent`` (no file — first run), ``ok`` (parsed), ``unreadable``
    (bad JSON / wrong shape / OSError: every save REFUSES until Reset),
    ``denied`` (permission — the file may be HEALTHY: saves refuse and Reset
    is never offered; fix ownership instead — F8 v2 r8 F6).
    skipped: profiles present on disk that did not parse (a newer version's
    field, a missing field) — carried through saves untouched (F8 v2 r7).
    active: the store's own ``active_profile`` name, parsed or not (r8 F10).
    """
    state: str
    detail: str = ""
    skipped: List[str] = field(default_factory=list)
    active: Optional[str] = None


_STORE_KEYS = ("profiles", "active_profile")


def _read_store(path: Path) -> Tuple[ProfileStoreStatus, Dict[str, Any]]:
    """Read the raw store. Returns (status, raw profile dicts by name)."""
    status, raw, _ = _read_store_full(path)
    return status, raw


def _read_store_full(path: Path) -> Tuple[ProfileStoreStatus, Dict[str, Any], Dict[str, Any]]:
    """(status, raw profile dicts by name, unknown top-level keys)."""
    try:
        # r9 #4: inside the try — exists() RAISES on an untraversable dir
        if not path.exists():
            return ProfileStoreStatus("absent"), {}, {}
        with open(path) as f:
            data = json.load(f)
    except PermissionError as e:
        try:
            st = path.stat()
            import pwd
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            who = f"owner {owner}, mode {st.st_mode & 0o777:o}"
        except OSError:
            who = "owner unknown"
        return ProfileStoreStatus("denied", f"permission denied ({who})"), {}, {}
    except (ValueError, OSError) as e:   # JSONDecodeError/UnicodeDecodeError ⊂ ValueError
        return ProfileStoreStatus("unreadable", f"{type(e).__name__}: {e}"), {}, {}
    # r10 F4: an object with NO "profiles" key is not a profile store — it
    # read as an ok EMPTY one and was overwritten (every writer since the
    # first commit wrote the key, so its absence is not "no profiles")
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        return ProfileStoreStatus(
            "unreadable", "not a profile store (wrong JSON shape)"), {}, {}
    active = data.get("active_profile")
    extras = {k: v for k, v in data.items() if k not in _STORE_KEYS}
    return (ProfileStoreStatus("ok", active=active if isinstance(active, str) else None),
            dict(data["profiles"]), extras)


def _parse_raw(raw: Dict[str, Any]) -> Tuple[Dict[str, BrokerProfile], List[str]]:
    profiles, skipped = {}, []
    for name, pdata in raw.items():
        try:
            profiles[name] = BrokerProfile(**pdata)
        except TypeError as e:
            logger.warning("Skipping malformed profile '%s' (kept on disk): %s", name, e)
            skipped.append(name)
    return profiles, skipped


def profile_store_status() -> ProfileStoreStatus:
    """The store's state, re-read now (the TUI shows it; saves obey it)."""
    status, raw = _read_store(_get_profiles_path())
    if status.state == "ok":
        status.skipped = _parse_raw(raw)[1]
    return status


def load_profiles() -> Dict[str, BrokerProfile]:
    """Load broker profiles from config file.

    An unreadable store loads as ``{}`` so callers never crash — but that
    ``{}`` is NOT "no profiles": ``save_profiles`` refuses over it and
    ``profile_store_status()`` says ``unreadable`` (hfm #1, F8 v2 r7).

    Returns:
        Dict mapping profile name to BrokerProfile
    """
    path = _get_profiles_path()
    status, raw = _read_store(path)
    if status.state != "absent":
        _tighten_mode(path)      # r8 F7: an unreadable store holds passwords too
        chown_to_operator(path)  # r8 F6: Admin-mode open hands a root-owned store back
    if status.state in ("unreadable", "denied"):
        logger.error("Broker profile store %s (%s) — saves refused", status.state,
                     status.detail)
        return {}
    return _parse_raw(raw)[0]


def _tighten_mode(path: Path) -> None:
    """The store holds passwords: a pre-r7 store was written at umask mode
    (0664 measured live 2026-11-03). Tighten to 0600 on read — idempotent."""
    try:
        st = path.stat()
        mode = st.st_mode & 0o777
        want = 0o700 if stat.S_ISDIR(st.st_mode) else 0o600   # r9 #7: a dir stays enterable
        if mode & 0o077:
            os.chmod(path, want)
            logger.warning("Broker profile store was mode %o (holds passwords) "
                           "— tightened to %o: %s", mode, want, path)
    except OSError as e:
        logger.warning("Could not tighten broker profile store mode: %s", e)


def save_profiles(profiles: Dict[str, BrokerProfile]) -> bool:
    """Save broker profiles to config file.

    Refuses (returns False, file untouched) when the store on disk is
    unreadable — a save over a failed read destroyed every profile
    (pre-existing ``93727ab6``; F8 v2 r7). Profiles on disk that never parsed
    are carried through byte-for-byte: the operator never saw them, so
    their absence here is not a deletion. Written fsync'd, unique temp, 0600
    (the store holds passwords).

    Args:
        profiles: Dict mapping profile name to BrokerProfile

    Returns:
        True if saved successfully
    """
    profiles_path = _get_profiles_path()
    status, raw, extras = _read_store_full(profiles_path)
    if status.state in ("unreadable", "denied"):
        logger.error("Refusing to save broker profiles over a store that is "
                     "%s (%s): %s", status.state, status.detail, profiles_path)
        return False
    _, skipped = _parse_raw(raw)
    any_active = any(p.is_active for p in profiles.values())
    carried = {}
    for name in skipped:
        if name in profiles:
            continue                      # the operator replaced it by name
        entry = raw[name]
        if any_active and isinstance(entry, dict) and entry.get("is_active"):
            entry = {**entry, "is_active": False}
        carried[name] = entry

    data = {
        **extras,      # r10 F3: a newer version's top-level keys survive our save
        "profiles": {**{name: asdict(p) for name, p in profiles.items()}, **carried},
        # r9 #2: over loaded AND carried — an unloaded active profile stays active
        "active_profile": next(
            (name for name, p in profiles.items() if p.is_active),
            next((name for name, e in carried.items()
                  if isinstance(e, dict) and e.get("is_active")), None),
        ),
    }
    try:
        # r9 #6: write THROUGH a symlinked store — replacing the link would
        # leave its target holding the old bytes (and old passwords)
        target = profiles_path.resolve() if profiles_path.is_symlink() else profiles_path
        atomic_write_text(target, json.dumps(data, indent=2))
        chown_to_operator(target)   # r8 F6b: a sudo save stays the operator's
        return True
    except OSError as e:
        logger.error("Error saving broker profiles: %s", e)
        return False


def reset_unreadable_profile_store() -> Optional[Path]:
    """Move an UNREADABLE store aside as ``<name>.unreadable.<n>`` (never
    deleted) so setup can start fresh. Returns the backup path, or None when
    the store is not unreadable (a readable store is never reset)."""
    path = _get_profiles_path()
    if _read_store(path)[0].state != "unreadable":
        return None
    n = 1
    while (backup := path.with_name(f"{path.name}.unreadable.{n}")).exists():
        n += 1
    os.rename(path, backup)
    _tighten_mode(backup)                 # r8 F7: the backup holds passwords
    logger.warning("Broker profile store reset; unreadable copy kept at %s", backup)
    return backup


def get_active_profile(profiles: Optional[Dict[str, BrokerProfile]] = None) -> Optional[BrokerProfile]:
    """Get the currently active broker profile.

    Args:
        profiles: Profiles dict (loaded if None)

    Returns:
        Active BrokerProfile or None
    """
    if profiles is None:
        profiles = load_profiles()

    for profile in profiles.values():
        if profile.is_active:
            return profile
    return None


def set_active_profile(name: str, profiles: Optional[Dict[str, BrokerProfile]] = None) -> bool:
    """Set a profile as the active broker profile.

    Args:
        name: Profile name to activate
        profiles: Profiles dict (loaded if None)

    Returns:
        True if activated successfully
    """
    if profiles is None:
        profiles = load_profiles()

    if name not in profiles:
        logger.error("Profile '%s' not found", name)
        return False

    # r9 #1: stage the flags on COPIES; the caller's dict (what the screen
    # renders) changes only after the save succeeded
    staged = {pname: replace(p, is_active=(pname == name))
              for pname, p in profiles.items()}
    if not save_profiles(staged):
        return False
    for pname, profile in profiles.items():
        profile.is_active = (pname == name)
    return True


# =============================================================================
# Mosquitto Service Management
# =============================================================================

def install_mosquitto_config(profile: BrokerProfile) -> Tuple[bool, str]:
    """Install mosquitto configuration files for a private broker.

    Creates:
    - /etc/mosquitto/conf.d/meshforge.conf
    - /etc/mosquitto/meshforge_passwd (password file)
    - /etc/mosquitto/meshforge_acl (ACL file)

    Requires root privileges.

    Args:
        profile: Private broker profile

    Returns:
        (success, message) tuple
    """
    if os.geteuid() != 0:
        return False, "Root privileges required. Run with sudo."

    conf_dir = Path("/etc/mosquitto/conf.d")
    if not conf_dir.parent.exists():
        return False, (
            "Mosquitto not installed.\n"
            "Install with: sudo apt install mosquitto mosquitto-clients"
        )

    try:
        # Ensure conf.d exists
        conf_dir.mkdir(parents=True, exist_ok=True)

        # Write mosquitto config
        conf_path = conf_dir / "meshforge.conf"
        conf_content = generate_mosquitto_conf(profile)
        # Atomic: a torn mosquitto.conf can stop the broker from starting.
        atomic_write_text(conf_path, conf_content)

        messages = [f"Config written to {conf_path}"]

        # Create password file if authenticated
        if not profile.allow_anonymous and profile.username and profile.password:
            passwd_path = Path("/etc/mosquitto/meshforge_passwd")

            # mosquitto_passwd creates/updates the password file
            result = subprocess.run(
                ["mosquitto_passwd", "-b", str(passwd_path),
                 profile.username, profile.password],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                messages.append(f"Password file created: {passwd_path}")
                # Set permissions
                os.chmod(str(passwd_path), 0o640)
            else:
                messages.append(
                    f"Warning: mosquitto_passwd failed: {result.stderr.strip()}"
                )

        # Write ACL file if enabled
        if profile.acl_enabled and not profile.allow_anonymous:
            acl_path = Path("/etc/mosquitto/meshforge_acl")
            acl_content = generate_mosquitto_acl(profile)
            atomic_write_text(acl_path, acl_content)
            messages.append(f"ACL file written to {acl_path}")

        return True, "\n".join(messages)

    except subprocess.TimeoutExpired:
        return False, "mosquitto_passwd timed out"
    except OSError as e:
        return False, f"File operation failed: {e}"


def check_mosquitto_installed() -> Tuple[bool, str]:
    """Check if mosquitto is installed and available.

    Returns:
        (installed, message) tuple
    """
    import shutil

    mosquitto_bin = shutil.which("mosquitto")
    mosquitto_passwd = shutil.which("mosquitto_passwd")

    if not mosquitto_bin:
        return False, (
            "Mosquitto broker not installed.\n\n"
            "Install with:\n"
            "  sudo apt install mosquitto mosquitto-clients"
        )

    if not mosquitto_passwd:
        return False, (
            "Mosquitto tools not fully installed.\n\n"
            "Install mosquitto-clients:\n"
            "  sudo apt install mosquitto-clients"
        )

    return True, "Mosquitto is installed"


def check_mosquitto_running() -> Tuple[bool, str]:
    """Check if mosquitto service is running.

    Returns:
        (running, message) tuple
    """
    status = _check_service('mosquitto')
    return status.available, status.message


def restart_mosquitto() -> Tuple[bool, str]:
    """Restart the mosquitto service after config changes.

    Returns:
        (success, message) tuple
    """
    if os.geteuid() != 0:
        return False, "Root privileges required. Run with sudo."

    return _apply_config_and_restart('mosquitto')


def enable_mosquitto_at_boot() -> Tuple[bool, str]:
    """Enable mosquitto to start at boot.

    Returns:
        (success, message) tuple
    """
    if os.geteuid() != 0:
        return False, "Root privileges required. Run with sudo."

    return _enable_service('mosquitto', start=True)


def host_needs_lan_ip(profile: BrokerProfile) -> bool:
    """True when the broker host is local — the radio connects over WiFi and
    cannot reach 'localhost'/127.0.0.1, so the radio must use the host's LAN IP.
    The in-app apply prompts for that IP; the display string shows a placeholder.
    """
    return profile.host in ("localhost", "127.0.0.1")


def get_meshtastic_mqtt_setup_argv(
        profile: BrokerProfile, host: str = None) -> List[List[str]]:
    """The meshtastic CLI commands to point the radio at this broker, as argv
    lists ready to execute.

    SSOT for BOTH the human-readable display string (get_meshtastic_mqtt_setup_
    commands) and the in-app apply (broker._apply_mqtt_to_radio) — so the two can
    never drift (honest_failure_modes #5). ``host`` overrides the broker host
    (pass the host's LAN IP for a local broker — see host_needs_lan_ip); None
    uses profile.host verbatim.
    """
    h = host if host is not None else profile.host
    base = ["meshtastic", "--host", "localhost"]

    set_cmd = base + [
        "--set", "mqtt.enabled", "true",
        "--set", "mqtt.address", h,
    ]
    if profile.username:
        set_cmd += ["--set", "mqtt.username", profile.username]
    if profile.password:
        set_cmd += ["--set", "mqtt.password", profile.password]
    set_cmd += [
        "--set", "mqtt.encryption_enabled", "true",
        "--set", "mqtt.json_enabled", str(profile.json_enabled).lower(),
        "--set", "mqtt.tls_enabled", str(profile.use_tls).lower(),
    ]

    ch_cmd = base + [
        "--ch-set", "uplink_enabled", "true", "--ch-index", "0",
        "--ch-set", "downlink_enabled", "true", "--ch-index", "0",
    ]
    return [set_cmd, ch_cmd]


MQTT_PASSWORD_PLACEHOLDER = "<MQTT_PASSWORD>"


def get_meshtastic_mqtt_setup_commands(profile: BrokerProfile,
                                       reveal_password: bool = False) -> str:
    """Render the radio MQTT setup as a human-readable command block.

    Renders from get_meshtastic_mqtt_setup_argv (the SSOT) so display and the
    in-app apply stay in lockstep. A local broker shows a <YOUR_SERVER_IP>
    placeholder (the radio needs the host's LAN IP, not localhost).

    The password renders as MQTT_PASSWORD_PLACEHOLDER unless the caller passes
    reveal_password=True after the operator ASKED (r6 #3, operator 2026-11-03):
    a stored credential is never on screen by default. The argv is unchanged.
    """
    lines = [
        "# Configure Meshtastic radio MQTT module for this broker",
        f"# Profile: {profile.name}",
        "",
    ]

    host = None
    if host_needs_lan_ip(profile):
        lines.append("# NOTE: Set broker to your device's LAN IP, not localhost")
        lines.append("# The radio needs the IP of the machine running mosquitto")
        host = "<YOUR_SERVER_IP>"

    set_cmd, ch_cmd = get_meshtastic_mqtt_setup_argv(profile, host=host)

    if not reveal_password and "mqtt.password" in set_cmd:
        i = set_cmd.index("mqtt.password") + 1
        if i < len(set_cmd) and set_cmd[i]:
            set_cmd = [*set_cmd[:i], MQTT_PASSWORD_PLACEHOLDER, *set_cmd[i + 1:]]
            lines.append(f"# {MQTT_PASSWORD_PLACEHOLDER} = the password saved in "
                         f"profile '{profile.name}' (not shown)")

    lines.append(" \\\n  ".join(set_cmd))
    lines.append("")
    lines.append("# Enable uplink (mesh -> MQTT) and downlink (MQTT -> mesh)")
    lines.append(" \\\n  ".join(ch_cmd))

    return "\n".join(lines)


# =============================================================================
# Convenience: Initialize default profiles
# =============================================================================

DEFAULT_PROFILE_NAMES = ("meshforge_private", "meshtastic_public")
# r10b F1: the one refusal that ATTEMPTED a write — the TUI keys on it (hfm #5)
DEFAULTS_WRITE_FAILED = "could not write the profile store"


def create_default_profiles() -> Tuple[bool, str]:
    """Write the two default profiles — ONLY on the operator's explicit Yes.

    F8 v2 r10 (r9 F1/F2, V13): this used to run on every Broker Manager open
    (``ensure_default_profiles``), so an empty store silently grew two
    profiles and a new private password nobody saw, and deleting them only
    brought them back. Refuses unless the store is readable and EMPTY
    (no loaded and no unloaded profiles) at the moment of the write.

    Returns (ok, why-not).
    """
    status = profile_store_status()
    if status.state in ("unreadable", "denied"):
        return False, f"the profile store is {status.state} ({status.detail})"
    if load_profiles() or status.skipped:
        return False, "the profile store is no longer empty — nothing written"
    profiles = {
        DEFAULT_PROFILE_NAMES[0]: create_private_profile(),
        DEFAULT_PROFILE_NAMES[1]: create_public_profile(),
    }
    if not save_profiles(profiles):
        return False, DEFAULTS_WRITE_FAILED
    logger.info("Created default broker profiles (operator confirmed)")
    return True, ""

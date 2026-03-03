"""
Channel Scan — detect and monitor active Meshtastic channels.

Since Meshtastic channels are pre-configured and encrypted, this module
monitors channel activity rather than scanning for unknown channels.
It queries the local device for configured channels and tracks message
traffic per channel to identify utilization patterns.

Features:
    - Query configured channels from meshtasticd (CLI or TCP)
    - Track message activity per channel (count, timestamps)
    - Calculate channel utilization metrics
    - Identify quiet vs busy channels
    - Detect channel activity from MQTT topic structure

Usage:
    from utils.channel_scan import ChannelMonitor

    monitor = ChannelMonitor()
    monitor.record_activity(channel=0, message_type="text")
    report = monitor.get_activity_report()
"""

import json
import logging
import subprocess
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum channels Meshtastic supports
MAX_CHANNELS = 8

# Activity is "recent" if within this window
RECENT_WINDOW_SEC = 3600  # 1 hour

# Channel utilization thresholds
UTILIZATION_LOW = 10       # messages/hour
UTILIZATION_MEDIUM = 50    # messages/hour
UTILIZATION_HIGH = 100     # messages/hour


@dataclass
class ChannelConfig:
    """Configuration for a single Meshtastic channel."""
    index: int
    name: str = ""
    role: str = "DISABLED"     # PRIMARY, SECONDARY, DISABLED
    psk: str = ""              # Pre-shared key (redacted in display)
    uplink_enabled: bool = False
    downlink_enabled: bool = False

    @property
    def is_active(self) -> bool:
        """Channel is active if it has a role other than DISABLED."""
        return self.role.upper() != "DISABLED"

    @property
    def display_name(self) -> str:
        """Human-readable channel name."""
        if self.name:
            return self.name
        if self.index == 0:
            return "Primary"
        return f"Channel {self.index}"

    @property
    def has_encryption(self) -> bool:
        """Whether channel has a PSK configured."""
        return bool(self.psk) and self.psk != "AQ=="  # "AQ==" is default/none


@dataclass
class ChannelActivity:
    """Activity tracking for a single channel."""
    channel_index: int
    message_count: int = 0
    text_count: int = 0
    position_count: int = 0
    telemetry_count: int = 0
    nodeinfo_count: int = 0
    other_count: int = 0
    first_activity: float = 0.0
    last_activity: float = 0.0
    timestamps: List[float] = field(default_factory=list)

    @property
    def messages_per_hour(self) -> float:
        """Calculate message rate (messages per hour)."""
        if not self.timestamps:
            return 0.0
        now = time.time()
        recent = [t for t in self.timestamps
                  if (now - t) < RECENT_WINDOW_SEC]
        if not recent:
            return 0.0
        oldest = min(recent)
        window = max(1.0, min(RECENT_WINDOW_SEC, now - oldest))
        return len(recent) * 3600.0 / window

    @property
    def utilization_level(self) -> str:
        """Categorize channel utilization."""
        rate = self.messages_per_hour
        if rate == 0:
            return "quiet"
        elif rate < UTILIZATION_LOW:
            return "low"
        elif rate < UTILIZATION_MEDIUM:
            return "medium"
        elif rate < UTILIZATION_HIGH:
            return "high"
        else:
            return "very high"

    @property
    def is_active_recently(self) -> bool:
        """Whether channel has had activity in the recent window."""
        if self.last_activity == 0.0:
            return False
        return (time.time() - self.last_activity) < RECENT_WINDOW_SEC

    def record(self, message_type: str = "other") -> None:
        """Record a message on this channel.

        Args:
            message_type: Type of message (text, position, telemetry,
                         nodeinfo, other).
        """
        now = time.time()
        self.message_count += 1
        if self.first_activity == 0.0:
            self.first_activity = now
        self.last_activity = now

        # Track by type
        msg_type = message_type.lower()
        if msg_type == "text":
            self.text_count += 1
        elif msg_type == "position":
            self.position_count += 1
        elif msg_type == "telemetry":
            self.telemetry_count += 1
        elif msg_type == "nodeinfo":
            self.nodeinfo_count += 1
        else:
            self.other_count += 1

        # Add to timestamps (keep last hour only, capped for memory safety)
        self.timestamps.append(now)
        cutoff = now - RECENT_WINDOW_SEC
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        # Cap at 5000 entries to prevent memory growth under extreme load
        if len(self.timestamps) > 5000:
            self.timestamps = self.timestamps[-5000:]

    def reset(self) -> None:
        """Reset all activity counters."""
        self.message_count = 0
        self.text_count = 0
        self.position_count = 0
        self.telemetry_count = 0
        self.nodeinfo_count = 0
        self.other_count = 0
        self.first_activity = 0.0
        self.last_activity = 0.0
        self.timestamps = []


class ChannelMonitor:
    """Monitors Meshtastic channel activity.

    Tracks message traffic per channel and provides utilization
    reports. Can query device for configured channels.
    """

    def __init__(self):
        """Initialize channel monitor."""
        self._channels: Dict[int, ChannelConfig] = {}
        self._activity: Dict[int, ChannelActivity] = {}
        self._lock = threading.Lock()

        # Initialize activity trackers for all possible channels
        for i in range(MAX_CHANNELS):
            self._activity[i] = ChannelActivity(channel_index=i)

    def record_activity(self, channel: int,
                        message_type: str = "other") -> None:
        """Record a message on a channel.

        Args:
            channel: Channel index (0-7).
            message_type: Type (text, position, telemetry, nodeinfo, other).
        """
        if not (0 <= channel < MAX_CHANNELS):
            logger.debug(f"Invalid channel index: {channel}")
            return

        with self._lock:
            self._activity[channel].record(message_type)

    def get_channel_activity(self, channel: int) -> Optional[ChannelActivity]:
        """Get activity data for a specific channel.

        Args:
            channel: Channel index.

        Returns:
            ChannelActivity or None if invalid index.
        """
        if not (0 <= channel < MAX_CHANNELS):
            return None
        with self._lock:
            return self._activity[channel]

    def get_active_channels(self) -> List[int]:
        """Get indices of channels with recent activity."""
        with self._lock:
            return [i for i, a in self._activity.items()
                    if a.is_active_recently]

    def get_total_messages(self) -> int:
        """Get total message count across all channels."""
        with self._lock:
            return sum(a.message_count for a in self._activity.values())

    def query_device_channels(self) -> List[ChannelConfig]:
        """Query meshtasticd for configured channels.

        Attempts to read channel configuration from the local
        Meshtastic device via CLI.

        Returns:
            List of configured channels (empty on failure).
        """
        channels = []
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("meshtastic CLI not available")
                return channels

            result = subprocess.run(
                [cli_path, '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                channels = self._parse_channel_info(result.stdout)
        except FileNotFoundError:
            logger.debug("meshtastic CLI not available")
        except subprocess.TimeoutExpired:
            logger.debug("meshtastic --info timed out")
        except Exception as e:
            logger.debug(f"Channel query failed: {e}")

        if channels:
            with self._lock:
                for ch in channels:
                    self._channels[ch.index] = ch

        return channels

    def _parse_channel_info(self, output: str) -> List[ChannelConfig]:
        """Parse channel info from meshtastic --info output.

        Args:
            output: stdout from meshtastic --info command.

        Returns:
            List of ChannelConfig objects.
        """
        channels = []
        lines = output.split('\n')
        in_channels = False
        current_index = -1

        for line in lines:
            stripped = line.strip()

            # Detect channel section
            if 'Channels:' in stripped or 'channels:' in stripped:
                in_channels = True
                continue

            if not in_channels:
                continue

            # End of channels section
            if stripped and not stripped.startswith(('-', ' ', 'Index', 'index')):
                if 'Preferences' in stripped or 'Nodes' in stripped:
                    break

            # Parse channel entries
            # Format varies: "  0: LongFast (PRIMARY)"
            # or "Index: 0, Role: PRIMARY, Name: LongFast"
            if ':' in stripped:
                parts = stripped.split(':')
                try:
                    # Try "Index: N" format
                    if 'index' in parts[0].lower():
                        current_index = int(parts[1].strip().split(',')[0].split()[0])
                    # Try "N: Name (ROLE)" format
                    elif parts[0].strip().isdigit():
                        current_index = int(parts[0].strip())
                        rest = ':'.join(parts[1:]).strip()
                        name = ""
                        role = "SECONDARY"
                        if '(' in rest:
                            name = rest.split('(')[0].strip()
                            role = rest.split('(')[1].rstrip(')').strip()
                        else:
                            name = rest.strip()

                        if current_index == 0 and not role:
                            role = "PRIMARY"

                        channels.append(ChannelConfig(
                            index=current_index,
                            name=name,
                            role=role.upper(),
                        ))
                except (ValueError, IndexError):
                    pass

            # Parse role/name on separate lines
            if 'role' in stripped.lower() and ':' in stripped:
                try:
                    role_val = stripped.split(':')[1].strip().upper()
                    if current_index >= 0 and current_index < MAX_CHANNELS:
                        # Update or create channel
                        existing = next((c for c in channels
                                         if c.index == current_index), None)
                        if existing:
                            existing.role = role_val
                        else:
                            channels.append(ChannelConfig(
                                index=current_index, role=role_val))
                except (ValueError, IndexError):
                    pass

        return channels

    def set_channels(self, channels: List[ChannelConfig]) -> None:
        """Set channel configuration manually.

        Useful for testing or when CLI is not available.

        Args:
            channels: List of channel configurations.
        """
        with self._lock:
            self._channels.clear()
            for ch in channels:
                self._channels[ch.index] = ch

    def get_channels(self) -> List[ChannelConfig]:
        """Get known channel configurations.

        Returns:
            List of configured channels, sorted by index.
        """
        with self._lock:
            return sorted(self._channels.values(), key=lambda c: c.index)

    def detect_channel_from_topic(self, topic: str) -> Optional[int]:
        """Detect channel index from MQTT topic.

        Meshtastic MQTT topics follow: msh/{region}/{channel_id}/...

        Args:
            topic: MQTT topic string.

        Returns:
            Channel index or None if not parseable.
        """
        parts = topic.split('/')
        # Format: msh/{region}/2/e/{channel_id}/...
        # or: msh/{region}/json/{channel_id}/...
        for i, part in enumerate(parts):
            if part in ('json', 'e', 'c') and i + 1 < len(parts):
                try:
                    channel_id = parts[i + 1]
                    # Channel ID is typically "LongFast", "0", etc.
                    if channel_id.isdigit():
                        return int(channel_id)
                    # Map known channel names to indices
                    # Channel 0 is typically the primary
                    return 0  # Default to primary for named channels
                except (ValueError, IndexError):
                    pass
        return None

    def get_activity_report(self) -> str:
        """Generate a formatted activity report.

        Returns:
            Multi-line string with channel activity summary.
        """
        lines = []
        with self._lock:
            total = sum(a.message_count for a in self._activity.values())
            active_count = sum(1 for a in self._activity.values()
                               if a.is_active_recently)
            lines.append(f"Channel Activity Report")
            lines.append(f"Total messages: {total} | "
                         f"Active channels: {active_count}/{MAX_CHANNELS}")
            lines.append("")

            lines.append(f"  {'Ch':<3} {'Name':<16} {'Role':<10} "
                         f"{'Msgs':>5} {'Rate/h':>7} {'Level':<9} {'Last'}")
            lines.append(f"  {'-'*3} {'-'*16} {'-'*10} "
                         f"{'-'*5} {'-'*7} {'-'*9} {'-'*12}")

            for i in range(MAX_CHANNELS):
                activity = self._activity[i]
                config = self._channels.get(i)

                # Skip channels with no config and no activity
                if not activity.message_count and (not config or not config.is_active):
                    continue

                name = config.display_name if config else f"Channel {i}"
                name = name[:16]
                role = config.role if config else "-"
                role = role[:10]
                msgs = activity.message_count
                rate = f"{activity.messages_per_hour:.1f}"
                level = activity.utilization_level

                if activity.last_activity > 0:
                    age = time.time() - activity.last_activity
                    if age < 60:
                        last = f"{age:.0f}s ago"
                    elif age < 3600:
                        last = f"{age / 60:.0f}m ago"
                    elif age < 86400:
                        last = f"{age / 3600:.1f}h ago"
                    else:
                        last = f"{age / 86400:.0f}d ago"
                else:
                    last = "never"

                lines.append(f"  {i:<3} {name:<16} {role:<10} "
                             f"{msgs:>5} {rate:>7} {level:<9} {last}")

            # Message type breakdown
            has_activity = any(a.message_count > 0
                               for a in self._activity.values())
            if has_activity:
                lines.append("")
                total_text = sum(a.text_count for a in self._activity.values())
                total_pos = sum(a.position_count for a in self._activity.values())
                total_tel = sum(a.telemetry_count for a in self._activity.values())
                total_node = sum(a.nodeinfo_count for a in self._activity.values())
                total_other = sum(a.other_count for a in self._activity.values())
                lines.append(f"  Types: text={total_text} position={total_pos} "
                             f"telemetry={total_tel} nodeinfo={total_node} "
                             f"other={total_other}")

        return "\n".join(lines)

    def get_stats(self) -> Dict:
        """Get channel monitor statistics.

        Returns:
            Dict with summary statistics.
        """
        with self._lock:
            return {
                'total_messages': sum(a.message_count
                                      for a in self._activity.values()),
                'active_channels': sum(1 for a in self._activity.values()
                                       if a.is_active_recently),
                'configured_channels': len(self._channels),
                'busiest_channel': max(
                    range(MAX_CHANNELS),
                    key=lambda i: self._activity[i].messages_per_hour
                ),
                'per_channel': {
                    i: {
                        'messages': self._activity[i].message_count,
                        'rate': self._activity[i].messages_per_hour,
                        'level': self._activity[i].utilization_level,
                    }
                    for i in range(MAX_CHANNELS)
                    if self._activity[i].message_count > 0
                },
            }

    def reset_all(self) -> None:
        """Reset all activity counters."""
        with self._lock:
            for activity in self._activity.values():
                activity.reset()

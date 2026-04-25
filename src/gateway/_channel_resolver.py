"""Resolve the bridge TX channel index from its channel name.

The gateway config carries two channel fields that must agree for the
bridge to work in both directions:

- ``mqtt_bridge.channel`` — a channel *name* (e.g. ``"meshforge"``) used
  to subscribe to MQTT uplinks from meshtasticd.
- ``meshtastic.channel`` — a numeric *index* used when the gateway TX's
  RNS→Meshtastic traffic through ``send_text_direct()``.

If these disagree, TX lands on the wrong channel (see Issue #42). This
helper queries meshtasticd's channel list at gateway startup and returns
the correct index for a given name so the two fields can be reconciled
automatically.
"""

from __future__ import annotations

from typing import Tuple

from utils.logging_config import get_logger

logger = get_logger(__name__)

Status = str  # one of the literals below


class ChannelResolutionError(RuntimeError):
    """Bridge TX channel cannot be confirmed against the live radio.

    Raised by ``apply_resolved_channel`` when ``mqtt_bridge.channel`` is
    set to a non-empty name but the radio either cannot be queried or
    does not advertise that name. Refuse-loud per
    ``feedback_refuse_broken_configs``: a silent fallback to whatever
    integer ``meshtastic.channel`` happens to hold can land bridged TX
    on the wrong channel (e.g. a statewide channel reserved for an
    unrelated bot).
    """


def resolve_tx_channel_index(
    channel_name: str,
    fallback_index: int,
) -> Tuple[int, Status]:
    """Query meshtasticd and resolve ``channel_name`` to its numeric index.

    Returns ``(index, status)`` where ``status`` is one of:

    - ``"no_name"`` — ``channel_name`` is empty; returned ``fallback_index``.
    - ``"matches_config"`` — resolved index equals ``fallback_index``.
    - ``"resolved"`` — resolved index differs from ``fallback_index``.
    - ``"unreachable"`` — meshtasticd query failed; returned ``fallback_index``.
    - ``"not_found"`` — channel name absent on radio; returned ``fallback_index``.

    Never raises. Safe to call at gateway startup.
    """
    if not channel_name:
        return fallback_index, "no_name"

    channels = _query_channels()
    if channels is None:
        return fallback_index, "unreachable"

    for ch in channels:
        if ch.get("name") == channel_name:
            idx = int(ch.get("index", fallback_index))
            if idx == fallback_index:
                return idx, "matches_config"
            return idx, "resolved"

    return fallback_index, "not_found"


def _query_channels():
    """Return meshtasticd's channel list, or ``None`` on failure."""
    try:
        from utils.connection_manager import MeshtasticConnection
    except ImportError as e:
        logger.warning(f"Cannot import MeshtasticConnection: {e}")
        return None

    try:
        with MeshtasticConnection(connect=True, blocking=True, timeout=10) as iface:
            if iface is None:
                return None
            if not hasattr(iface, "localNode") or iface.localNode is None:
                return None
            local_node = iface.localNode
            if not hasattr(local_node, "channels"):
                return None

            channels = []
            for idx, ch in enumerate(local_node.channels):
                name = ""
                if hasattr(ch, "settings") and hasattr(ch.settings, "name"):
                    name = ch.settings.name or ""
                channels.append({"index": idx, "name": name})
            return channels
    except Exception as e:
        logger.warning(f"Channel query failed: {e}")
        return None


def apply_resolved_channel(config) -> None:
    """Resolve + log + mutate ``config.meshtastic.channel`` in place.

    Call once at gateway startup after the meshtasticd pre-flight check.
    """
    bridge_name = getattr(config.mqtt_bridge, "channel", "")
    fallback = int(getattr(config.meshtastic, "channel", 0))

    resolved, status = resolve_tx_channel_index(bridge_name, fallback)

    if status == "no_name":
        logger.debug(
            f"TX channel resolution skipped: mqtt_bridge.channel empty; "
            f"using meshtastic.channel={fallback}"
        )
    elif status == "matches_config":
        logger.info(
            f"TX channel {bridge_name!r} resolved to index {resolved} "
            f"(matches configured meshtastic.channel)"
        )
    elif status == "resolved":
        logger.warning(
            f"TX channel mismatch corrected: mqtt_bridge.channel={bridge_name!r} "
            f"resolves to index {resolved}, but meshtastic.channel={fallback}. "
            f"Using resolved index {resolved} for TX. "
            f"Update gateway.json to silence this warning."
        )
        config.meshtastic.channel = resolved
    elif status == "unreachable":
        raise ChannelResolutionError(
            f"Cannot confirm bridge TX channel: meshtasticd channel-list "
            f"query failed and mqtt_bridge.channel={bridge_name!r} is set. "
            f"Refusing to start with an unverified channel index "
            f"(meshtastic.channel={fallback}) — bridged TX could land on "
            f"the wrong channel. "
            f"Fix: ensure meshtasticd is reachable on its TCP port, then "
            f"restart the gateway. If this persists, run "
            f"scripts/configure_gateway.sh to re-derive the index."
        )
    elif status == "not_found":
        raise ChannelResolutionError(
            f"Bridge channel {bridge_name!r} is not present on the radio's "
            f"channel list. Refusing to start: configured "
            f"meshtastic.channel={fallback} is unverified and could point "
            f"at any other channel. "
            f"Fix: add a channel named {bridge_name!r} to the radio "
            f"(meshtastic --ch-set name {bridge_name} --ch-index N), or "
            f"update mqtt_bridge.channel in gateway.json to match an "
            f"existing channel name, or run scripts/configure_gateway.sh."
        )

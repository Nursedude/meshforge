"""Readers for `meshtastic --info` output — ONE parser per artifact (hfm #5).

Moved here 2026-11-01 from ChannelConfigHandler._parse_primary_name so every
consumer reads channel 0 the same way. Before that, two sibling detectors
(handlers/mqtt.py _detect_local_channel, handlers/service_menu.py
_auto_detect_primary_channel) took the FIRST line containing "name" — a
node's "longName" in the "Nodes in mesh" block, printed before "Channels:" —
and the MQTT Local preset saved the channel 'NodeA",' (live, 2026-11-01).

Format (pinned meshtastic 2.7.11, node.showChannels):
    "  Index N: ROLE psk=<x> <one-line protobuf json, every field>"
MeshAnchor twin: same file.
"""
import json
import re
from typing import Optional

# Channel 0's line in `meshtastic --info`.
CH0_LINE = re.compile(r'Index 0: PRIMARY[^\n{]*(\{[^\n]*\})')


def parse_primary_channel_name(info: str) -> Optional[str]:
    """Channel 0's name from `meshtastic --info`: "" when the primary is
    unnamed (the firmware then shows its preset name), None when the output
    carries no readable primary channel — a read that did not happen. The
    CLI prints the channel through protobuf json_format (ensure_ascii), so
    the object is JSON-DECODED: a regex capture handed back escape text for
    any non-ASCII or quoted name (reader pair, 2026-09-29)."""
    if not info or "Index 0: PRIMARY" not in info:
        return None
    m = CH0_LINE.search(info)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except ValueError:
        return None
    name = obj.get("name", "") if isinstance(obj, dict) else None
    return name if isinstance(name, str) else None

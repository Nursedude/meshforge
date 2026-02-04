"""
Packet Dissectors - Protocol parsers for mesh network packets.

Contains:
- PacketDissector: Abstract base class for protocol dissectors
- MeshtasticDissector: Parser for Meshtastic protocol packets
- RNSDissector: Parser for Reticulum (RNS) protocol packets
- DisplayFilter: Wireshark-style packet filtering
"""

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from .traffic_models import (
    FieldType,
    HopState,
    MeshPacket,
    MESHTASTIC_PORTS,
    PacketDirection,
    PacketField,
    PacketProtocol,
    PacketTree,
)


# =============================================================================
# PACKET DISSECTOR (PROTOCOL PARSER)
# =============================================================================

class PacketDissector(ABC):
    """
    Base class for protocol dissectors.

    Each protocol (Meshtastic, RNS) has its own dissector that parses
    raw packets into structured MeshPacket objects with full PacketTree.
    """

    @abstractmethod
    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this dissector can handle the data."""
        pass

    @abstractmethod
    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse raw data into a MeshPacket with protocol tree."""
        pass

    def _add_frame_layer(self, tree: PacketTree, packet: MeshPacket) -> PacketField:
        """Add frame-level fields common to all protocols."""
        frame = tree.add_layer("Frame", "frame")
        tree.add_field(frame, "Timestamp", "frame.time",
                       packet.timestamp, FieldType.TIMESTAMP)
        tree.add_field(frame, "Direction", "frame.direction",
                       packet.direction.value, FieldType.ENUM)
        tree.add_field(frame, "Size", "frame.size",
                       packet.size, FieldType.INTEGER,
                       "Total packet size in bytes")
        tree.add_field(frame, "Protocol", "frame.protocol",
                       packet.protocol.value, FieldType.ENUM)
        return frame


class MeshtasticDissector(PacketDissector):
    """
    Dissector for Meshtastic packets.

    Parses Meshtastic protocol packets including:
    - Header (from, to, channel, hop_limit)
    - Payload (portnum, decoded content)
    - Metrics (SNR, RSSI)
    """

    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this looks like a Meshtastic packet."""
        # Check for meshtastic indicators in metadata
        if metadata.get("protocol") == "meshtastic":
            return True
        if "from" in metadata and "to" in metadata:
            return True
        return False

    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse Meshtastic packet into MeshPacket."""
        packet = MeshPacket(
            protocol=PacketProtocol.MESHTASTIC,
            raw_bytes=data,
            size=len(data) if data else 0,
        )

        # Extract from metadata (typically from meshtastic-python callbacks)
        packet.source = str(metadata.get("from", metadata.get("fromId", "")))
        packet.destination = str(metadata.get("to", metadata.get("toId", "broadcast")))
        packet.channel = int(metadata.get("channel", 0))
        packet.hop_limit = int(metadata.get("hopLimit", 3))
        packet.hop_start = int(metadata.get("hopStart", 3))
        packet.hops_taken = packet.hop_start - packet.hop_limit  # Recalculate after setting
        packet.via_mqtt = bool(metadata.get("viaMqtt", False))
        packet.want_ack = bool(metadata.get("wantAck", False))

        # Payload info
        packet.portnum = int(metadata.get("portnum", metadata.get("decoded", {}).get("portnum", 0)))
        packet.port_name = MESHTASTIC_PORTS.get(packet.portnum, f"PORT_{packet.portnum}")

        # Decoded payload
        if "decoded" in metadata:
            decoded = metadata["decoded"]
            packet.decoded_payload = decoded
            if "payload" in decoded:
                try:
                    packet.payload = decoded["payload"] if isinstance(decoded["payload"], bytes) else decoded["payload"].encode()
                except (AttributeError, UnicodeError):
                    pass

        # Metrics
        packet.snr = metadata.get("snr", metadata.get("rxSnr"))
        packet.rssi = metadata.get("rssi", metadata.get("rxRssi"))
        if "channelUtilization" in metadata:
            packet.channel_utilization = metadata["channelUtilization"]
        if "airUtilTx" in metadata:
            packet.air_util_tx = metadata["airUtilTx"]

        # Relay tracking (Meshtastic 2.6+)
        relay_node = metadata.get("relayNode")
        if relay_node and relay_node > 0:
            packet.relay_node = relay_node
        next_hop = metadata.get("nextHop")
        if next_hop and next_hop > 0:
            packet.next_hop = next_hop

        # Direction
        if metadata.get("direction") == "outbound":
            packet.direction = PacketDirection.OUTBOUND
        elif metadata.get("relayed") or packet.relay_node:
            packet.direction = PacketDirection.RELAYED
        else:
            packet.direction = PacketDirection.INBOUND

        # Build protocol tree
        packet.tree = self._build_tree(packet, metadata)

        return packet

    def _build_tree(self, packet: MeshPacket, metadata: Dict[str, Any]) -> PacketTree:
        """Build hierarchical protocol tree."""
        tree = PacketTree()

        # Frame layer
        self._add_frame_layer(tree, packet)

        # Meshtastic layer
        mesh = tree.add_layer("Meshtastic", "mesh")

        # Addressing
        tree.add_field(mesh, "Source", "mesh.from", packet.source, FieldType.STRING,
                       "Source node ID")
        tree.add_field(mesh, "Destination", "mesh.to", packet.destination, FieldType.STRING,
                       "Destination node ID (^all for broadcast)")
        tree.add_field(mesh, "Channel", "mesh.channel", packet.channel, FieldType.INTEGER,
                       "Channel index (0-7)")

        # Routing
        routing = PacketField(name="Routing", abbrev="mesh.routing",
                              value=None, field_type=FieldType.NESTED)
        mesh.children.append(routing)

        tree.add_field(routing, "Hop Limit", "mesh.hop_limit", packet.hop_limit, FieldType.INTEGER,
                       "Remaining hops allowed")
        tree.add_field(routing, "Hop Start", "mesh.hop_start", packet.hop_start, FieldType.INTEGER,
                       "Original hop limit when sent")
        tree.add_field(routing, "Hops Taken", "mesh.hops", packet.hops_taken, FieldType.INTEGER,
                       "Number of hops traversed")
        tree.add_field(routing, "Via MQTT", "mesh.mqtt", packet.via_mqtt, FieldType.BOOLEAN,
                       "Received via MQTT broker")
        tree.add_field(routing, "Want ACK", "mesh.ack", packet.want_ack, FieldType.BOOLEAN,
                       "Acknowledgement requested")

        # Relay tracking (Meshtastic 2.6+)
        if packet.relay_node is not None:
            tree.add_field(routing, "Relay Node", "mesh.relay", f"!????{packet.relay_node:02x}",
                           FieldType.STRING, "Last byte of relay node ID (Meshtastic 2.6+)")
        if packet.next_hop is not None:
            tree.add_field(routing, "Next Hop", "mesh.next_hop", f"!????{packet.next_hop:02x}",
                           FieldType.STRING, "Expected next-hop relay (Meshtastic 2.6+)")

        # Payload
        payload_field = PacketField(name="Payload", abbrev="mesh.payload",
                                    value=None, field_type=FieldType.NESTED)
        mesh.children.append(payload_field)

        tree.add_field(payload_field, "Port Number", "mesh.portnum", packet.portnum, FieldType.INTEGER,
                       "Meshtastic application port")
        tree.add_field(payload_field, "Port Name", "mesh.port", packet.port_name, FieldType.STRING,
                       "Application type")
        tree.add_field(payload_field, "Size", "mesh.payload_size", len(packet.payload), FieldType.INTEGER,
                       "Payload size in bytes")

        # Decoded content based on port
        if packet.decoded_payload:
            self._add_decoded_fields(tree, payload_field, packet)

        # Metrics
        if packet.snr is not None or packet.rssi is not None:
            metrics = PacketField(name="Radio Metrics", abbrev="mesh.radio",
                                  value=None, field_type=FieldType.NESTED)
            mesh.children.append(metrics)

            if packet.snr is not None:
                tree.add_field(metrics, "SNR", "mesh.snr", packet.snr, FieldType.FLOAT,
                               "Signal-to-Noise Ratio (dB)")
            if packet.rssi is not None:
                tree.add_field(metrics, "RSSI", "mesh.rssi", packet.rssi, FieldType.INTEGER,
                               "Received Signal Strength (dBm)")
            if packet.channel_utilization is not None:
                tree.add_field(metrics, "Channel Util", "mesh.chan_util",
                               packet.channel_utilization, FieldType.FLOAT,
                               "Channel utilization percentage")
            if packet.air_util_tx is not None:
                tree.add_field(metrics, "TX Air Util", "mesh.air_util",
                               packet.air_util_tx, FieldType.FLOAT,
                               "Transmit airtime utilization")

        return tree

    def _add_decoded_fields(self, tree: PacketTree, parent: PacketField,
                            packet: MeshPacket) -> None:
        """Add decoded payload fields based on port type."""
        decoded = packet.decoded_payload

        if packet.portnum == 1:  # TEXT_MESSAGE
            text = decoded.get("text", decoded.get("payload", ""))
            tree.add_field(parent, "Text", "mesh.text", text, FieldType.STRING)

        elif packet.portnum == 4:  # POSITION
            pos_data = decoded.get("position", decoded)
            if "latitude" in pos_data:
                tree.add_field(parent, "Latitude", "mesh.pos.lat",
                               pos_data["latitude"], FieldType.FLOAT)
            if "longitude" in pos_data:
                tree.add_field(parent, "Longitude", "mesh.pos.lon",
                               pos_data["longitude"], FieldType.FLOAT)
            if "altitude" in pos_data:
                tree.add_field(parent, "Altitude", "mesh.pos.alt",
                               pos_data["altitude"], FieldType.INTEGER)

        elif packet.portnum == 5:  # NODEINFO
            user_data = decoded.get("user", decoded)
            if "longName" in user_data:
                tree.add_field(parent, "Long Name", "mesh.user.long",
                               user_data["longName"], FieldType.STRING)
            if "shortName" in user_data:
                tree.add_field(parent, "Short Name", "mesh.user.short",
                               user_data["shortName"], FieldType.STRING)
            if "hwModel" in user_data:
                tree.add_field(parent, "Hardware", "mesh.user.hw",
                               user_data["hwModel"], FieldType.STRING)

        elif packet.portnum == 67:  # TELEMETRY
            telem = decoded.get("telemetry", decoded)
            if "deviceMetrics" in telem:
                dm = telem["deviceMetrics"]
                if "batteryLevel" in dm:
                    tree.add_field(parent, "Battery", "mesh.telem.battery",
                                   dm["batteryLevel"], FieldType.INTEGER)
                if "voltage" in dm:
                    tree.add_field(parent, "Voltage", "mesh.telem.voltage",
                                   dm["voltage"], FieldType.FLOAT)
                if "channelUtilization" in dm:
                    tree.add_field(parent, "Ch Util", "mesh.telem.ch_util",
                                   dm["channelUtilization"], FieldType.FLOAT)

        elif packet.portnum == 70:  # TRACEROUTE
            route = decoded.get("route", decoded.get("routeBack", []))
            tree.add_field(parent, "Route", "mesh.traceroute",
                           route, FieldType.STRING)


class RNSDissector(PacketDissector):
    """
    Dissector for Reticulum (RNS) packets.

    Parses RNS protocol data including:
    - Packet type (DATA, ANNOUNCE, LINK_*, PATH_*)
    - Header fields (flags, hop count)
    - Destination hash (16 bytes)
    - Interface info
    - Service type (LXMF, Nomad, etc.)
    - Link state tracking

    Wire format: [HEADER 2B] [ADDRESSES 16/32B] [CONTEXT 1B] [DATA 0-465B]
    Reference: https://reticulum.network/manual/understanding.html
    """

    # RNS packet types from header
    RNS_PACKET_TYPES = {
        0x00: "DATA",
        0x01: "ANNOUNCE",
        0x02: "LINK_REQUEST",
        0x03: "LINK_PROOF",
        0x04: "LINK_RTT",
        0x05: "LINK_KEEPALIVE",
        0x06: "LINK_CLOSE",
        0x07: "PATH_REQUEST",
        0x08: "PATH_RESPONSE",
    }

    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this looks like an RNS packet."""
        if metadata.get("protocol") == "rns":
            return True
        if "dest_hash" in metadata or "destination_hash" in metadata:
            return True
        if metadata.get("packet_type") in self.RNS_PACKET_TYPES.values():
            return True
        return False

    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse RNS packet into MeshPacket."""
        packet = MeshPacket(
            protocol=PacketProtocol.RNS,
            raw_bytes=data,
            size=len(data) if data else 0,
        )

        # Extract RNS-specific fields
        dest_hash = metadata.get("dest_hash", metadata.get("destination_hash"))
        if isinstance(dest_hash, str):
            try:
                packet.rns_dest_hash = bytes.fromhex(dest_hash)
            except ValueError:
                pass
        elif isinstance(dest_hash, bytes):
            packet.rns_dest_hash = dest_hash

        # Source hash (for announces)
        source_hash = metadata.get("source_hash", metadata.get("identity_hash"))
        if isinstance(source_hash, str):
            try:
                packet.source = source_hash
            except ValueError:
                packet.source = "local"
        elif isinstance(source_hash, bytes):
            packet.source = source_hash.hex()
        else:
            packet.source = metadata.get("source", "local")

        packet.destination = metadata.get("destination", "")
        if packet.rns_dest_hash:
            packet.destination = packet.rns_dest_hash.hex()[:16]

        packet.rns_interface = metadata.get("interface", "")
        packet.rns_service = metadata.get("service_type", metadata.get("aspect", ""))

        # Hop count
        packet.hops_taken = int(metadata.get("hops", 0))
        packet.hop_limit = 128 - packet.hops_taken  # RNS has higher hop limit
        packet.hop_start = 128  # RNS default TTL

        # Direction
        direction = metadata.get("direction", "inbound")
        if direction == "outbound":
            packet.direction = PacketDirection.OUTBOUND
        elif direction == "internal":
            packet.direction = PacketDirection.INTERNAL
        else:
            packet.direction = PacketDirection.INBOUND

        # Build protocol tree
        packet.tree = self._build_tree(packet, metadata, data)

        return packet

    def _build_tree(self, packet: MeshPacket, metadata: Dict[str, Any],
                    raw_data: bytes) -> PacketTree:
        """Build hierarchical protocol tree for RNS."""
        tree = PacketTree()

        # Frame layer
        self._add_frame_layer(tree, packet)

        # RNS layer
        rns = tree.add_layer("Reticulum", "rns")

        # Packet type
        packet_type = metadata.get("packet_type", "DATA")
        tree.add_field(rns, "Packet Type", "rns.type", packet_type, FieldType.STRING,
                       "RNS packet type")

        # Header fields (if raw data available)
        if raw_data and len(raw_data) >= 2:
            header = PacketField(name="Header", abbrev="rns.header",
                                value=None, field_type=FieldType.NESTED)
            rns.children.append(header)

            flags = (raw_data[0] >> 4) & 0x0F
            hops = raw_data[0] & 0x0F
            type_raw = (raw_data[1] >> 4) & 0x0F

            tree.add_field(header, "Flags", "rns.header.flags", flags, FieldType.INTEGER,
                          "Header flags")
            tree.add_field(header, "Hop Count", "rns.header.hops", hops, FieldType.INTEGER,
                          "Hops traversed")
            tree.add_field(header, "Type Byte", "rns.header.type", type_raw, FieldType.INTEGER,
                          "Packet type byte")

            # Context byte at position 18 (after 16-byte hash)
            if len(raw_data) >= 19:
                context = raw_data[18]
                tree.add_field(header, "Context", "rns.context", context, FieldType.INTEGER,
                              "Context byte")

        # Destination
        tree.add_field(rns, "Destination Hash", "rns.dest_hash",
                       packet.rns_dest_hash.hex() if packet.rns_dest_hash else "",
                       FieldType.BYTES, "16-byte destination hash (truncated SHA-256)")

        # Source (for announces)
        if packet.source and packet.source != "local":
            tree.add_field(rns, "Source Hash", "rns.source_hash", packet.source,
                          FieldType.BYTES, "Source identity hash")

        # Routing
        routing = PacketField(name="Routing", abbrev="rns.routing",
                             value=None, field_type=FieldType.NESTED)
        rns.children.append(routing)

        tree.add_field(routing, "Hops", "rns.hops", packet.hops_taken, FieldType.INTEGER,
                       "Number of hops traversed")
        tree.add_field(routing, "TTL", "rns.ttl", packet.hop_limit, FieldType.INTEGER,
                       "Remaining time-to-live (max 128)")
        tree.add_field(routing, "Interface", "rns.interface", packet.rns_interface, FieldType.STRING,
                       "RNS interface name")

        # Interface type if available
        iface_type = metadata.get("interface_type", "")
        if iface_type:
            tree.add_field(routing, "Interface Type", "rns.iface_type", iface_type, FieldType.STRING,
                          "Interface type (TCP, UDP, LoRa, etc.)")

        # Service info
        if packet.rns_service:
            tree.add_field(rns, "Service/Aspect", "rns.service", packet.rns_service, FieldType.STRING,
                           "Service type or aspect filter (e.g., lxmf.delivery)")

        # Announce details if packet is an announce
        if packet_type == "ANNOUNCE" or "announce" in metadata:
            announce = PacketField(name="Announce", abbrev="rns.announce",
                                   value=None, field_type=FieldType.NESTED)
            rns.children.append(announce)

            # Aspect
            aspect = metadata.get("aspect", metadata.get("announce_aspect", ""))
            if aspect:
                tree.add_field(announce, "Aspect", "rns.announce.aspect", aspect, FieldType.STRING,
                              "Announce aspect filter")

            # App data
            app_data = metadata.get("announce_app_data", metadata.get("app_data"))
            if app_data:
                if isinstance(app_data, bytes):
                    tree.add_field(announce, "App Data Size", "rns.announce.app_size",
                                  len(app_data), FieldType.INTEGER)
                    # Try to decode as display name
                    try:
                        decoded = app_data.decode('utf-8', errors='ignore')
                        if decoded.isprintable():
                            tree.add_field(announce, "Display Name", "rns.announce.name",
                                          decoded, FieldType.STRING)
                    except Exception:
                        pass
                elif isinstance(app_data, str):
                    tree.add_field(announce, "App Data", "rns.announce.app_data",
                                  app_data, FieldType.STRING)

            # Identity hash
            identity = metadata.get("identity_hash", metadata.get("announce_identity"))
            if identity:
                if isinstance(identity, bytes):
                    tree.add_field(announce, "Identity Hash", "rns.announce.identity",
                                  identity.hex(), FieldType.BYTES)
                else:
                    tree.add_field(announce, "Identity Hash", "rns.announce.identity",
                                  str(identity), FieldType.STRING)

        # Link details if this is a link packet
        link_id = metadata.get("link_id")
        link_state = metadata.get("link_state")
        if link_id or link_state or packet_type.startswith("LINK_"):
            link = PacketField(name="Link", abbrev="rns.link",
                              value=None, field_type=FieldType.NESTED)
            rns.children.append(link)

            if link_id:
                if isinstance(link_id, bytes):
                    tree.add_field(link, "Link ID", "rns.link.id", link_id.hex(), FieldType.BYTES)
                else:
                    tree.add_field(link, "Link ID", "rns.link.id", str(link_id), FieldType.STRING)

            if link_state:
                tree.add_field(link, "State", "rns.link.state", link_state, FieldType.STRING,
                              "Link state (pending, active, closed)")

            # RTT if available
            rtt = metadata.get("rtt_ms")
            if rtt is not None:
                tree.add_field(link, "RTT", "rns.link.rtt", rtt, FieldType.FLOAT,
                              "Round-trip time in milliseconds")

        # LXMF-specific fields
        if packet.rns_service and "lxmf" in packet.rns_service.lower():
            lxmf = PacketField(name="LXMF", abbrev="rns.lxmf",
                              value=None, field_type=FieldType.NESTED)
            rns.children.append(lxmf)

            # Message fields if available
            lxmf_title = metadata.get("lxmf_title", metadata.get("title"))
            lxmf_content = metadata.get("lxmf_content", metadata.get("content"))
            lxmf_stamp = metadata.get("lxmf_stamp")

            if lxmf_title:
                tree.add_field(lxmf, "Title", "rns.lxmf.title", lxmf_title, FieldType.STRING)
            if lxmf_content:
                preview = lxmf_content[:100] + "..." if len(lxmf_content) > 100 else lxmf_content
                tree.add_field(lxmf, "Content Preview", "rns.lxmf.content", preview, FieldType.STRING)
            if lxmf_stamp:
                tree.add_field(lxmf, "Stamp", "rns.lxmf.stamp", lxmf_stamp, FieldType.STRING)

        # Payload size
        payload_size = metadata.get("payload_size", len(raw_data) - 19 if len(raw_data) > 19 else 0)
        if payload_size > 0:
            tree.add_field(rns, "Payload Size", "rns.payload_size", payload_size, FieldType.INTEGER,
                          "Data payload size in bytes")

        return tree


# =============================================================================
# DISPLAY FILTER (FILTERING SYSTEM)
# =============================================================================

class DisplayFilter:
    """
    Wireshark-style display filter for packets.

    Supports expressions like:
    - mesh.hops > 2
    - mesh.from == "!abc123"
    - mesh.snr >= -5 and mesh.portnum == 1
    - rns.hops <= 3 or mesh.mqtt == true
    """

    # Regex for parsing filter expressions
    FIELD_PATTERN = re.compile(
        r'(\w+\.\w+(?:\.\w+)?)\s*(==|!=|>=|<=|>|<|contains|matches)\s*'
        r'(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
    )

    def __init__(self, expression: str = ""):
        self.expression = expression.strip()
        self._compiled: Optional[List[Tuple[str, str, str, str]]] = None

    def compile(self) -> bool:
        """Compile the filter expression."""
        if not self.expression:
            self._compiled = []
            return True

        # Split on 'and' / 'or' (simple implementation)
        # For now, treat all conditions as AND
        expr = self.expression.lower()
        expr = re.sub(r'\s+and\s+', ' AND ', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\s+or\s+', ' OR ', expr, flags=re.IGNORECASE)

        parts = []
        current_logic = "AND"

        # Parse each condition
        for match in self.FIELD_PATTERN.finditer(self.expression):
            field_name = match.group(1)
            operator = match.group(2)
            value = match.group(3) or match.group(4) or match.group(5)
            parts.append((field_name, operator, value, current_logic))

        self._compiled = parts
        return len(parts) > 0 or not self.expression

    def matches(self, packet: MeshPacket) -> bool:
        """Check if packet matches the filter."""
        if self._compiled is None:
            self.compile()

        if not self._compiled:
            return True  # Empty filter matches all

        if not packet.tree:
            return False

        # Check all conditions (simple AND logic for now)
        for field_name, operator, value, logic in self._compiled:
            field = packet.tree.get_field(field_name)
            if field is None:
                # Field not found - doesn't match
                return False
            if not field.matches_filter(operator, value):
                return False

        return True

    @classmethod
    def get_available_fields(cls) -> Dict[str, str]:
        """Get list of available filter fields."""
        return {
            # Frame fields
            "frame.time": "Packet timestamp",
            "frame.direction": "Packet direction (inbound/outbound)",
            "frame.size": "Packet size in bytes",
            "frame.protocol": "Protocol (meshtastic/rns)",

            # Meshtastic fields
            "mesh.from": "Source node ID",
            "mesh.to": "Destination node ID",
            "mesh.channel": "Channel number",
            "mesh.hop_limit": "Remaining hops",
            "mesh.hop_start": "Original hop limit",
            "mesh.hops": "Hops taken",
            "mesh.mqtt": "Via MQTT (true/false)",
            "mesh.ack": "ACK requested (true/false)",
            "mesh.portnum": "Port number",
            "mesh.port": "Port name",
            "mesh.snr": "Signal-to-Noise Ratio",
            "mesh.rssi": "Received Signal Strength",
            "mesh.text": "Text message content",

            # RNS fields
            "rns.type": "RNS packet type (DATA/ANNOUNCE/LINK_*/PATH_*)",
            "rns.dest_hash": "Destination hash (16-byte hex)",
            "rns.source_hash": "Source identity hash",
            "rns.hops": "Hop count traversed",
            "rns.ttl": "Remaining time-to-live",
            "rns.interface": "Interface name",
            "rns.iface_type": "Interface type (TCP/UDP/LoRa/etc.)",
            "rns.service": "Service type/aspect",
            "rns.context": "Context byte value",
            "rns.payload_size": "Payload size in bytes",
            # RNS Header fields
            "rns.header.flags": "Header flags",
            "rns.header.hops": "Header hop count",
            "rns.header.type": "Packet type byte",
            # RNS Announce fields
            "rns.announce.aspect": "Announce aspect filter",
            "rns.announce.identity": "Announced identity hash",
            "rns.announce.name": "Announced display name",
            "rns.announce.app_size": "App data size",
            # RNS Link fields
            "rns.link.id": "Link identifier",
            "rns.link.state": "Link state (pending/active/closed)",
            "rns.link.rtt": "Round-trip time (ms)",
            # LXMF fields
            "rns.lxmf.title": "LXMF message title",
            "rns.lxmf.content": "LXMF message content preview",
            "rns.lxmf.stamp": "LXMF timestamp stamp",
        }

"""
Knowledge guides extracted from knowledge_content.py for CLAUDE.md #6 compliance.

Contains: load_troubleshooting_guides, load_best_practices, load_aredn_knowledge.
Split to keep knowledge_content.py under the 1,500-line threshold.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .knowledge_base import KnowledgeBase

from .knowledge_base import (
    KnowledgeEntry,
    KnowledgeTopic,
    TroubleshootingGuide,
    TroubleshootingStep,
)


def load_troubleshooting_guides(kb: "KnowledgeBase") -> None:
    """Load troubleshooting guides."""

    kb._add_guide(TroubleshootingGuide(
        problem="no_connection_meshtasticd",
        description="Cannot connect to meshtasticd service",
        prerequisites=["meshtasticd installed", "Meshtastic device connected"],
        steps=[
            TroubleshootingStep(
                instruction="Check if meshtasticd is running",
                command="sudo systemctl status meshtasticd",
                expected_result="Active: active (running)",
                if_fail="Start the service: sudo systemctl start meshtasticd",
            ),
            TroubleshootingStep(
                instruction="Check if port 4403 is listening",
                command="ss -tlnp | grep 4403",
                expected_result="LISTEN ... :4403",
                if_fail="Service may have crashed, check logs",
            ),
            TroubleshootingStep(
                instruction="Check for other clients",
                command="ss -tnp | grep 4403",
                expected_result="No established connections or only MeshForge",
                if_fail="Another client is connected, close it first",
            ),
            TroubleshootingStep(
                instruction="Check meshtasticd logs for errors",
                command="journalctl -u meshtasticd -n 50",
                expected_result="No ERROR or CRITICAL messages",
            ),
            TroubleshootingStep(
                instruction="Restart meshtasticd and try again",
                command="sudo systemctl restart meshtasticd",
            ),
        ],
        related_problems=["serial_port_issues", "device_not_found"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="weak_signal",
        description="Nodes have weak signal (low SNR/RSSI)",
        prerequisites=["Nodes are powered on", "Basic connectivity exists"],
        steps=[
            TroubleshootingStep(
                instruction="Check current SNR and RSSI values",
                command="meshtastic --nodes",
                expected_result="SNR > -10, RSSI > -110",
            ),
            TroubleshootingStep(
                instruction="Verify antenna is properly connected",
                expected_result="Antenna screwed on tightly, correct frequency band",
                if_fail="Transmitting without antenna can damage radio!",
            ),
            TroubleshootingStep(
                instruction="Check antenna orientation",
                expected_result="Antenna vertical for maximum range",
                if_fail="Horizontal antennas have different pattern",
            ),
            TroubleshootingStep(
                instruction="Increase antenna height if possible",
                expected_result="Even 1-2 meters height can double range",
            ),
            TroubleshootingStep(
                instruction="Check for obstructions in RF path",
                expected_result="Clear line of sight to other node",
                if_fail="Consider relay node or better antenna placement",
            ),
            TroubleshootingStep(
                instruction="Consider changing modem preset for more range",
                command="meshtastic --set lora.modem_preset LONG_SLOW",
                expected_result="Longer range but slower data rate",
            ),
        ],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="high_channel_utilization",
        description="Channel utilization consistently above 50%",
        steps=[
            TroubleshootingStep(
                instruction="Check current channel utilization",
                command="meshtastic --info | grep -i util",
                expected_result="Channel utilization < 25%",
            ),
            TroubleshootingStep(
                instruction="Identify message sources",
                expected_result="Determine which nodes are sending most traffic",
            ),
            TroubleshootingStep(
                instruction="Reduce position broadcast rate",
                command="meshtastic --set position.position_broadcast_secs 900",
                expected_result="Position updates every 15 minutes instead of default",
            ),
            TroubleshootingStep(
                instruction="Use faster modem preset if range allows",
                command="meshtastic --set lora.modem_preset MEDIUM_FAST",
                expected_result="Shorter air time per message",
            ),
            TroubleshootingStep(
                instruction="Move high-volume traffic to MQTT",
                expected_result="Telemetry via MQTT reduces RF usage",
            ),
        ],
    ))


def load_best_practices(kb: "KnowledgeBase") -> None:
    """Load best practices knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.BEST_PRACTICES,
        title="MeshForge Deployment Best Practices",
        content="""
Recommended practices for MeshForge deployment:

NETWORK DESIGN:
1. Start with minimum viable mesh (3-4 nodes)
2. Test RF links before adding complexity
3. Place router nodes at high points
4. Use MQTT for internet connectivity

GATEWAY CONFIGURATION:
1. Run MeshForge on stable power (not battery)
2. Use wired Ethernet when possible
3. Configure reasonable queue sizes
4. Enable message persistence

MONITORING:
1. Check diagnostic panel regularly
2. Set up alerts for critical issues
3. Monitor channel utilization
4. Track node battery levels

SECURITY:
1. Change default channel keys
2. Use TLS for MQTT connections
3. Don't expose services to internet directly
4. Keep firmware updated

RELIABILITY:
1. Test failover scenarios
2. Have backup power (UPS)
3. Document your configuration
4. Regular backups of config files
""",
        keywords=["best practices", "deployment", "setup", "configuration", "security"],
        expertise_level="intermediate",
    ))


def load_aredn_knowledge(kb: "KnowledgeBase") -> None:
    """Load AREDN (Amateur Radio Emergency Data Network) knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Network Overview",
        content="""
AREDN (Amateur Radio Emergency Data Network) is a mesh network using
modified WiFi routers on ham radio frequencies.

Key Differences from Meshtastic:
- Uses WiFi hardware (802.11), not LoRa
- Much higher bandwidth (Mbps vs kbps)
- Shorter range per hop (typically 1-5 km)
- Requires ham radio license (Technician or higher)
- Operates on 2.4 GHz, 5.8 GHz, or 3.4 GHz bands

Network Architecture:
- Nodes are modified WiFi routers (Ubiquiti, Mikrotik, GL.iNet)
- OLSR routing protocol (automatic mesh routing)
- Each node has mesh RF + local LAN ports
- Services hosted on connected computers (chat, VoIP, video)

For MeshForge:
- AREDN is a MONITORING target, not a bridge
- MeshForge discovers AREDN nodes via OLSR data
- Read-only: MeshForge does not inject traffic
- Useful for operators managing both networks

AREDN API:
- Each node has web UI at http://localnode.local.mesh
- OLSR topology: http://node.local.mesh:9090/links
- Node list: http://node.local.mesh:8080/cgi-bin/sysinfo.json
""",
        keywords=["aredn", "amateur radio", "emergency", "wifi", "olsr", "mesh", "ham"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Node Discovery",
        content="""
MeshForge discovers AREDN nodes through the OLSR protocol.

OLSR (Optimized Link State Routing):
- Proactive routing protocol for mobile ad-hoc networks
- Nodes broadcast topology information
- Each node maintains full network map
- Uses Multi-Point Relays (MPR) to reduce flooding

Discovery Methods:

1. OLSR Topology Data:
   - URL: http://<node>:9090/links
   - Returns JSON with link quality, neighbor list
   - Updated every 2-10 seconds

2. Node System Info:
   - URL: http://<node>:8080/cgi-bin/sysinfo.json
   - Returns: hostname, firmware, services, GPS position
   - Rich data for map display

3. Network-wide scan:
   - Query one node's OLSR for all known hosts
   - Walk the topology to discover entire network
   - Typically completes in seconds (IP-based, fast)

MeshForge Integration:
- Polls AREDN nodes periodically (configurable interval)
- Extracts: node names, positions, link quality, services
- Displays on map alongside Meshtastic nodes
- Different icon/color to distinguish network types

Limitations:
- Must be on same network (direct or tunnel)
- AREDN nodes that block API access won't be discovered
- GPS data optional (many AREDN nodes don't have GPS)
""",
        keywords=["aredn", "olsr", "discovery", "topology", "scan", "api"],
        related_entries=["AREDN Network Overview"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.NETWORKING,
        title="AREDN Services",
        content="""
AREDN nodes can host and access various network services.

Common Services:
- Chat: MeshChat (web-based group messaging)
- VoIP: Asterisk PBX for voice calls
- Video: IP cameras and streaming
- File sharing: FTP/SFTP servers
- Web: Hosted websites and dashboards

Service Advertisement:
- Nodes advertise services in OLSR data
- Format: protocol://host:port/path
- Other nodes auto-discover available services
- Accessible from any node on the mesh

For Emergency Communications:
- Voice: Multiple VoIP servers for redundancy
- Messaging: MeshChat for text-based coordination
- Situational Awareness: Shared maps and status boards
- Infrastructure: DNS, NTP, monitoring

MeshForge can display:
- Which services are available on which nodes
- Service uptime/availability
- Network paths to service nodes
- This is read-only monitoring

Hardware Needed:
- Ubiquiti NanoStation (sector), Rocket (backbone)
- Mikrotik hAP ac3 (node+services)
- GL.iNet (compact, low power)
- Any device with AREDN firmware support
""",
        keywords=["aredn", "services", "voip", "chat", "meshchat", "video", "emergency"],
        related_entries=["AREDN Network Overview", "AREDN Node Discovery"],
        expertise_level="intermediate",
    ))

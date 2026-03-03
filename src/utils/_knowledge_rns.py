"""
Reticulum/RNS knowledge content for MeshForge Knowledge Base.

Extracted from knowledge_content.py for CLAUDE.md #6 compliance (<1,500 lines).
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


def load_reticulum_knowledge(kb: "KnowledgeBase") -> None:
    """Load Reticulum-specific knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="Reticulum Network Stack",
        content="""
Reticulum (RNS) is a cryptographic networking stack for reliable communication
over high-latency, low-bandwidth links.

Key concepts:
- Identity-based addressing (no IP addresses)
- End-to-end encryption by default
- Works over any transport (LoRa, TCP, I2P, etc.)
- Automatic routing and path discovery

Components:
- rnsd: Reticulum daemon
- nomadnet: Text-based messaging app
- LXMF: Messaging format
- Sideband: Mobile app

For MeshForge:
- RNS provides the "other" mesh network
- Gateway bridges Meshtastic ↔ RNS
- Different addressing schemes (hash vs node ID)
""",
        keywords=["reticulum", "rns", "cryptographic", "lxmf", "nomadnet", "identity"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Interfaces",
        content="""
Reticulum supports multiple transport interfaces.

TCPClientInterface:
- Connect to remote RNS node via TCP
- Used for internet bridging
- Config: target_host, target_port

TCPServerInterface:
- Accept incoming TCP connections
- Run as hub for other nodes

SerialInterface:
- Direct serial connection
- For LoRa modems, packet radio

RNodeInterface:
- For RNode hardware (LoRa modem)
- Most common for RF mesh

LocalInterface:
- Loopback for local apps
- Always enabled

AutoInterface:
- Automatic peer discovery
- Uses UDP multicast on LAN
- Great for local testing

Configuration in: ~/.reticulum/config
""",
        keywords=["interface", "tcp", "serial", "rnode", "transport", "config"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Cryptography",
        content="""
Reticulum uses strong, well-established cryptographic primitives:

Identity & Addressing:
- 512-bit Curve25519 keysets (Ed25519 + X25519)
- No source addresses on packets (initiator anonymity)
- Destination addresses are cryptographic hashes
- Globally unique without central coordination

Encryption:
- AES-256-CBC encryption with PKCS7 padding
- HMAC-SHA256 for authentication
- Forward secrecy via ephemeral ECDH exchanges
- Per-packet keys for privacy

Link Establishment:
- Only 3 packets (297 bytes) to establish encrypted link
- Link overhead: 0.44 bits per second
- Unforgeable delivery confirmations

This means:
- Messages are encrypted end-to-end by default
- No trust in network infrastructure required
- Even relay nodes cannot read message contents
- Identity is provable via cryptographic signatures

For MeshForge gateway:
- Each side maintains its own identity
- Bridge must have valid RNS identity to participate
- Messages re-encrypted across network boundary
""",
        keywords=["cryptography", "encryption", "aes", "curve25519", "ed25519", "identity", "security"],
        related_entries=["Reticulum Network Stack"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Node Discovery",
        content="""
Reticulum uses an announce-based discovery system:

Announces:
- Nodes broadcast their identity and destination hash
- Public key shared for others to route to you
- App data can include display name, capabilities
- Without announcing, you are invisible on the network

Path Discovery:
- Automatic multi-hop path finding
- Transport layer maintains path table
- Paths expire and refresh automatically
- No central routing authority

Network Visualizer (like MeshChat):
- Shows announced nodes and their connectivity
- Tracks path hops to each destination
- Displays announce timestamps
- Helps understand network topology

Bootstrap:
- New nodes connect to known peers
- Temporary bootstrap links discover local infrastructure
- System automatically forms stronger direct links
- Bootstrap connections can be discarded after discovery

For MeshForge:
- Use list_known_destinations() to see known nodes
- Use discover_nodes() for active discovery
- Monitor Transport.path_table for topology
- Check Identity.known_destinations for all seen nodes
""",
        keywords=["discovery", "announce", "path", "routing", "bootstrap", "visualizer", "topology"],
        related_entries=["RNS Interfaces"],
        expertise_level="intermediate",
    ))


def load_rns_troubleshooting(kb: "KnowledgeBase") -> None:
    """Load RNS troubleshooting knowledge."""

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Identity Management",
        content="""
RNS identities are persistent cryptographic keysets stored on disk.

Identity Location:
- Default: ~/.reticulum/storage/identities/
- Each identity is a 512-bit Curve25519 keyset
- Identity hash = first 128 bits of SHA-256 of public key
- This hash IS the network address

Creating Identity:
  import RNS
  identity = RNS.Identity()  # Generates new keypair
  identity.to_file("/path/to/identity")

Loading Identity:
  identity = RNS.Identity.from_file("/path/to/identity")

Common Issues:
1. Lost identity file = lost network address
   - Other nodes can't reach you at old address
   - Must re-announce with new identity
   - Solution: Back up identity files!

2. Duplicate identity (cloned SD card):
   - Two nodes with same keys = routing confusion
   - Delete identity on one and restart rnsd
   - Fresh identity will be generated

3. Identity not announcing:
   - Check destination is registered
   - Verify rnsd is running
   - Check interface connectivity

For MeshForge:
- Gateway bridge needs stable identity
- Back up: ~/.reticulum/storage/
- Identity hash displayed in bridge status
""",
        keywords=["identity", "keys", "address", "hash", "announce", "backup", "cryptographic"],
        related_entries=["RNS Cryptography", "RNS Node Discovery"],
        expertise_level="intermediate",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="RNS Transport and Routing",
        content="""
RNS Transport handles multi-hop routing across heterogeneous networks.

Transport Nodes:
- Regular node: Only communicates with direct neighbors
- Transport node: Relays traffic between non-adjacent nodes
- Enable transport: transport_enabled = Yes in config

Path Table:
- Maintained automatically by Transport layer
- Entries: destination_hash -> next_hop_interface
- Paths expire after 2 hours (configurable)
- Refreshed by announces and traffic

Routing Process:
1. Source sends packet with destination hash
2. Each transport node checks path table
3. If path known: forward to next hop
4. If unknown: packet is dropped (no flooding)

Path Discovery:
- Passive: Listen for announces
- Active: Transport.request_path(destination_hash)
- Path requests propagate through transport network
- Response contains full path back

Rate Limiting:
- Announces rate-limited to prevent flooding
- Default: 1 announce per 600 seconds per destination
- Can be adjusted but don't set too low

For MeshForge gateway:
- Should run as transport node for better connectivity
- Monitor Transport.path_table for network topology
- High path_table churn = network instability
""",
        keywords=["transport", "routing", "path", "hop", "relay", "table", "forward"],
        related_entries=["RNS Node Discovery", "RNS Interfaces"],
        expertise_level="expert",
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="LXMF Message Protocol",
        content="""
LXMF (Lightweight Extensible Message Format) is the messaging layer on RNS.

Message Types:
- Single packet: Small messages (<500 bytes), delivered directly
- Resource transfer: Larger messages, uses RNS Links for reliable delivery
- Propagation: Messages stored at intermediate nodes for offline recipients

Delivery Modes:
1. Direct: Source → Destination (both must be online)
2. Propagated: Source → Propagation Node → Destination (async delivery)

Propagation Nodes:
- Store messages for offline destinations
- Forward when destination comes online
- Message TTL (time to live) prevents indefinite storage
- Multiple propagation nodes for redundancy

Message Structure:
- Source identity (sender)
- Destination identity (recipient)
- Timestamp
- Content (plaintext or encrypted payload)
- Signature (proves sender authenticity)

For MeshForge bridge:
- Meshtastic messages converted to LXMF format
- DeliveryTracker monitors confirmation callbacks
- Timeout = assume delivery failed
- Queue re-attempts automatically

Common Issues:
- Message never delivered: Destination offline + no propagation node
- Duplicate messages: Retry logic without deduplication
- Large messages fail: Split into chunks or use resource transfer
""",
        keywords=["lxmf", "message", "delivery", "propagation", "offline", "format"],
        related_entries=["Reticulum Network Stack", "RNS Transport and Routing"],
        expertise_level="intermediate",
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rnsd_not_starting",
        description="rnsd daemon fails to start or crashes on startup",
        prerequisites=["Reticulum installed", "Python 3 available"],
        steps=[
            TroubleshootingStep(
                instruction="Check rnsd service status",
                command="sudo systemctl status rnsd",
                expected_result="Active: active (running)",
                if_fail="Check error message in status output",
            ),
            TroubleshootingStep(
                instruction="Check for config file errors",
                command="cat ~/.reticulum/config",
                expected_result="Valid YAML/config format with interfaces defined",
                if_fail="Delete config and restart — fresh config will be generated",
            ),
            TroubleshootingStep(
                instruction="Verify Python RNS package is installed",
                command="python3 -c 'import RNS; print(RNS.__version__)'",
                expected_result="Version number printed (e.g., 0.7.3)",
                if_fail="Install: pipx install rns",
            ),
            TroubleshootingStep(
                instruction="Check for port conflicts on AutoInterface",
                command="ss -ulnp | grep 29716",
                expected_result="Nothing or only rnsd using the port",
                if_fail="Kill conflicting process: kill <PID>",
            ),
            TroubleshootingStep(
                instruction="Check interface device exists",
                command="ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null",
                expected_result="Device file exists if using SerialInterface",
                if_fail="Connect device and check dmesg for USB errors",
            ),
            TroubleshootingStep(
                instruction="Try running rnsd in foreground for debug output",
                command="rnsd -v",
                expected_result="Verbose output showing interface initialization",
            ),
        ],
        related_problems=["no_connection_meshtasticd", "serial_port_issues"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rnsd_ratchets_permission",
        description="rnsd crashes with PermissionError on /etc/reticulum/storage/ subdirectories (ratchets, resources, cache)",
        prerequisites=["rnsd installed", "Using system-wide config at /etc/reticulum/"],
        steps=[
            TroubleshootingStep(
                instruction="Check if required storage subdirectories exist",
                command="ls -la /etc/reticulum/storage/{ratchets,resources,cache/announces}",
                expected_result="All directories exist with write permissions",
                if_fail="One or more directories missing — RNS needs ratchets/ (key ratcheting), resources/ (resource storage), cache/announces/ (transport)",
            ),
            TroubleshootingStep(
                instruction="Create all required directories with correct permissions",
                command="sudo mkdir -p /etc/reticulum/storage/{ratchets,resources,cache/announces} && sudo chmod 755 /etc/reticulum/storage/{ratchets,resources,cache/announces}",
                expected_result="Directories created successfully",
                if_fail="Check filesystem is not mounted read-only",
            ),
            TroubleshootingStep(
                instruction="Restart rnsd to verify the fix",
                command="sudo systemctl restart rnsd",
                expected_result="Active: active (running)",
                if_fail="Check journalctl -u rnsd for other errors",
            ),
        ],
        related_problems=["rnsd_not_starting"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_path_failure",
        description="Cannot reach RNS destination — path not found",
        prerequisites=["rnsd running", "At least one interface active"],
        steps=[
            TroubleshootingStep(
                instruction="Check if destination has announced recently",
                command="rnpath <destination_hash>",
                expected_result="Path found with hop count",
                if_fail="Destination may be offline or out of range",
            ),
            TroubleshootingStep(
                instruction="Check your interfaces are active",
                command="rnstatus",
                expected_result="Interfaces shown with RX/TX byte counts",
                if_fail="Interface may be misconfigured or disconnected",
            ),
            TroubleshootingStep(
                instruction="Verify transport nodes are available",
                expected_result="At least one transport node should be reachable",
                if_fail="Run a transport node yourself or find one on the network",
            ),
            TroubleshootingStep(
                instruction="Wait for path discovery (especially on mesh)",
                expected_result="Paths can take minutes to propagate on LoRa",
                if_fail="Try requesting path explicitly: rnpath -r <hash>",
            ),
            TroubleshootingStep(
                instruction="Check if announce is reaching network",
                command="rnid -a",
                expected_result="Announce sent successfully",
            ),
        ],
        related_problems=["rnsd_not_starting", "weak_signal"],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_interface_config",
        description="RNS interface configuration issues",
        prerequisites=["rnsd installed", "Hardware connected"],
        steps=[
            TroubleshootingStep(
                instruction="Generate fresh default config if needed",
                command="rnsd --config-generate",
                expected_result="Config file created at ~/.reticulum/config",
            ),
            TroubleshootingStep(
                instruction="For RNode: verify device detection",
                command="rnodeconf -a /dev/ttyUSB0",
                expected_result="RNode info displayed",
                if_fail="Device may not be an RNode — check firmware",
            ),
            TroubleshootingStep(
                instruction="For TCP interface: check connectivity",
                command="nc -zv <host> <port>",
                expected_result="Connection succeeded",
                if_fail="Check host:port and network/firewall",
            ),
            TroubleshootingStep(
                instruction="Verify config syntax (common YAML errors)",
                expected_result="Correct indentation (2 spaces), no tabs",
                if_fail="YAML is whitespace-sensitive — check indentation",
            ),
            TroubleshootingStep(
                instruction="Check interface enabled flag",
                expected_result="interface_enabled = True for each interface",
                if_fail="Set interface_enabled = True and restart rnsd",
            ),
        ],
    ))

    # --- NomadNet / rnsd coexistence ---

    kb._add_guide(TroubleshootingGuide(
        problem="nomadnet_rnsd_coexistence",
        description=(
            "NomadNet and rnsd competing for shared instance "
            "port 37428"
        ),
        prerequisites=["rnsd installed", "NomadNet installed"],
        steps=[
            TroubleshootingStep(
                instruction="Check who owns port 37428",
                command="sudo ss -ulnp | grep 37428",
                expected_result="rnsd should own the port",
                if_fail=(
                    "If NomadNet owns it, startup order is wrong"
                ),
            ),
            TroubleshootingStep(
                instruction="Stop NomadNet",
                command="pkill -f nomadnet",
                expected_result="NomadNet processes terminated",
            ),
            TroubleshootingStep(
                instruction="Restart rnsd so it claims the port",
                command="sudo systemctl restart rnsd",
                expected_result="Active: active (running)",
                if_fail=(
                    "Check journalctl -u rnsd for errors"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Verify rnsd owns port 37428"
                ),
                command="sudo ss -ulnp | grep 37428",
                expected_result="rnsd shown as port owner",
                if_fail=(
                    "Check share_instance = Yes in config"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Start NomadNet (connects as client)"
                ),
                command="nomadnet --daemon",
                expected_result=(
                    "NomadNet connects to rnsd shared instance"
                ),
                if_fail=(
                    "Check NomadNet logfile: "
                    "~/.nomadnetwork/logfile"
                ),
            ),
        ],
        related_problems=[
            "rnsd_not_starting", "rns_path_failure",
        ],
    ))

    kb._add_guide(TroubleshootingGuide(
        problem="rns_interface_rx_only",
        description=(
            "RNS interfaces show RX traffic but zero TX — "
            "link establishment failing"
        ),
        prerequisites=[
            "rnsd running",
            "At least one interface enabled",
        ],
        steps=[
            TroubleshootingStep(
                instruction=(
                    "Check interface TX/RX counters"
                ),
                command="rnstatus",
                expected_result=(
                    "Both TX and RX byte counts should be "
                    "non-zero"
                ),
                if_fail=(
                    "Interfaces with 0 TX cannot establish "
                    "links"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Check if shared instance port is "
                    "listening"
                ),
                command="sudo ss -ulnp | grep 37428",
                expected_result=(
                    "rnsd bound to port 37428"
                ),
                if_fail=(
                    "rnsd may not have finished initializing, "
                    "or NomadNet may be holding the port"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Check for blocking interfaces in config"
                ),
                expected_result=(
                    "All enabled interfaces have dependencies "
                    "met"
                ),
                if_fail=(
                    "Disable the blocking interface or start "
                    "its dependency"
                ),
            ),
            TroubleshootingStep(
                instruction=(
                    "Restart rnsd to reinitialize interfaces"
                ),
                command="sudo systemctl restart rnsd",
                expected_result=(
                    "Active: active (running), then rnstatus "
                    "shows TX > 0"
                ),
            ),
        ],
        related_problems=[
            "nomadnet_rnsd_coexistence",
            "rnsd_not_starting",
        ],
    ))

    kb._add_entry(KnowledgeEntry(
        topic=KnowledgeTopic.RETICULUM,
        title="NomadNet and rnsd Coexistence",
        content="""
NomadNet and rnsd both create Reticulum instances. When both set
share_instance = Yes, they compete for UDP port 37428.

Correct startup order:
1. rnsd starts first, binds port 37428 (shared instance)
2. NomadNet starts second, detects rnsd and connects as client
3. MeshForge gateway connects as another client

If NomadNet starts first:
- NomadNet binds port 37428
- rnsd fails to bind, enters crash loop
- MeshForge gateway may connect to NomadNet instead of rnsd
- Some interfaces (Meshtastic_Interface) only available via rnsd

Diagnosis:
- sudo ss -ulnp | grep 37428 -- shows who owns the port
- RX-only interfaces in rnstatus -- rnsd initialized but can't TX
- NomadNet logfile: ~/.nomadnetwork/logfile

Fix:
- Stop NomadNet: pkill -f nomadnet
- Restart rnsd: sudo systemctl restart rnsd
- Wait for port 37428, then start NomadNet
- For boot: set rnsd to start before NomadNet in systemd
""",
        keywords=[
            "nomadnet", "rnsd", "coexistence", "port", "37428",
            "shared instance", "conflict", "startup order",
        ],
        related_entries=[
            "Reticulum Network Stack",
            "RNS Transport and Routing",
        ],
        expertise_level="intermediate",
    ))



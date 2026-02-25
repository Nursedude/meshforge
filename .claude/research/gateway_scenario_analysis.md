# MeshForge Gateway Scenario Analysis: Multi-Protocol Bridging Deep Dive

**Date**: 2026-02-22
**Author**: Dude AI / WH6GXZ
**Status**: Research Analysis
**Branch**: claude/meshforge-gateway-analysis-3zVJt

---

## 1. Executive Summary

MeshForge operates as a **Network Operations Center (NOC)** bridging three fundamentally incompatible mesh ecosystems: **Meshtastic**, **RNS/Reticulum**, and **MeshCore**. The gateway subsystem (`src/gateway/`) implements 6 bridge modes that handle the protocol translation, addressing incompatibilities, and physical layer differences between these networks.

**Key findings from this analysis:**

1. **Hash/addressing is the hard problem** -- Meshtastic uses 32-bit sequential IDs, RNS uses 128-bit SHA-256 hashes of Ed25519 keys, MeshCore uses 16-bit addresses from non-standard Ed25519 derivatives. These CANNOT be mapped. MeshForge solves this with network-prefixed IDs and a protocol-agnostic `CanonicalMessage` layer.

2. **RF incompatibility is by design** -- Even within the same protocol family (Meshtastic), different LoRa presets use different spreading factors and bandwidths. Between protocols (Meshtastic vs MeshCore), frequencies, sync words, and modulation are all different. Bridging MUST happen at the application layer with multiple radios.

3. **Four gateway scenarios analyzed** -- Short Turbo<>Long Fast (implemented), Meshtastic<>MeshCore (alpha), LoRa<>WiFi (partially exists), Meshtastic<>RNS (production). Each has distinct challenges around addressing, payload sizes, and transport.

4. **MeshLinkFoundation WiFi gateway pattern** -- Their hostapd/dnsmasq/iptables stack is directly applicable to MeshForge's LoRa<>WiFi scenario as a WiFi transport backend. Their captive portal pattern could surface mesh network access to WiFi clients.

---

## 2. MeshForge Gateway Architecture (Current State)

### 2.1 Bridge Modes

MeshForge supports 6 bridge modes configured via `bridge_mode` in `GatewayConfig` (`src/gateway/config.py:370-378`):

| Mode | Transport | Protocol Pair | Status |
|------|-----------|---------------|--------|
| `mqtt_bridge` | MQTT (recommended) | Meshtastic <> RNS | Production (main) |
| `message_bridge` | TCP (legacy) | Meshtastic <> RNS | Production (main) |
| `rns_transport` | LoRa via Meshtastic | RNS over Meshtastic | Production (main) |
| `mesh_bridge` | TCP (dual instance) | Meshtastic <> Meshtastic | Production (main) |
| `meshcore_bridge` | Serial/TCP/BLE | MeshCore <> Meshtastic/RNS | Alpha branch |
| `tri_bridge` | All transports | All 3 protocols | Alpha branch |

### 2.2 Translation Layer

The `CanonicalMessage` (`src/gateway/canonical_message.py:57-78`) serves as the protocol-agnostic intermediate format:

```
                  from_meshtastic()        to_meshcore_text()
  Meshtastic ─────────────────> CanonicalMessage ─────────────────> MeshCore
  MeshCore   ─────────────────> CanonicalMessage ─────────────────> Meshtastic
  RNS/LXMF  ─────────────────> CanonicalMessage ─────────────────> RNS/LXMF
                  from_meshcore()            to_meshtastic()
                  from_rns()                 to_rns()
```

This reduces N*(N-1) conversion paths to 2*N (6 methods for 3 protocols instead of 6 bidirectional converters). Source: `canonical_message.py:5-6`.

### 2.3 Routing Engine

The `MessageRouter` (`src/gateway/message_routing.py:38-64`) implements direction-based routing with two classification modes:

1. **Confidence-scored classifier** (when `utils.classifier` available) -- ML-style scoring
2. **Legacy regex-based rules** -- Pattern matching fallback

Direction map (`message_routing.py:55-64`):
```
bidirectional      -> Any direction
mesh_to_rns        -> (meshtastic, rns)
rns_to_mesh        -> (rns, meshtastic)
mesh_to_meshcore   -> (meshtastic, meshcore)
meshcore_to_mesh   -> (meshcore, meshtastic)
rns_to_meshcore    -> (rns, meshcore)
meshcore_to_rns    -> (meshcore, rns)
all_to_all         -> Any direction
```

---

## 3. Gateway Scenario Analysis

### 3.1 Scenario A: Meshtastic Short Turbo <> MeshForge <> Long Fast

**Status: IMPLEMENTED** (`src/gateway/mesh_bridge.py`, 531 lines)

```
 ┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
 │  SHORT TURBO     │         │    MeshForge      │         │   LONG FAST      │
 │  Local Mesh      │◄───────►│  Preset Bridge    │◄───────►│   Rural Mesh     │
 │  ~500 B/s        │  TCP    │  (mesh_bridge.py) │  TCP    │   ~50 B/s        │
 │  Short range     │ :4404   │  Dedup + Queue    │ :4403   │   Max range      │
 │  SF5, 500kHz BW  │         │  Prefix tagging   │         │   SF11, 250kHz   │
 └──────────────────┘         └──────────────────┘         └──────────────────┘
       Radio #2                   Raspberry Pi                   Radio #1
```

#### How It Works

1. Two `meshtasticd` instances run on different TCP ports (default 4403/4404)
2. Each instance controls a radio configured for its LoRa preset
3. `MeshtasticPresetBridge` (`mesh_bridge.py:53`) connects to both via TCP
4. Messages received on one preset are queued for forwarding to the other
5. Dedup via content hash prevents forwarding loops (`mesh_bridge.py:47-50`)
6. Optional prefix tags identify bridged messages: `[LONG_FAST] Hello` (`mesh_bridge.py:480-486`)

#### Hash/Addressing Implications

**No translation needed.** Both sides are Meshtastic -- same 32-bit `!aabbccdd` node IDs, same addressing scheme. The bridge simply re-transmits the text content; the `fromId` changes to the gateway's own node ID on the destination network.

#### Physical Layer Mismatch

| Parameter | SHORT_TURBO | LONG_FAST | Impact |
|-----------|-------------|-----------|--------|
| Throughput | ~500 B/s | ~50 B/s | 10x flow control needed |
| Delay per msg | 0.4s | 8.0s | Bridge buffers fast-side bursts |
| Range | Short (~1 km urban) | Maximum (~15+ km LOS) | Different coverage areas |
| Spreading Factor | SF5 | SF11 | Incompatible modulation |
| Bandwidth | 500 kHz | 250 kHz | Different channel widths |

Source: `config.py:325-338` (`RNSOverMeshtasticConfig.get_throughput_estimate()`)

#### Throughput Asymmetry

The 10x speed difference is the main challenge. When the Short Turbo side bursts 10 messages in 4 seconds, the Long Fast side needs 80 seconds to transmit them all. The bridge's `Queue(maxsize=1000)` (`mesh_bridge.py:75-76`) absorbs bursts, but sustained high traffic from the fast side will overflow.

**Recommendation**: Add persistent queue integration (SQLite via `message_queue.py`) for the preset bridge to survive restarts and handle sustained asymmetry.

#### Configuration

Template available at `config.py:811-847`:
```python
config = GatewayConfig.template_dual_preset_bridge(
    primary_port=4403,      # Long Fast radio
    secondary_port=4404,    # Short Turbo radio
    primary_preset="LONG_FAST",
    secondary_preset="SHORT_TURBO"
)
```

#### Current Gaps

| Gap | Severity | Notes |
|-----|----------|-------|
| TCP-only transport | Medium | No MQTT mode for preset bridge (MQTT avoids web client conflicts) |
| No persistent queue | Medium | In-memory Queue only; messages lost on restart |
| No telemetry bridging | Low | Only TEXT_MESSAGE_APP forwarded (`mesh_bridge.py:377`) |
| No rate limiting | Low | Fast side can overwhelm slow side queue |

---

### 3.2 Scenario B: Meshtastic <> MeshForge <> MeshCore

**Status: IMPLEMENTED (Alpha branch)** (`meshcore_handler.py`, `canonical_message.py`, `meshcore_bridge_mixin.py`)

```
 ┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
 │  MESHTASTIC      │         │    MeshForge      │         │   MESHCORE       │
 │  Mesh Network    │◄───────►│  3-Way Bridge     │◄───────►│   Mesh Network   │
 │  906.875 MHz     │ TCP/    │  (rns_bridge.py)  │ Serial/ │   910.525 MHz    │
 │  SF11, 250kHz    │ MQTT    │  CanonicalMessage │ TCP/BLE │   SF7, 62.5kHz   │
 │  32-bit IDs      │         │  MessageRouter    │         │   16-bit addrs   │
 └──────────────────┘         └──────────────────┘         └──────────────────┘
    meshtasticd                   Raspberry Pi              Companion Radio
    Radio #1                                                   Radio #2
```

#### How It Works

1. `meshcore_handler.py` connects to a MeshCore companion radio via serial, TCP, or BLE (`config.py:253-256`)
2. `meshtastic_handler.py` connects to meshtasticd via TCP or subscribes to MQTT
3. Messages from either side are normalized to `CanonicalMessage` (`canonical_message.py:101,170`)
4. `MessageRouter` determines destination network based on direction rules
5. Messages are converted to destination-native format and transmitted
6. Internet-originated messages (MQTT `via_mqtt` flag) are blocked from MeshCore (`canonical_message.py:344-366`)

#### Hash/Addressing Translation

**Addresses are NOT translated.** They are preserved in `CanonicalMessage`:

| Field | Meshtastic Source | MeshCore Source |
|-------|-------------------|-----------------|
| `source_network` | `"meshtastic"` | `"meshcore"` |
| `source_address` | `"!aabbccdd"` (`canonical_message.py:126`) | `"meshcore:a1b2c3d4e5f6"` (pubkey prefix) |
| `destination_address` | `"!eeff0011"` or None (broadcast) | Contact name or None |

The gateway is identified on each network by its own native ID -- a Meshtastic node ID on the Meshtastic side, a MeshCore pubkey on the MeshCore side. Cross-network messages appear to originate from the gateway node itself.

#### RF Physical Layer Incompatibility

| Parameter | Meshtastic (LongFast) | MeshCore (Default) |
|-----------|----------------------|-------------------|
| Frequency | 906.875 MHz | 910.525 MHz |
| Spreading Factor | SF11 | SF7 |
| Bandwidth | 250 kHz | 62.5 kHz |
| Sync Word | 0x2B | 0x12 |
| Preamble | 16 symbols | 8 symbols |
| Max Payload | 237 bytes | 184 bytes (160 text) |

Source: `.claude/research/dual_protocol_meshcore.md:32-44`

**These protocols cannot hear each other on RF.** Different frequencies, sync words, spreading factors, and bandwidths. Two separate radios are mandatory.

#### Payload Truncation

Meshtastic allows 237-byte text messages. MeshCore limits text to 160 bytes. When bridging Meshtastic->MeshCore, messages exceeding 160 bytes are truncated with a Unicode ellipsis (`canonical_message.py:309-319`, `canonical_message.py:421`):

```python
MESHTASTIC_MAX_PAYLOAD = 237      # canonical_message.py:31
MESHCORE_MAX_TEXT = 160            # canonical_message.py:33
TRUNCATION_INDICATOR = "\u2026"    # canonical_message.py:34
```

#### Internet Origin Filtering

MeshCore is a **pure-radio network** -- it has no internet transport. Meshtastic messages arriving via MQTT (internet-originated) MUST be filtered to prevent internet traffic from flooding the radio-only MeshCore network:

```python
# canonical_message.py:344-366
def should_bridge(self, filter_mqtt=False, filter_internet_to_meshcore=True):
    if (filter_internet_to_meshcore
            and self.via_internet
            and self.destination_network == Protocol.MESHCORE.value):
        return False
```

#### Current Gaps

| Gap | Severity | Notes |
|-----|----------|-------|
| CHANNEL_MSG_RECV event bug | High | meshcore_py #1232; polling fallback at `config.py:268-269` |
| No cross-protocol DM routing | High | DMs require contact mapping table (doesn't exist yet) |
| Non-standard Ed25519 scalar | Medium | MeshCore key derivation incompatible with standard libraries |
| Alpha-only | Medium | Not merged to main branch |

---

### 3.3 Scenario C: LoRa <> MeshForge <> WiFi

**Status: PARTIALLY EXISTS** (RNS supports WiFi transport; no dedicated WiFi AP mode in MeshForge)

```
 ┌──────────────────┐         ┌──────────────────────────────┐         ┌──────────────────┐
 │  LoRa MESH       │         │         MeshForge RPi        │         │   WiFi CLIENTS   │
 │  (Meshtastic or  │◄───────►│  ┌─────────┐  ┌───────────┐ │◄───────►│   Phones/Laptops │
 │   MeshCore or    │ Radio   │  │ Gateway │  │ hostapd   │ │  WiFi   │   running         │
 │   RNS+LoRa)      │         │  │ Bridge  │  │ AP Mode   │ │  AP     │   Sideband/       │
 │                  │         │  └────┬────┘  └─────┬─────┘ │         │   NomadNet/       │
 └──────────────────┘         │       │    RNS      │       │         │   Web Browser     │
                              │       └──────┬──────┘       │         └──────────────────┘
                              │          rnsd TCP            │
                              └──────────────────────────────┘
```

#### Three Architecture Options

**Option 1: RNS WiFi Interface (Simplest)**

RNS natively supports TCP/IP transport. If MeshForge creates a WiFi AP (hostapd), WiFi clients running RNS apps (Sideband, NomadNet) connect to the AP and reach the LoRa mesh via `rnsd`:

```
WiFi Client (Sideband) -> WiFi AP -> rnsd TCP -> RNS Interface -> LoRa Radio -> Mesh
```

No protocol translation needed -- RNS handles end-to-end addressing. The WiFi AP is just a transport layer. Hash/addressing is preserved because RNS destinations are the same regardless of transport (TCP, UDP, LoRa, I2P).

**Option 2: Meshtastic MQTT over WiFi**

Meshtastic already publishes to MQTT. A WiFi AP allows local clients to subscribe to the MQTT broker and receive mesh traffic:

```
LoRa Mesh -> meshtasticd -> MQTT Broker -> WiFi AP -> WiFi Client (MQTT subscriber)
```

This is read-heavy (monitoring), but write-back is possible via `meshtastic --sendtext` over TCP.

**Option 3: Captive Portal Gateway (MeshLinkFoundation Pattern)**

WiFi clients connect to a MeshForge hotspot and see a web-based interface for the mesh network -- message board, node map, weather data. No mesh protocol knowledge needed on the client side.

#### MeshLinkFoundation Adaptation

MeshLinkFoundation's `meshlink-wifi-gateway` provides a ready-made WiFi AP stack for Raspberry Pi:

| Component | MeshLink Implementation | MeshForge Adaptation |
|-----------|------------------------|---------------------|
| WiFi AP | `hostapd` with WPA2 on wlan0 | Same -- configure SSID as "MeshForge" |
| DHCP/DNS | `dnsmasq` (192.168.50.0/24) | Same subnet, different domain |
| Traffic redirect | iptables REDIRECT port 80->3000 | Redirect to MeshForge web interface |
| Captive portal | Node.js broker with service tiers | Replace with mesh status/messaging page |
| Service management | systemd units | Integrate with MeshForge service_check.py |
| Multi-node | Tailscale VPN between gateways | Applicable for distributed mesh gateways |

**What to take**: hostapd config, dnsmasq setup, iptables rules, systemd patterns.
**What to skip**: Stripe payments, service tiers, Node.js broker (replace with Python/MeshForge native).

#### Hash/Addressing Implications

WiFi is a Layer 2 transport. Protocol-level addressing passes through unmodified:
- RNS destination hashes are preserved end-to-end
- Meshtastic node IDs survive MQTT transport
- No address translation at the WiFi boundary

#### Current Gaps

| Gap | Severity | Notes |
|-----|----------|-------|
| No WiFi AP management | High | MeshForge cannot create/manage a hotspot |
| No captive portal | High | No web interface for non-mesh WiFi clients |
| No bandwidth management | Medium | LoRa throughput (~50-500 B/s) << WiFi expectations |
| No client isolation | Medium | WiFi clients could interfere with each other |
| Expectation mismatch | Medium | WiFi users expect internet-speed responses |

---

### 3.4 Scenario D: Meshtastic <> MeshForge <> RNS (Production)

**Status: PRODUCTION** (main branch, `mqtt_bridge` mode recommended)

```
 ┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
 │  MESHTASTIC      │         │    MeshForge      │         │   RNS/LXMF       │
 │  Mesh Network    │◄───────►│  Gateway Bridge   │◄───────►│   Network        │
 │  LoRa Radio      │ MQTT/   │  (rns_bridge.py)  │  LXMF   │   LoRa/TCP/I2P   │
 │  !aabbccdd IDs   │ TCP     │  1,570 lines      │  API    │   128-bit hashes │
 │  237-byte msgs   │         │  SQLite queue      │         │   Unlimited msgs │
 └──────────────────┘         └──────────────────┘         └──────────────────┘
    meshtasticd                   Raspberry Pi                  rnsd
```

#### How It Works (MQTT Mode -- Recommended)

1. MeshForge subscribes to Meshtastic MQTT topics (`msh/{region}/2/json/{channel}/#`)
2. JSON-decoded mesh packets are normalized to `CanonicalMessage` (`canonical_message.py:101`)
3. Router determines if message should cross to RNS (`message_routing.py:55-64`)
4. For RNS delivery, message is wrapped in LXMF format and sent via `rnsd`
5. Incoming LXMF messages follow the reverse path

#### Hash/Addressing

This is the most challenging address mapping because the protocols have **zero addressing overlap**:

| Aspect | Meshtastic | RNS |
|--------|-----------|-----|
| Address format | `!aabbccdd` (32-bit hex) | 16-byte hex string (128-bit) |
| Address derivation | Sequential / random assignment | `SHA-256(Ed25519_pubkey)[:16]` |
| Crypto binding | None (v2.4-), TOFU (v2.5+) | Cryptographic (Ed25519 verified) |
| Spoofability | Easy (just set node ID) | Impossible (need private key) |

MeshForge tracks nodes in a unified registry with network-prefixed IDs:
- Meshtastic nodes: `mesh_!12345678`
- RNS nodes: `rns_abcd1234efgh5678`

Cross-network messages appear to originate from MeshForge's own identity on each network.

#### Broadcast Asymmetry

| Feature | Meshtastic | RNS |
|---------|-----------|-----|
| Broadcast | Native (`!ffffffff`) | Requires propagation node |
| Delivery model | Best-effort flood | Store-and-forward via propagation |
| Confirmation | None for broadcast | LXMF delivery receipts (directed only) |

Meshtastic broadcasts freely to all nodes. RNS has no native broadcast -- messages must target specific destination hashes, or use a propagation node for wider distribution.

---

## 4. The Hash Problem: Cryptographic Deep Dive

### 4.1 Why Universal Hash Mapping Is Impossible

The three protocols derive node identities from fundamentally different sources:

#### Meshtastic: Sequential IDs (No Crypto Binding)

```
Node ID = 32-bit integer, displayed as !aabbccdd
Source: Hardware MAC address, user-assigned, or random
Crypto: NONE (pre-v2.5) or TOFU Ed25519 (v2.5+)
```

Meshtastic IDs have **no mathematical relationship** to any cryptographic key. A node ID is just a number. Even with v2.5+ PKI, the ID is independent of the public key -- there's no hash derivation.

**Attack surface**: Pre-v2.5, anyone can claim any node ID. Post-v2.5, TOFU means the first key seen for an ID is trusted, but MITM is possible on first contact.

#### RNS/Reticulum: SHA-256 Truncated Hash (Full Crypto Binding)

```
Step 1: Generate Ed25519 keypair (32-byte public key)
Step 2: identity.hash = SHA-256(public_key)[:16]  (truncate to 128 bits)
Step 3: Destination hash displayed as 32-char hex string

Source: src/gateway/canonical_message.py:233 (from_rns)
Reference: .claude/research/rns_comprehensive.md:124-136
```

The destination hash IS the identity. You cannot claim someone else's hash without their private key because:
1. Hash is derived from public key via SHA-256
2. All messages are signed with the corresponding Ed25519 private key
3. Recipients verify signatures against the announced public key
4. SHA-256 is collision-resistant (birthday attack requires ~2^64 operations for 128-bit truncation)

**Security guarantee**: Cryptographic identity verification. Cannot be spoofed.

#### MeshCore: Non-Standard Ed25519 (Crypto Binding, Incompatible Derivation)

```
Step 1: Generate Ed25519 keypair (NON-STANDARD scalar clamping)
Step 2: 16-bit mesh address derived from pubkey hash
Step 3: 12-char hex prefix of pubkey used as node identifier

Source: .claude/research/dual_protocol_meshcore.md:59
Reference: src/gateway/node_models.py (meshcore node creation)
```

**Critical incompatibility**: MeshCore uses a pre-clamped scalar for Ed25519 instead of the standard `clamp(SHA-512(seed))` derivation. This means:

1. Standard Ed25519 libraries (libsodium, Python `cryptography`) will derive the **wrong public key** from a MeshCore private key
2. MeshCore public keys cannot be verified using standard Ed25519 signature verification without accommodating the non-standard scalar
3. Even though both RNS and MeshCore use Ed25519, the key derivation is incompatible

Source: `.claude/research/dual_protocol_meshcore.md:59`: "MeshCore uses a non-standard Ed25519 convention where the first 32 bytes of the private key are a pre-clamped scalar (skips `clamp(sha512(seed))`). Standard crypto libraries will derive the wrong public key if handed a raw MeshCore private key without accommodation."

### 4.2 Hash Comparison Table

| Property | Meshtastic | RNS | MeshCore |
|----------|-----------|-----|----------|
| **Size** | 32 bits | 128 bits | 16 bits (mesh addr) + variable (pubkey) |
| **Derivation** | None (assigned) | SHA-256(Ed25519 pubkey)[:16] | Ed25519 pubkey hash (non-standard) |
| **Entropy** | ~32 bits | ~128 bits | ~16 bits (mesh) + ~96 bits (pubkey prefix) |
| **Collision risk** | High (~65K nodes = 50% birthday) | Negligible (~2^64 ops) | High for mesh addr (~256 nodes = 50%) |
| **Spoofability** | Trivial (pre-v2.5) | Impossible | Computationally infeasible |
| **Verification** | Trust on First Use (v2.5+) | Cryptographic (Ed25519 signature) | Cryptographic (non-standard Ed25519) |
| **Cross-protocol mapping** | Cannot derive from RNS/MeshCore | Cannot derive from Meshtastic/MeshCore | Cannot derive from Meshtastic/RNS |

### 4.3 What MeshForge Does Instead

Since hash mapping is impossible, MeshForge uses three strategies:

**Strategy 1: Network-Prefixed Identifiers**

Each node gets a unique ID prefixed by its source network:
```
mesh_!12345678          -- Meshtastic node
rns_abcd1234efgh5678    -- RNS node (first 16 chars of hash)
meshcore:a1b2c3d4e5f6   -- MeshCore node (pubkey prefix)
```

These are used in the unified node tracker for cross-reference, but they are NOT used for on-wire addressing.

**Strategy 2: CanonicalMessage Preserves Native Addresses**

The `CanonicalMessage` dataclass (`canonical_message.py:57-78`) stores the original address:
```python
source_network: str = ""            # "meshtastic" | "meshcore" | "rns"
source_address: str = ""            # Network-native address (preserved)
destination_address: Optional[str]   # Network-native address or None
destination_network: Optional[str]   # Set by router
```

When a message crosses from Meshtastic to MeshCore, the `source_address` retains the Meshtastic `!aabbccdd` format. The message appears on MeshCore as originating from MeshForge's own MeshCore identity, with the original sender noted in the message text (if prefix tagging is enabled).

**Strategy 3: Direction-Based Routing (Not Address-Based)**

The `MessageRouter` (`message_routing.py:38-64`) routes by network direction, not by address translation:
```
"Is this message from meshtastic?" + "Is the configured direction mesh_to_meshcore?"
    -> YES: Forward to MeshCore handler
    -> NO: Check other rules or drop
```

This bypasses the hash translation problem entirely -- the router doesn't need to know what a destination looks like on the other network.

### 4.4 What Cannot Be Done

| Capability | Status | Why |
|-----------|--------|-----|
| Universal hash that works across all 3 protocols | Impossible | Different derivations, sizes, and crypto assumptions |
| Direct message routing across protocols | Requires contact mapping table | No address translation = no way to know who "!abc" is on RNS |
| Cryptographic identity verification across protocols | Impossible | Different Ed25519 key derivations (RNS standard, MeshCore non-standard) |
| Automated node identity linking | Not implemented | Would require manual pairing or a discovery protocol |
| End-to-end encryption across protocols | Impossible | Different encryption schemes (AES-256-CTR vs AES-128+HMAC vs AES-256-CBC+HMAC) |

### 4.5 Theoretical Contact Mapping Table

For cross-protocol DMs to work, MeshForge would need a contact mapping table:

```
┌────────────────────────────────────────────────────────────────────┐
│ Contact Map (hypothetical)                                         │
├──────────────┬────────────────────┬──────────────────────────────┤
│ User         │ Meshtastic ID      │ RNS Hash                     │
├──────────────┼────────────────────┼──────────────────────────────┤
│ Alice        │ !a1b2c3d4          │ 7f3a9c2e1b8d4f6a...         │
│ Bob          │ !e5f6a7b8          │ (not on RNS)                 │
│ Carol        │ (not on Meshtastic)│ 2d4e6f8a1c3b5d7e...         │
└──────────────┴────────────────────┴──────────────────────────────┘
```

This would require manual registration or an automated discovery protocol where users on multiple networks advertise their cross-protocol identities. Neither exists in MeshForge today.

---

## 5. MeshLinkFoundation Feature Diff

### 5.1 Organization Overview

**MeshLinkFoundation** (github.com/MeshLinkFoundation) maintains 3 repositories focused on decentralized WiFi internet sharing. Despite the "mesh" name, they have **zero integration** with Meshtastic, RNS, MeshCore, or LoRa protocols.

### 5.2 Repositories

| Repository | Purpose | Tech Stack |
|-----------|---------|------------|
| `meshlink-wifi-gateway` | Raspberry Pi WiFi hotspot with captive portal | Shell, hostapd, dnsmasq, iptables |
| `broker` | Captive portal UI with service tier management | TypeScript, Node.js, Vite, Tailwind |
| `operator-dashboard` | Multi-node management console | React, Express.js, TypeScript |

### 5.3 Feature Adaptation Matrix

| MeshLink Feature | What It Does | MeshForge Relevance | Adaptable? | Priority |
|-----------------|-------------|---------------------|------------|----------|
| **hostapd WiFi AP** | Creates SSID "MeshLink" on wlan0 | WiFi transport for LoRa<>WiFi bridge | Yes | High |
| **dnsmasq DHCP/DNS** | 192.168.50.0/24 subnet, DHCP .10-.100 | WiFi client IP assignment | Yes | High |
| **iptables REDIRECT** | Port 80 -> 3000 (captive portal) | Redirect to MeshForge web UI | Yes | High |
| **systemd services** | hostapd + dnsmasq persistence | Integrate with `service_check.py` | Yes | High |
| **Captive portal** | Landing page on WiFi connect | Mesh network status/messaging page | Pattern only | Medium |
| **Tailscale VPN** | Secure inter-node connectivity | Distributed gateway management | Yes | Medium |
| **Operator dashboard** | Multi-node stats aggregation | Parallel to MeshForge TUI monitoring | Patterns | Low |
| **Service tiers/Stripe** | Paid WiFi access levels | Not relevant for mesh networking | No | N/A |
| **MESH token crypto** | Cryptocurrency payments | Not relevant | No | N/A |

### 5.4 Concrete Adaptation: WiFi AP Module for MeshForge

Using MeshLinkFoundation patterns, a MeshForge WiFi AP module would:

```python
# Hypothetical: src/utils/wifi_ap.py (adapting MeshLink patterns)

# 1. hostapd config (from meshlink-wifi-gateway/install.sh)
HOSTAPD_CONF = """
interface=wlan0
driver=nl80211
ssid=MeshForge
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={passphrase}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""

# 2. dnsmasq config
DNSMASQ_CONF = """
interface=wlan0
dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,24h
address=/meshforge.local/192.168.50.1
"""

# 3. iptables rules (MeshLink uses REDIRECT, not DNAT -- avoids routing issues)
# iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080
```

**Key MeshLink design decisions to adopt:**
- Use iptables `REDIRECT` not `DNAT` (avoids routing issues with local IPs)
- No DNS hijacking (Android/MIUI enforce HTTPS, causing TLS failures)
- HTTP-only port 80 redirection (port 443 TLS errors aren't recognized as captive portals)
- Tested on: Raspberry Pi 5 (4GB), Debian 13 Trixie

---

## 6. Cross-Cutting Concerns

### 6.1 Encryption Boundary

When messages cross protocol boundaries, **end-to-end encryption is broken by design**:

```
Meshtastic (AES-256-CTR) -> MeshForge [PLAINTEXT] -> MeshCore (AES-128+HMAC)
Meshtastic (AES-256-CTR) -> MeshForge [PLAINTEXT] -> RNS (AES-256-CBC+HMAC)
```

The gateway must decrypt on one side and re-encrypt on the other. This means:
1. The gateway has access to plaintext -- it's a trusted relay
2. Channel encryption (shared PSK) doesn't survive the bridge
3. DM encryption requires key material for both protocols on the gateway

**Mitigation**: Gateway should be physically secured (dedicated RPi, not shared). Document that bridge is a trust point.

### 6.2 Loop Prevention

Multi-bridge scenarios (e.g., two MeshForge gateways both bridging Meshtastic<>RNS) can create infinite loops. Current mitigations:

| Mechanism | Location | How It Works |
|-----------|----------|-------------|
| Content hash dedup | `mesh_bridge.py:47-50` | MD5 hash of content + source ID |
| Dedup window | `mesh_bridge.py:198` | 60-second default TTL for seen messages |
| MQTT origin filter | `canonical_message.py:344-366` | Block internet-originated messages from MeshCore |
| Queue bounds | `mesh_bridge.py:75-76` | `maxsize=1000` prevents unbounded accumulation |

**Gap**: No global dedup across bridge modes. If a message enters via MQTT bridge AND preset bridge simultaneously, both instances will forward it.

### 6.3 Latency Budget

| Path | Expected Latency | Bottleneck |
|------|-------------------|-----------|
| Short Turbo -> Long Fast | 8-16s | Long Fast TX time |
| Meshtastic -> MeshCore | 2-10s | MeshCore companion serial latency |
| Meshtastic -> RNS (LXMF) | 5-30s | LXMF store-and-forward |
| LoRa -> WiFi (RNS transport) | <1s | WiFi is fast; LoRa RX is the bottleneck |
| WiFi -> LoRa | 0.4-8s | LoRa TX time depends on preset |

---

## 7. Recommendations

### Priority 1: MQTT Mode for Preset Bridge
**Current**: `mesh_bridge.py` uses TCP-only (`meshtastic.tcp_interface.TCPInterface`).
**Problem**: TCP connection to meshtasticd blocks the web client (only one TCP connection allowed).
**Solution**: Add MQTT subscription mode (like `rns_bridge.py`'s mqtt_bridge mode) to receive from both presets without blocking web clients.

### Priority 2: WiFi AP Management Module
**Current**: No WiFi AP capability in MeshForge.
**Solution**: Adapt MeshLinkFoundation's hostapd/dnsmasq/iptables pattern into a `src/utils/wifi_ap.py` module. Use `service_check.py` for systemd integration.
**Enables**: LoRa<>WiFi scenario, captive portal for non-mesh clients, field-deployed mesh access points.

### Priority 3: Cross-Protocol Contact Mapping
**Current**: No way to route DMs between Meshtastic and MeshCore/RNS nodes.
**Solution**: SQLite-backed contact mapping table linking Meshtastic !IDs, RNS hashes, and MeshCore pubkeys for the same user. Manual registration initially; automated discovery later.

### Priority 4: Persistent Queue for Preset Bridge
**Current**: In-memory `Queue(maxsize=1000)` in `mesh_bridge.py`.
**Solution**: Integrate with existing `message_queue.py` SQLite persistence for crash recovery and sustained throughput asymmetry handling.

### Priority 5: MeshCore CHANNEL_MSG_RECV Stabilization
**Current**: Firmware bug (meshcore_py #1232) causes missed channel messages; polling fallback at 5-second intervals (`config.py:268-269`).
**Solution**: Monitor upstream fix. Current polling is a viable workaround but adds latency and battery drain.

---

## 8. Verification Matrix

All technical claims in this document are verified against source code:

| Claim | Source File | Line(s) |
|-------|-----------|---------|
| 6 bridge modes | `config.py` | 370-378 |
| CanonicalMessage 2N conversion | `canonical_message.py` | 5-6, 57-78 |
| Meshtastic address extraction | `canonical_message.py` | 101, 126 |
| MeshCore address extraction | `canonical_message.py` | 170, 191-197 |
| RNS address extraction | `canonical_message.py` | 233, 252-253 |
| Payload size limits | `canonical_message.py` | 31-34 |
| MeshCore text truncation | `canonical_message.py` | 309-319 |
| Internet origin filtering | `canonical_message.py` | 344-366 |
| Direction routing map | `message_routing.py` | 55-64 |
| Preset bridge dedup | `mesh_bridge.py` | 47-50, 439-448 |
| Preset bridge queues | `mesh_bridge.py` | 75-76 |
| TEXT_MESSAGE_APP filter | `mesh_bridge.py` | 377 |
| Throughput estimates | `config.py` | 325-338 |
| Dual preset template | `config.py` | 811-847 |
| MeshCore connection types | `config.py` | 253-256 |
| MeshCore polling interval | `config.py` | 268-269 |
| RF parameter comparison | `dual_protocol_meshcore.md` | 32-44 |
| MeshCore non-standard scalar | `dual_protocol_meshcore.md` | 59 |
| Crypto comparison | `dual_protocol_meshcore.md` | 49-57 |
| RNS identity creation | `rns_comprehensive.md` | 124-136 |

---

*Made with aloha for the mesh community -- WH6GXZ*

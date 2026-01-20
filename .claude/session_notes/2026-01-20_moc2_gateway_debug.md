# MOC2 Gateway Debug Session - 2026-01-20

## Environment

**MOC2 (Client)**
- Raspberry Pi 4 with MeshAdv-Pi-Hat
- IP: 192.168.86.33
- meshtasticd running, Short Turbo preset, Slot 8
- rnsd running as root with Meshtastic_Interface.py

**HawaiiNet Server (RNSmeshgate)**
- IP: 192.168.86.38:4242
- Running reticulum-meshchat service (active 2+ days)
- Receiving announces from 6+ nodes
- TCPServerInterface on port 4242

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| meshtasticd | ✅ Running | Port 4403, Short Turbo |
| rnsd | ✅ Running | Root user, verbose mode |
| TCPInterface (HawaiiNet) | ⚠️ 199 B up, 0 B down | One-way traffic |
| MeshtasticInterface | ⚠️ Up, 0 B traffic | No RNS peer on mesh |
| NomadNet conversations | ✅ 6 conversations visible | RNS core working |
| reticulum-meshchat | ✅ Receiving announces | Server healthy |

## Key Findings

### 1. Architecture Conflict Identified

Two bridging approaches competing:
- **Meshtastic_Interface.py** (external RNS interface) - connects to TCP:4403
- **MeshForge rns_bridge.py** (message bridge) - also wants TCP:4403

meshtasticd allows **ONLY ONE TCP CLIENT** at a time.

### 2. Why MeshtasticInterface Shows 0 B

For Meshtastic_Interface.py to show traffic:
- Need ANOTHER RNS node on the Meshtastic mesh
- That node must also have Meshtastic_Interface.py configured
- Both must use portNum=256 (PRIVATE_APP)

Without a peer, 0 B is expected.

### 3. Why HawaiiNet Shows 0 B Down

Possible causes:
- `ifac_netname` mismatch between client and server
- Server not configured to share paths with this client
- Firewall or routing issue

Server IS healthy (receiving announces), so issue is client-side config.

### 4. Meshtastic "Waiting" Messages Unrelated to RNS

Regular Meshtastic chat (TEXT_MESSAGE_APP, port 1) is separate from RNS (PRIVATE_APP, port 256).
"Waiting" = no ACK from Meshtastic peers, not an RNS issue.

## Files Modified This Session

1. `/root/.reticulum/config` - Added `portNum = 256` to Meshtastic interface
2. `src/main.py` - Fixed `Path.home()` → `get_real_user_home()` (MF001)
3. Various permission fixes for `/home/wh6gxz/.reticulum/`

## Next Steps

1. **Get NomadNet working** - Verify bidirectional HawaiiNet connectivity
2. **Check server ifac_netname** - Match client config to server requirements
3. **Solve gateway conflict** - Choose either:
   - Meshtastic_Interface.py (RNS transport layer) - needs RNS peer on mesh
   - MeshForge message bridge (LXMF translation) - works with any Meshtastic node

## Architecture Options

### Option A: Meshtastic_Interface.py Only
```
RNS ←→ rnsd ←→ Meshtastic_Interface.py ←→ meshtasticd ←→ LoRa ←→ [RNS peer]
```
- Requires another Meshtastic_Interface.py node on mesh
- Pure RNS transport over Meshtastic

### Option B: MeshForge Message Bridge Only
```
RNS ←→ rnsd ←→ (shared instance) ←→ MeshForge ←→ meshtasticd ←→ LoRa ←→ [Any node]
```
- Disable Meshtastic_Interface.py in RNS config
- MeshForge translates LXMF ↔ Meshtastic text messages
- Works with regular Meshtastic nodes

### Option C: Hybrid (Not Recommended)
Both running = TCP port conflict = 0 traffic

## Reference Commands

```bash
# Check rnsd status
sudo rnstatus -a

# Check path table
sudo python3 -c "import RNS; r=RNS.Reticulum(); print(len(RNS.Transport.path_table))"

# Check TCP connections to meshtasticd
sudo lsof -i :4403

# Check HawaiiNet server
nc -zv 192.168.86.38 4242

# Restart rnsd
sudo systemctl restart rnsd && sleep 3 && sudo rnstatus
```

## Session End

User signing off to verify HawaiiNet with NomadNet, then return to gateway debugging.

73 de WH6GXZ

# Meshtastic JavaScript & Module API Research

## Overview

This document covers the Meshtastic JavaScript library for web/node integration and the Module API for custom firmware development.

---

## Part 1: JavaScript Library (@meshtastic/js)

### Official Resources
- **npm Package**: https://www.npmjs.com/package/@meshtastic/js
- **API Documentation**: https://js.meshtastic.org/
- **GitHub (archived)**: https://github.com/meshtastic/js
- **Active Monorepo**: https://github.com/meshtastic/web

### Important: Migration Notice
The standalone `@meshtastic/js` repository is **archived**. The code has been migrated into the **Meshtastic Web monorepo**.

### Package Structure (Monorepo)
```
@meshtastic/core          - Core functionality
@meshtastic/transport-http        - HTTP transport
@meshtastic/transport-web-bluetooth - Web Bluetooth support
@meshtastic/transport-web-serial    - Web Serial support
@meshtastic/transport-deno          - TCP transport for Deno
```

All packages are now published to JSR (JavaScript Registry).

### Connection Types
1. **HTTP(S)** - Connect to device web server
2. **Web Bluetooth** - Direct BLE connection (browser)
3. **Web Serial** - USB serial connection (browser)

### Installation
```bash
# From npm (may be outdated)
npm install @meshtastic/js

# From JSR (recommended for new projects)
npx jsr add @meshtastic/core
npx jsr add @meshtastic/transport-http
```

### Basic Usage Pattern
```javascript
import { Client } from '@meshtastic/core';
import { HttpTransport } from '@meshtastic/transport-http';

// Create transport
const transport = new HttpTransport('http://meshtastic.local');

// Create client
const client = new Client(transport);

// Connect
await client.connect();

// Get nodes
const nodes = client.nodes;

// Send message
await client.sendText('Hello mesh!');

// Listen for events
client.events.onFromRadio.subscribe((packet) => {
    console.log('Received:', packet);
});
```

### Key Events
- `onFromRadio` - All packets from device
- `onMeshPacket` - Mesh network packets
- `onNodeInfoPacket` - Node information updates
- `onPositionPacket` - Position updates
- `onTextMessagePacket` - Text messages
- `onChannelPacket` - Channel configuration

### Integration with MeshForge
For MeshForge Web UI, we could:
1. Use HTTP transport to connect to meshtasticd
2. Replace Flask API calls with direct Meshtastic.js calls
3. Get real-time updates via event subscriptions
4. Build richer map/node visualizations

---

## Part 2: Module API (Custom Firmware Modules)

### Official Resources
- **Module API Docs**: https://meshtastic.org/docs/development/device/module-api/
- **Client API**: https://meshtastic.org/docs/development/device/client-api/
- **HTTP API**: https://meshtastic.org/docs/development/device/http-api/
- **Protobufs**: https://github.com/meshtastic/protobufs

### Module Types

#### SinglePortModule
For modules that send/receive raw bytes on a single port.
```cpp
#include "mesh/SinglePortModule.h"

class MyModule : public SinglePortModule {
public:
    MyModule() : SinglePortModule("mymodule", PortNum_PRIVATE_APP) {}

protected:
    virtual ProcessMessage handleReceived(const MeshPacket &mp) override;
};
```

#### ProtobufModule
For modules using Protocol Buffers.
```cpp
#include "mesh/ProtobufModule.h"

class MyProtoModule : public ProtobufModule<MyMessage> {
public:
    MyProtoModule() : ProtobufModule("myproto", PortNum_PRIVATE_APP, &MyMessage_msg) {}

protected:
    virtual bool handleReceivedProtobuf(const MeshPacket &mp, MyMessage *msg) override;
};
```

### Key Source Files
```
src/mesh/MeshModule.h       - Base class (don't use directly)
src/mesh/SinglePortModule.h - Raw byte modules
src/mesh/ProtobufModule.h   - Protobuf modules
```

### Port Numbers
- Use `PRIVATE_APP` (256) for development
- Request official port number for production modules
- See `meshtastic/portnums.proto` for all ports

### Protobuf Architecture
- **Language-neutral** serialization format
- **Smaller** than JSON/XML
- **Type-safe** with generated code
- Used for device-to-device and app-to-device communication

### Protobuf Resources
- **Definitions**: https://github.com/meshtastic/protobufs
- **Buf Registry**: https://buf.build/meshtastic/protobufs
- **Python API**: https://python.meshtastic.org/protobuf/index.html
- **JSR Package**: https://jsr.io/@meshtastic/protobufs

### Key Protobuf Files
```
meshtastic/mesh.proto        - Core mesh types
meshtastic/portnums.proto    - Port number definitions
meshtastic/config.proto      - Device configuration
meshtastic/module_config.proto - Module settings
meshtastic/telemetry.proto   - Sensor data
meshtastic/admin.proto       - Admin messages
```

---

## Integration Ideas for MeshForge

### JavaScript Integration
1. **Real-time Dashboard** - Use @meshtastic/core for live updates
2. **Direct Device Control** - Send config without CLI
3. **Message History** - Stream and store messages
4. **Node Tracking** - Real-time position updates

### Custom Module Ideas
1. **Remote Monitoring** - Custom telemetry module
2. **Alert System** - Threshold-based notifications
3. **Mesh Analytics** - Network health metrics
4. **Integration Bridge** - Connect to external systems

---

## References

- [Meshtastic JS Docs](https://js.meshtastic.org/)
- [Module API](https://meshtastic.org/docs/development/device/module-api/)
- [Protobufs Reference](https://meshtastic.org/docs/development/reference/protobufs/)
- [HTTP API](https://meshtastic.org/docs/development/device/http-api/)

---
*Last Updated: 2026-01-03*
*Status: Research complete, potential integration paths identified*

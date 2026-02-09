# Web Serial API & Meshtastic Transport Architecture Deep Dive

## Purpose

Deep research into the Web Serial API, the Meshtastic web client's transport layer architecture,
the `@meshtastic/js` library, and the feasibility of MeshForge serving as a WebSocket proxy
between the web client and meshtasticd.

---

## Part 1: Web Serial API Fundamentals

### What Is It?

The Web Serial API allows web pages running in a browser to communicate directly with serial
devices (USB, UART) connected to the user's computer. It provides `ReadableStream` and
`WritableStream` interfaces for bidirectional binary data transfer, completely bypassing
the need for any daemon or native application.

- **Spec**: https://wicg.github.io/serial/
- **Chrome Docs**: https://developer.chrome.com/docs/capabilities/serial
- **MDN**: https://developer.mozilla.org/en-US/docs/Web/API/Web_Serial_API

### The `navigator.serial.requestPort()` Flow

1. **User gesture required** -- a click/tap triggers the port selection dialog
2. `navigator.serial.requestPort({ filters })` opens a browser-native chooser
3. User selects a serial device (e.g., a Meshtastic radio on `/dev/ttyUSB0`)
4. Returns a `SerialPort` object
5. `await port.open({ baudRate: 115200 })` opens the connection
6. `port.readable` gives a `ReadableStream<Uint8Array>` for incoming data
7. `port.writable` gives a `WritableStream<Uint8Array>` for outgoing data

Filters allow targeting specific USB vendor/product IDs:
```javascript
const port = await navigator.serial.requestPort({
  filters: [{ usbVendorId: 0x10C4 }]  // Silicon Labs (common Meshtastic USB chip)
});
```

### Reading Binary Data

```javascript
const reader = port.readable.getReader();
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  // value is Uint8Array -- raw binary protobuf-framed data
  processIncomingBytes(value);
}
```

### Writing Binary Data

```javascript
const writer = port.writable.getWriter();
await writer.write(serializedProtobufWithFraming);
writer.releaseLock();
```

### Error Handling and Disconnect

Users can physically unplug the device at any time. Code must:
- Catch read/write errors
- Call `reader.cancel()` and `writer.releaseLock()` before `port.close()`
- Update UI to reflect disconnection

### Security Model

- `requestPort()` **always** shows a user-controlled chooser -- no silent enumeration
- Sites cannot access ports without explicit user consent
- Only HTTPS contexts (or localhost) are allowed
- One device per `requestPort()` call

### Browser Compatibility

| Browser       | Supported? | Versions         |
|---------------|-----------|------------------|
| **Chrome**    | YES       | 89+ (all current)|
| **Edge**      | YES       | 89+ (Chromium)   |
| **Opera**     | YES       | 76+              |
| **Firefox**   | NO        | Not supported    |
| **Safari**    | NO        | Not supported    |

**Key implication**: Web Serial is Chromium-only. For Firefox/Safari users, a WebSocket
bridge (like what MeshForge could provide) is the **only** way to get "browser-to-serial"
functionality.

### How It Bypasses the Daemon

With Web Serial, the browser talks **directly** to the USB serial chip:

```
[Browser (Chrome)] --Web Serial API--> [USB serial chip] --> [Meshtastic radio]
```

No meshtasticd, no Python library, no daemon needed. The browser IS the client.
However, this only works when the user has the device physically plugged into their computer.

---

## Part 2: Meshtastic Web Client Architecture

### Repository & Build System

- **Monorepo**: https://github.com/meshtastic/web
- **Hosted**: https://client.meshtastic.org
- **Build tool**: Vite + React + TypeScript
- **Package manager**: Deno (formerly pnpm)
- **State management**: Zustand (multiple specialized stores)
- **Protobuf tooling**: Buf CLI for `packages/protobufs`

### Monorepo Package Structure

```
packages/
├── web/                         # The React web client UI
├── core/                        # @meshtastic/core - MeshDevice, types, queue
├── protobufs/                   # @meshtastic/protobufs - generated proto types
├── transport-http/              # HTTP(S) transport (ESP32 / meshtasticd)
├── transport-web-serial/        # Web Serial API transport
├── transport-web-bluetooth/     # Web Bluetooth API transport
├── transport-node/              # Node.js TCP transport (port 4403)
├── transport-node-serial/       # Node.js serial transport
└── transport-deno/              # Deno TCP transport
```

All packages are published to both JSR and npm.

### Firmware v2.7.0 Change: Web UI Removed from Device

As of firmware v2.7.0, the web UI is **no longer bundled on the ESP32**. Users must:
1. Use the hosted version at `client.meshtastic.org`
2. Self-host the web client (Docker, custom server, etc.)
3. Bundle it on a Linux-native deployment (Raspberry Pi, OpenWRT)

**This is a significant opportunity for MeshForge** -- users need somewhere to host the
web client, and MeshForge already runs on Linux/Raspberry Pi.

### Self-Hosting the Web Client

**Docker (official image)**:
```bash
docker run -d -p 8080:8080 -p 8443:8443 \
  --restart always --name Meshtastic-Web \
  ghcr.io/meshtastic/web
```
This uses UBI9 Nginx 1.22 to serve the static files.

**From source**:
Build with Deno, output goes to `dist/` -- pure static HTML/JS/CSS that any
web server can serve (Nginx, Python's http.server, Flask, etc.).

---

## Part 3: The Transport Interface (`@meshtastic/core`)

### The Core Abstraction

File: `packages/core/src/types.ts`

```typescript
export interface Transport {
  toDevice: WritableStream<Uint8Array>;
  fromDevice: ReadableStream<DeviceOutput>;
  disconnect(): Promise<void>;
}

type DeviceOutput = Packet | DebugLog | StatusEvent;

interface Packet {
  type: "packet";
  data: Uint8Array;
}

interface DebugLog {
  type: "debug";
  data: string;
}

interface StatusEvent {
  type: "status";
  data: { status: DeviceStatusEnum; reason?: string };
}
```

**This is the critical interface.** Any object that provides `toDevice`, `fromDevice`,
and `disconnect()` can be used as a transport. The web client does not care how the
bytes get to/from the device.

### MeshDevice Class

File: `packages/core/src/meshDevice.ts`

```typescript
class MeshDevice {
  public transport: Transport;

  constructor(transport: Transport, configId?: number) {
    this.transport = transport;
    // Sets up logger, queue, events, xmodem
    this.transport.fromDevice.pipeTo(decodePacket(this));
  }

  // Send raw protobuf bytes to device
  public async sendRaw(toRadio: Uint8Array, id?: number): Promise<number> {
    if (toRadio.length > 512) throw new Error("Message too long");
    this.queue.push({ id, data: toRadio });
    await this.queue.processQueue(this.transport.toDevice);
    return this.queue.wait(id);
  }
}
```

Key architectural points:
- `fromDevice` stream is piped through `decodePacket()` which handles protobuf parsing
- `toDevice` is a `WritableStream` that the queue writes to
- The queue system manages packet ordering and ACK tracking
- All transports are interchangeable via this interface

### Transport Factory Pattern

Each transport package exports a static `create()` method:

```typescript
// Web Serial
import { TransportWebSerial } from "@meshtastic/transport-web-serial";
const transport = await TransportWebSerial.create();
const device = new MeshDevice(transport);

// HTTP
import { TransportHTTP } from "@meshtastic/transport-http";
const transport = await TransportHTTP.create("10.10.0.57");
const device = new MeshDevice(transport);
```

### Connection Manager Pattern in the Web UI

The web client uses Zustand stores and follows this flow for all connection types:
1. **ID Generation**: `randId()` creates a unique device identifier
2. **Device Registration**: `useDeviceStore.addDevice()` adds device to state
3. **Connection Assignment**: `device.addConnection(transport)` binds transport
4. **Event Subscription**: `subscribeAll()` registers for device events

---

## Part 4: The Meshtastic Framing Protocol

### Serial/TCP Framing (4-Byte Header)

All serial and TCP connections use a simple framing protocol:

```
[0x94][0xC3][LENGTH_MSB][LENGTH_LSB][PROTOBUF_PAYLOAD...]
```

| Byte | Value  | Description                          |
|------|--------|--------------------------------------|
| 0    | `0x94` | START1 magic byte                    |
| 1    | `0xC3` | START2 magic byte                    |
| 2    | MSB    | High byte of protobuf payload length |
| 3    | LSB    | Low byte of protobuf payload length  |
| 4+   | data   | Serialized protobuf (ToRadio or FromRadio) |

- Maximum payload size: 512 bytes (lengths > 512 are treated as corruption)
- The `0x94C3` magic bytes are chosen to not look like normal 7-bit ASCII
- If the receiver doesn't see valid header bytes, it prints them as debug output
- No CRC or error correction -- assumes the stream is reliable

### HTTP Framing

For HTTP connections, there is no 4-byte header. Protobuf payloads are sent as raw
binary in the HTTP body:

- **Send**: `PUT /api/v1/toradio` with binary protobuf body (one ToRadio per request)
- **Receive**: `GET /api/v1/fromradio` returns binary protobuf body
- Supports `chunked=true` for streaming
- **Single client limitation**: only one client can poll `/api/v1/fromradio` at a time

### BLE Framing

BLE uses raw protobuf without headers, communicated via GATT characteristics:
- **Service UUID**: `6ba1b218-15a8-461f-9fa8-5dcae273eafd`
- **ToRadio**: `f75c76d2-129e-4dad-a1dd-7866124401e7`
- **FromRadio**: `2c55e69e-4993-11ed-b878-0242ac120002`
- **FromNum (notify)**: `ed9da18c-a800-4f66-a670-aa7547e34453`

### Connection Initialization Flow (All Transports)

1. Client sends `ToRadio { startConfig: configId }` to the device
2. Device responds with a stream of `FromRadio` packets (entire NodeDB dump)
3. Client reads until it gets an empty response (download complete)
4. Client then subscribes for ongoing updates (BLE notify / HTTP poll / stream read)

---

## Part 5: ACK/Delivery Mechanism

### How `want_ack` Works

The `MeshPacket` protobuf has a `want_ack` boolean field:

**Direct Messages**:
```
Packet 1: TEXT_MESSAGE_APP: A -> B  (id=101, want_ack=true)
Packet 2: ROUTING_APP:      B -> A  (id=102, request_id=101, want_ack=false)
```
The ACK is a `ROUTING_APP` packet with `request_id` pointing back to the original.

**Broadcast Messages**:
- ACK flooding would overwhelm the channel
- Instead: sender listens for rebroadcasts as "implicit ACK"
- If no rebroadcast heard, retransmit (up to 3 times with exponential backoff)

### ACK Handling in @meshtastic/core

In `meshDevice.ts`, routing packets are processed:

```typescript
switch (routingPacket.variant.case) {
  case "errorReason": {
    if (routingPacket.variant.value === Routing_Error.NONE) {
      // ACK received -- resolve the promise for this packet
      this.queue.processAck(dataPacket.requestId);
    } else {
      // NACK or error -- reject the promise
      this.queue.processError({
        id: dataPacket.requestId,
        error: routingPacket.variant.value,
      });
    }
    break;
  }
}
```

The queue system uses promises: `sendRaw()` returns a promise that resolves when ACK
is received, or rejects on error/timeout.

### ACK in the Context of a Proxy

If MeshForge sits between the web client and meshtasticd:
- MeshForge must **faithfully forward** all ROUTING_APP packets (ACKs/NACKs)
- The `request_id` field ties ACKs to original packets -- must be preserved
- MeshForge can **observe** ACKs without consuming them
- The queue system in `@meshtastic/core` handles ACK matching on the client side

---

## Part 6: Existing WebSocket Proxy Projects

### 1. liamcottle/meshtastic-websocket-proxy

- **GitHub**: https://github.com/liamcottle/meshtastic-websocket-proxy
- **npm**: `@liamcottle/meshtastic-websocket-proxy`

**Architecture**:
```
[WebSocket Client 1] ──┐
[WebSocket Client 2] ──┼── WebSocket Server ──── HTTP API ──── [Meshtastic Device]
[WebSocket Client N] ──┘     (port 8080)       (polling)         (ESP32/RPi)
```

**Key features**:
- Solves the single-client limitation of the HTTP API
- Broadcasts all `FromRadio` packets to all connected WebSocket clients
- All clients can send `ToRadio` packets
- JSON message format with base64-encoded protobuf:
  ```json
  {
    "type": "from_radio",
    "protobuf": "<base64 encoded protobuf>",
    "json": { /* decoded packet for convenience */ }
  }
  ```
- `--ignore-history` flag to skip packets received before proxy started

**Usage**:
```bash
npx @liamcottle/meshtastic-websocket-proxy \
  --meshtastic-host 127.0.0.1 \
  --websocket-port 8080
```

**Limitation**: Uses JSON + base64 encoding, NOT the raw binary framing that
the Meshtastic web client expects. Cannot be used directly as a transport for
the official web client without modifications.

### 2. WillerZ/ws-serial-gateway

- **GitHub**: https://github.com/WillerZ/ws-serial-gateway
- **Language**: Rust

**Architecture**:
```
[Web Client] ── WebSocket ── [ws-serial-gateway] ── Serial ── [Meshtastic Device]
```

**Key features**:
- Transparent binary tunnel -- WebSocket binary frames pass directly to/from serial
- YAML config maps URL endpoints to serial ports + baud rates
- Single client per serial port (hardware limitation)
- No protocol awareness -- pure byte forwarding

**This is closer to what MeshForge needs**: raw binary WebSocket frames that the
Meshtastic transport layer can work with directly.

### 3. Meshtastic Web PR #998 (WebSocket Transport)

- **PR**: https://github.com/meshtastic/web/pull/998
- **Status**: Open (not merged)
- **Author**: WillerZ

Adds a WebSocket transport module to the official web client that:
- Creates a `Transport` object backed by a WebSocket connection
- Implements `toDevice` / `fromDevice` using WebSocket binary frames
- Works with `ws-serial-gateway` as the server
- Successfully tested: connecting, viewing config, changing config, sending messages

**Critical finding**: The transport code is described as "near-complete" and working.
The UI changes need work, but the transport itself validates the approach.

### 4. MeshTXT by Liam Cottle

- **GitHub**: https://github.com/liamcottle/meshtxt
- **Live**: https://meshtxt.liamcottle.net/

A complete alternative web client with:
- `--meshtastic-api-url` flag to proxy to a remote meshtasticd
- Built-in server that proxies fromradio/toradio requests
- CORS handling (or Caddy reverse proxy example)
- Bluetooth, Serial, and HTTP connections

---

## Part 7: Can MeshForge Do This?

### Question 1: Serve the Meshtastic web client from port 5000?

**YES, absolutely.** The Meshtastic web client builds to static HTML/JS/CSS files.
MeshForge already has a web server capability. Options:

1. **Embed the pre-built web client**: Download the built assets from
   `ghcr.io/meshtastic/web` Docker image or build from source, serve as static files
2. **Reverse proxy**: Proxy requests to the Docker container
3. **Build from source**: Include in MeshForge's build process

The web client at `client.meshtastic.org` is just static files. Any HTTP server can
serve them.

### Question 2: Provide a WebSocket transport?

**YES, and there's precedent.** The approach:

1. MeshForge runs a WebSocket server (e.g., on `ws://localhost:5000/ws/serial`)
2. MeshForge connects to meshtasticd via TCP (port 4403) using the 4-byte framed protocol
3. The WebSocket carries the same 4-byte framed binary packets
4. A custom transport module in the web client (or a fork) connects via WebSocket

The `Transport` interface is simple:
```typescript
interface Transport {
  toDevice: WritableStream<Uint8Array>;
  fromDevice: ReadableStream<DeviceOutput>;
  disconnect(): Promise<void>;
}
```

MeshForge's Python WebSocket server would:
- Accept WebSocket connections from the browser
- Forward binary frames to meshtasticd TCP (port 4403)
- Forward meshtasticd responses back as binary WebSocket frames
- Handle the 4-byte framing (or pass it through transparently)

### Question 3: Act as a man-in-the-middle?

**YES, with caveats.** MeshForge can sit between the web client and meshtasticd:

```
[Meshtastic Web Client]          [MeshForge]              [meshtasticd]
  (browser, port 5000)    <-->   WebSocket     <-->    TCP port 4403
                                 Server                (4-byte framed)
                                    |
                                    v
                            [MeshForge Logic]
                            - ACK tracking
                            - Message logging
                            - Node tracking
                            - Gateway bridge
```

**What MeshForge can do in the middle**:
- **Log all packets** for monitoring/analytics
- **Track nodes** by observing NodeInfo packets
- **Monitor ACKs** by watching ROUTING_APP packets
- **Inject packets** (e.g., MeshForge-originated messages)
- **Multiplex clients** -- solve the single-client limitation

**What MeshForge must NOT do**:
- Modify packet IDs (breaks ACK matching)
- Consume/swallow packets (breaks client state)
- Add latency to ACK forwarding (causes timeouts)

### Question 4: Intercept ACK packets and forward them properly?

**YES.** ACKs are just `FromRadio` packets containing `ROUTING_APP` data with a
`request_id` field. MeshForge can:

1. Parse the `FromRadio` protobuf to identify ROUTING_APP packets
2. Extract the `request_id` to correlate with original messages
3. Log the ACK event for monitoring
4. Forward the packet unchanged to the web client
5. The web client's `queue.processAck()` handles the rest

The Python `meshtastic` library already has protobuf definitions for all of this.

---

## Part 8: Proposed MeshForge Architecture

### Option A: Transparent WebSocket Proxy (Simplest)

```python
# MeshForge WebSocket proxy -- transparent binary bridge
import asyncio
import websockets
import socket

async def proxy_handler(websocket, path):
    """Bridge WebSocket client to meshtasticd TCP port 4403."""
    # Connect to meshtasticd
    tcp_reader, tcp_writer = await asyncio.open_connection('127.0.0.1', 4403)

    async def ws_to_tcp():
        async for message in websocket:
            # Forward binary WebSocket frame to TCP (4-byte framing included)
            tcp_writer.write(message)
            await tcp_writer.drain()

    async def tcp_to_ws():
        while True:
            data = await tcp_reader.read(4096)
            if not data:
                break
            await websocket.send(data)

    await asyncio.gather(ws_to_tcp(), tcp_to_ws())
```

**Pros**: Minimal code, no protobuf parsing needed, just byte forwarding.
**Cons**: Cannot inspect/log packets without adding parsing.

### Option B: Protocol-Aware Proxy (Recommended)

```python
# MeshForge protocol-aware proxy
import asyncio
import struct
from meshtastic.protobuf import mesh_pb2

START1 = 0x94
START2 = 0xC3

def parse_framed_packet(data: bytes) -> list[bytes]:
    """Extract protobuf payloads from 4-byte framed stream."""
    packets = []
    i = 0
    while i < len(data) - 3:
        if data[i] == START1 and data[i+1] == START2:
            length = struct.unpack('>H', data[i+2:i+4])[0]
            if length <= 512 and i + 4 + length <= len(data):
                packets.append(data[i+4:i+4+length])
                i += 4 + length
                continue
        i += 1
    return packets

def inspect_from_radio(payload: bytes):
    """Observe a FromRadio packet without modifying it."""
    fr = mesh_pb2.FromRadio()
    fr.ParseFromString(payload)
    if fr.HasField('packet'):
        mp = fr.packet
        if mp.decoded.portnum == mesh_pb2.PortNum.ROUTING_APP:
            # This is an ACK/NACK
            routing = mesh_pb2.Routing()
            routing.ParseFromString(mp.decoded.payload)
            log_ack(mp.from_field, mp.to, mp.decoded.request_id, routing)
        elif mp.decoded.portnum == mesh_pb2.PortNum.TEXT_MESSAGE_APP:
            log_message(mp)
        elif mp.decoded.portnum == mesh_pb2.PortNum.NODEINFO_APP:
            update_node_tracker(mp)
```

**Pros**: Full visibility into mesh traffic, ACK tracking, node monitoring.
**Cons**: More complex, must handle protobuf parsing errors gracefully.

### Option C: Custom Transport Plugin for Web Client

Create a `@meshforge/transport-websocket` package:

```typescript
import type { Transport, DeviceOutput } from "@meshtastic/core";

export class TransportMeshForge {
  static async create(url: string): Promise<Transport> {
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    const fromDevice = new ReadableStream<DeviceOutput>({
      start(controller) {
        ws.onmessage = (event) => {
          controller.enqueue({
            type: "packet",
            data: new Uint8Array(event.data),
          });
        };
        ws.onclose = () => controller.close();
      },
    });

    const toDevice = new WritableStream<Uint8Array>({
      write(chunk) {
        ws.send(chunk);
      },
    });

    return {
      fromDevice,
      toDevice,
      disconnect: async () => ws.close(),
    };
  }
}
```

This would plug directly into the Meshtastic web client's `MeshDevice` constructor.

---

## Part 9: Key Technical Considerations

### The Single-Client Problem

meshtasticd's HTTP API (`/api/v1/fromradio`) only supports **one** client at a time.
If the web client is polling fromradio, MeshForge's Python bridge cannot also read from it.

**Solutions**:
1. **Use TCP port 4403** instead of HTTP API -- TCP supports the same protocol
2. **MeshForge becomes the sole TCP client** and multiplexes to WebSocket clients
3. **Use serial connection** if meshtasticd is on the same machine

### Framing Decision: Include or Strip?

The 4-byte framing (`0x94 0xC3 + length`) is used on serial and TCP.
For WebSocket, each message IS a frame, so you could:

- **Option A**: Forward raw bytes including 4-byte headers (simple passthrough)
- **Option B**: Strip headers on receive, add headers on send (cleaner but more work)

The `ws-serial-gateway` project uses Option A (transparent passthrough).
PR #998's WebSocket transport also appears to use raw binary frames.

**Recommendation**: Use Option A (passthrough) for simplicity.

### CORS Considerations

If serving the web client from MeshForge (port 5000) and the WebSocket is also on
port 5000 (e.g., `ws://localhost:5000/ws`), there are no CORS issues -- same origin.

If they're on different ports, standard WebSocket connections are not subject to CORS
(WebSocket is not same-origin restricted for the connection itself, though the
initial HTTP upgrade is).

### meshtasticd TCP Port 4403

- Default Meshtastic TCP port: **4403**
- Protocol: Same 4-byte framed protobuf as serial
- MeshForge's Python library can connect:
  ```python
  import meshtastic.tcp_interface
  iface = meshtastic.tcp_interface.TCPInterface(hostname="localhost", portNumber=4403)
  ```
- Or use raw sockets for more control (just 4-byte framing over TCP)

---

## Part 10: Summary & Recommendations

### What's Proven to Work

1. The Meshtastic `Transport` interface is simple and pluggable (3 members)
2. WebSocket transport has been demonstrated (PR #998, ws-serial-gateway)
3. The web client is just static files -- trivially self-hostable
4. JSON + base64 WebSocket proxying works (liamcottle's proxy)
5. Raw binary WebSocket proxying works (ws-serial-gateway)

### Recommended MeshForge Approach

1. **Phase 1**: Serve the Meshtastic web client's static files from MeshForge's
   web server (port 5000). Download pre-built assets from the official Docker image.

2. **Phase 2**: Implement a WebSocket proxy on MeshForge that bridges to meshtasticd
   TCP port 4403. Use transparent binary forwarding (4-byte framing passthrough).

3. **Phase 3**: Add protocol-aware inspection -- parse FromRadio packets to track
   nodes, log messages, monitor ACKs, without modifying the packet stream.

4. **Phase 4**: Create a lightweight `TransportMeshForge` TypeScript module that
   the web client can use to connect via MeshForge's WebSocket endpoint instead
   of direct HTTP/serial/BLE.

### Why This Matters for MeshForge

- **Single pane of glass**: Users access mesh monitoring AND device configuration
  from one URL
- **No daemon dependency for browsers**: Firefox/Safari users get access via WebSocket
  (bypasses the Chromium-only Web Serial limitation)
- **Multi-client support**: Multiple users can monitor the same radio simultaneously
- **NOC integration**: MeshForge can observe ALL traffic for its monitoring dashboards
  while the web client maintains full device control
- **Firmware v2.7.0 gap**: Since the web UI was removed from firmware, users NEED
  a self-hosted solution -- MeshForge can fill that gap

---

## Sources

- [Web Serial API Spec (WICG)](https://wicg.github.io/serial/)
- [MDN Web Serial API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Serial_API)
- [Chrome Developers: Read and Write to Serial Ports](https://developer.chrome.com/docs/capabilities/serial)
- [Can I Use: Web Serial](https://caniuse.com/web-serial)
- [Meshtastic Web Monorepo (GitHub)](https://github.com/meshtastic/web)
- [Meshtastic Web Client Overview](https://meshtastic.org/docs/software/web-client/)
- [Meshtastic JS Development Docs](https://meshtastic.org/docs/development/js/)
- [@meshtastic/core on JSR](https://jsr.io/@meshtastic/core)
- [@meshtastic/transport-web-serial on npm](https://www.npmjs.com/package/@meshtastic/transport-web-serial)
- [Meshtastic Client API (Serial/TCP/BLE)](https://meshtastic.org/docs/development/device/client-api/)
- [Meshtastic HTTP API](https://meshtastic.org/docs/development/device/http-api/)
- [Meshtastic Mesh Broadcast Algorithm (ACK)](https://meshtastic.org/docs/overview/mesh-algo/)
- [liamcottle/meshtastic-websocket-proxy](https://github.com/liamcottle/meshtastic-websocket-proxy)
- [WillerZ/ws-serial-gateway](https://github.com/WillerZ/ws-serial-gateway)
- [PR #998: WebSocket Transport for Meshtastic Web](https://github.com/meshtastic/web/pull/998)
- [liamcottle/meshtxt](https://github.com/liamcottle/meshtxt)
- [fgadot/meshtastic-local-web-client (Docker)](https://github.com/fgadot/meshtastic-local-web-client)
- [Meshtastic Protobufs (buf.build)](https://buf.build/meshtastic/protobufs)
- [meshtastic/firmware StreamAPI.h](https://github.com/meshtastic/firmware/blob/master/src/mesh/StreamAPI.h)
- [Meshtastic Python Library](https://python.meshtastic.org/)
- [Bridging the Meshtastic Web Client to Serial Devices](https://blog.ipmotion.ca/bridging-the-meshtastic-web-client-to-serial-devices/)
- [Feature Request: Reliable ACKs for DMs](https://github.com/meshtastic/firmware/issues/8164)
- [Consider WebSockets (meshtastic.js Issue #12)](https://github.com/meshtastic/meshtastic.js/issues/12)
- [Web Client Self-Hosting (Discourse)](https://meshtastic.discourse.group/t/web-client-self-hosting/7689)

---
*Last Updated: 2026-02-09*
*Status: Research complete -- architecture validated, integration path clear*

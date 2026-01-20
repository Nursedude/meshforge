# MeshForge Daemon (meshforged) Design

## Vision

MeshForge is not a client. MeshForge IS the mesh network controller.

> "meshforge is meshtasticd" - This is paramount to avoiding persistent issues over connectivity, functionality, reliability.

## Core Principles

1. **Own everything** - MeshForge controls all services, ports, connections
2. **User certainty** - User must KNOW MeshForge is working as expected
3. **Turnkey solution** - Works out of box, AI assists when needed
4. **Learn from mistakes** - Knowledge base grows, solutions documented
5. **Keep user in domain** - Options, not exits

---

## Architecture

### Layer Model

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interfaces                          │
│   ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐ │
│   │  Rich CLI │   │  GTK App  │   │  Web UI   │   │  Dude AI  │ │
│   └─────┬─────┘   └─────┬─────┘   └─────┬─────┘   └─────┬─────┘ │
└─────────┼───────────────┼───────────────┼───────────────┼───────┘
          │               │               │               │
          └───────────────┴───────┬───────┴───────────────┘
                                  │ API (REST/WebSocket)
┌─────────────────────────────────┼───────────────────────────────┐
│                          meshforged                              │
│  ┌──────────────────────────────┴───────────────────────────┐   │
│  │                      API Server                           │   │
│  │  - REST endpoints for commands                            │   │
│  │  - WebSocket for real-time updates                        │   │
│  │  - Authentication (local only by default)                 │   │
│  └──────────────────────────────┬───────────────────────────┘   │
│                                 │                                │
│  ┌──────────────┐  ┌────────────┴────────────┐  ┌─────────────┐ │
│  │   Service    │  │     Connection Pool     │  │   Health    │ │
│  │   Manager    │  │                         │  │   Monitor   │ │
│  │              │  │  - Owns TCP:4403        │  │             │ │
│  │  - Start     │  │  - Proxies access       │  │  - Checks   │ │
│  │  - Stop      │  │  - Single connection    │  │  - Alerts   │ │
│  │  - Restart   │  │  - Queue requests       │  │  - Remediate│ │
│  │  - Status    │  │                         │  │             │ │
│  └──────┬───────┘  └────────────┬────────────┘  └──────┬──────┘ │
│         │                       │                       │        │
│  ┌──────┴───────────────────────┴───────────────────────┴──────┐ │
│  │                       Event Bus                              │ │
│  │  - Service state changes                                     │ │
│  │  - Connection events                                         │ │
│  │  - Health alerts                                             │ │
│  │  - Message events                                            │ │
│  └──────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────┼───────────────────────────────┐
│                        Managed Services                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ meshtasticd  │  │    rnsd      │  │  hamclock    │  ...      │
│  │  (primary)   │  │              │  │              │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Service Manager

**Purpose**: Own the lifecycle of all services

```python
class ServiceManager:
    """
    Manages service lifecycle with pre/post checks.
    """

    services = {
        'meshtasticd': {
            'unit': 'meshtasticd.service',
            'required': True,
            'depends_on': [],
            'port': 4403,
            'health_check': check_meshtastic_tcp,
        },
        'rnsd': {
            'unit': 'rnsd.service',
            'required': False,
            'depends_on': [],
            'port': 37428,
            'health_check': check_rns_shared_instance,
        },
        'hamclock': {
            'unit': 'hamclock.service',
            'required': False,
            'depends_on': [],
            'port': 8080,
            'health_check': check_http_port,
        },
    }

    def start(self, service: str) -> ServiceResult:
        """
        Start service with pre/post validation.

        1. Pre-flight: Check dependencies, ports
        2. Action: systemctl start
        3. Post-check: Verify running, port open
        4. Report: Success or failure with details
        """

    def stop(self, service: str) -> ServiceResult:
        """Stop with graceful shutdown."""

    def restart(self, service: str) -> ServiceResult:
        """Restart with health verification."""

    def status(self, service: str) -> ServiceStatus:
        """Get detailed status including health."""
```

### 2. Connection Pool

**Purpose**: Single point of control for meshtasticd TCP

```python
class MeshtasticConnectionPool:
    """
    Owns the meshtasticd TCP connection.
    All access goes through here.
    """

    def __init__(self, host: str = 'localhost', port: int = 4403):
        self._connection = None
        self._interface = None
        self._lock = threading.Lock()
        self._queue = queue.Queue()

    def get_interface(self) -> MeshInterface:
        """
        Get the shared interface.
        Creates connection if needed.
        """
        with self._lock:
            if not self._interface:
                self._interface = self._connect()
            return self._interface

    def execute(self, command: Callable) -> Any:
        """
        Execute command through the connection.
        Thread-safe, queued execution.
        """

    def proxy_tcp(self, client_socket):
        """
        Proxy external TCP requests.
        Allows web UI to work through MeshForge.
        """
```

### 3. Health Monitor

**Purpose**: Continuous health checking with auto-remediation

```python
class HealthMonitor:
    """
    Watches service health and responds.
    """

    checks = [
        HealthCheck('meshtasticd_running', interval=10),
        HealthCheck('tcp_port_4403', interval=30),
        HealthCheck('radio_responding', interval=60),
        HealthCheck('rnsd_running', interval=30),
        HealthCheck('disk_space', interval=300),
        HealthCheck('memory_usage', interval=60),
    ]

    def on_failure(self, check: HealthCheck, result: CheckResult):
        """
        Handle health check failure.

        1. Log the failure
        2. Attempt auto-remediation if configured
        3. Alert user if remediation fails
        4. Add to knowledge base
        """
```

### 4. API Server

**Purpose**: Unified interface for all UIs

```python
# REST API Endpoints
POST /api/v1/service/{name}/start
POST /api/v1/service/{name}/stop
POST /api/v1/service/{name}/restart
GET  /api/v1/service/{name}/status
GET  /api/v1/services

GET  /api/v1/health
GET  /api/v1/health/{check}

POST /api/v1/meshtastic/send
GET  /api/v1/meshtastic/nodes
GET  /api/v1/meshtastic/config

GET  /api/v1/rns/status
GET  /api/v1/rns/paths

# WebSocket for real-time
WS   /api/v1/events
     - service_state_changed
     - health_alert
     - message_received
     - node_discovered
```

### 5. Event Bus

**Purpose**: Decouple components, enable reactive UIs

```python
class EventBus:
    """
    Publish/subscribe for internal events.
    """

    events = [
        'service.started',
        'service.stopped',
        'service.failed',
        'health.check.passed',
        'health.check.failed',
        'meshtastic.message.received',
        'meshtastic.node.discovered',
        'rns.announce.received',
        'user.action.required',
    ]

    def publish(self, event: str, data: dict):
        """Publish event to all subscribers."""

    def subscribe(self, event: str, callback: Callable):
        """Subscribe to event."""
```

---

## Port Ownership

### Before (Current)
```
meshtasticd listens on :4403
  ├── Web UI connects directly
  ├── Meshtastic CLI connects directly
  ├── MeshForge connects as client
  └── Conflicts occur
```

### After (meshforged)
```
meshforged owns :4403 (proxy)
  ├── meshtasticd listens on :4403 (localhost only)
  ├── meshforged maintains single connection
  └── All external access through meshforged API
```

### Implementation

```python
# /etc/meshtasticd/config.yaml (modified)
Webserver:
  Port: 9443
  BindAddress: 127.0.0.1  # Local only - proxied by meshforged

# meshforged proxies external requests
```

---

## User Experience

### Post-Boot Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    System Boot Complete                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  meshforged starting...                                      │
│                                                              │
│  [✓] Hardware detected: MeshAdv-Pi-Hat                       │
│  [✓] meshtasticd started (PID 1234)                         │
│  [✓] Radio online: Short Turbo, Region US                    │
│  [✓] rnsd started (PID 1235)                                │
│  [✓] RNS connected to HawaiiNet                              │
│                                                              │
│  MeshForge is ready.                                         │
│                                                              │
│  Run 'meshforge' to open the dashboard.                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Error Recovery

```
┌─────────────────────────────────────────────────────────────┐
│  [!] Health Check Failed: meshtasticd not responding        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Diagnosis:                                                  │
│    - Service running but TCP port 4403 not responding       │
│    - Last successful check: 2 minutes ago                    │
│    - Similar issue occurred 3 days ago (auto-fixed)         │
│                                                              │
│  Attempting auto-recovery...                                 │
│    [→] Restarting meshtasticd                                │
│    [✓] Service restarted                                     │
│    [✓] TCP port responding                                   │
│    [✓] Radio online                                          │
│                                                              │
│  Recovery successful. Added to knowledge base.               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Interactive Guidance

```
┌─────────────────────────────────────────────────────────────┐
│  MeshForge detected a configuration issue:                   │
│                                                              │
│  Your Meshtastic preset is LONG_FAST but the nearby node    │
│  "WH6GXZ-Gateway" is using SHORT_TURBO.                     │
│                                                              │
│  Options:                                                    │
│    1. Change my preset to SHORT_TURBO (recommended)          │
│    2. Keep LONG_FAST (won't connect to gateway)              │
│    3. Learn more about presets                               │
│                                                              │
│  What would you like to do? [1/2/3]                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Dude AI Integration

### Context Awareness

Dude AI knows:
- User's callsign and location
- Hardware configuration
- Network topology
- Past issues and solutions
- Current system state

### Interaction Examples

```
User: "My messages aren't sending"

Dude AI: "I see meshtasticd is running and TCP port is healthy.
Let me check your radio status...

Found the issue: Your node shows 0 connected peers.
This usually means:
1. No other nodes in range
2. Wrong preset (you're on SHORT_TURBO, range ~1km)
3. Antenna issue

Would you like me to:
- Scan for nearby nodes
- Check antenna connection
- Explain preset options"
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Create meshforged skeleton
- [ ] Implement ServiceManager
- [ ] Implement ConnectionPool
- [ ] Basic health checks
- [ ] CLI integration

### Phase 2: Port Ownership (Week 3)
- [ ] TCP proxy for 4403
- [ ] Modify meshtasticd config for local-only
- [ ] Update all MeshForge code to use proxy

### Phase 3: API & Events (Week 4)
- [ ] REST API server
- [ ] WebSocket events
- [ ] Update GTK/Web to use API

### Phase 4: Intelligence (Week 5+)
- [ ] Auto-remediation logic
- [ ] Knowledge base integration
- [ ] Dude AI context provider
- [ ] Learning from user actions

---

## File Structure

```
src/
├── daemon/
│   ├── __init__.py
│   ├── meshforged.py         # Main daemon entry
│   ├── service_manager.py    # Service lifecycle
│   ├── connection_pool.py    # TCP connection ownership
│   ├── health_monitor.py     # Health checks
│   ├── event_bus.py          # Event pub/sub
│   └── api/
│       ├── __init__.py
│       ├── server.py         # API server
│       ├── routes.py         # REST endpoints
│       └── websocket.py      # WS handlers
```

---

## Success Criteria

1. **User runs `meshforge`** → Everything just works
2. **Service fails** → Auto-recovered before user notices
3. **User asks "why"** → Clear explanation provided
4. **New user installs** → Guided setup, working in 5 minutes
5. **Expert user** → Full control available when needed

---

*"MeshForge is meshtasticd. Own it. Control it. Make it reliable."*

---

Document Version: 1.0
Created: 2026-01-20
Author: Dude AI

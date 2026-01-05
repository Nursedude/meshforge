"""
MeshForge University - Course Management

Defines course structure, lessons, and content loading.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum


class Difficulty(Enum):
    """Course difficulty levels"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class Lesson:
    """Individual lesson within a course"""
    id: str
    title: str
    content: str  # Markdown content
    duration_minutes: int = 10
    has_assessment: bool = False
    prerequisites: List[str] = field(default_factory=list)
    resources: List[Dict[str, str]] = field(default_factory=list)
    panel_reference: Optional[str] = None  # Link to MeshForge panel

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'duration_minutes': self.duration_minutes,
            'has_assessment': self.has_assessment,
            'prerequisites': self.prerequisites,
            'resources': self.resources,
            'panel_reference': self.panel_reference,
        }


@dataclass
class Course:
    """Course containing multiple lessons"""
    id: str
    title: str
    description: str
    difficulty: Difficulty
    lessons: List[Lesson] = field(default_factory=list)
    icon: str = "school-symbolic"
    estimated_hours: float = 1.0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'difficulty': self.difficulty.value,
            'lessons': [l.to_dict() for l in self.lessons],
            'icon': self.icon,
            'estimated_hours': self.estimated_hours,
            'tags': self.tags,
        }


class CourseManager:
    """Manages course catalog and content"""

    def __init__(self):
        self.courses: Dict[str, Course] = {}
        self._load_builtin_courses()

    def _load_builtin_courses(self):
        """Load built-in course catalog"""
        # Course 1: Getting Started
        getting_started = Course(
            id="getting-started",
            title="Getting Started with MeshForge",
            description="Learn the basics of MeshForge and mesh networking",
            difficulty=Difficulty.BEGINNER,
            icon="media-playback-start-symbolic",
            estimated_hours=0.5,
            tags=["intro", "basics", "setup"],
            lessons=[
                Lesson(
                    id="gs-01-welcome",
                    title="Welcome to MeshForge",
                    duration_minutes=5,
                    content="""# Welcome to MeshForge

MeshForge is your complete toolkit for managing LoRa mesh networks.

## What You'll Learn

In this course, you'll discover:

- **What is a mesh network?** - How devices communicate without central infrastructure
- **MeshForge panels** - Navigate the application's features
- **Basic operations** - Start/stop services, view status, and monitor nodes

## Why Mesh Networks?

Mesh networks are self-healing, decentralized communication systems:

- **No internet required** - Works off-grid
- **Long range** - LoRa can reach 10+ miles with good line of sight
- **Community-powered** - Each node extends the network

## Let's Get Started

Click **Next** to learn about the MeshForge interface.
""",
                    resources=[
                        {"title": "Meshtastic Official", "url": "https://meshtastic.org"},
                    ]
                ),
                Lesson(
                    id="gs-02-interface",
                    title="Understanding the Interface",
                    duration_minutes=10,
                    panel_reference="dashboard",
                    content="""# The MeshForge Interface

MeshForge is organized into panels, each handling a specific task.

## Main Panels

| Panel | Purpose | Shortcut |
|-------|---------|----------|
| Dashboard | Overview & status | Ctrl+1 |
| Service | Start/stop meshtasticd | Ctrl+2 |
| Radio Config | Configure your node | Ctrl+5 |
| Node Map | Visualize network | Ctrl+7 |
| Tools | RF calculators | Ctrl+9 |

## Navigation Tips

- **Sidebar** - Click any panel name to switch
- **F9** - Toggle sidebar visibility
- **F11** - Fullscreen mode
- **Ctrl+Q** - Quit application

## Status Bar

The top status bar shows:
- Service status (running/stopped)
- Node count on mesh
- Service uptime

## Try It Now

1. Press **Ctrl+1** to go to Dashboard
2. Check the service status
3. Press **Ctrl+7** to view the Node Map
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="gs-03-service",
                    title="Managing the Meshtasticd Service",
                    duration_minutes=10,
                    panel_reference="service",
                    content="""# Service Management

The `meshtasticd` service is the heart of your mesh node.

## What is meshtasticd?

- Linux daemon that interfaces with LoRa hardware
- Handles message routing and encryption
- Exposes API on TCP port 4403

## Service States

| State | Meaning |
|-------|---------|
| Running | Node is active on mesh |
| Stopped | Node is offline |
| Failed | Error occurred - check logs |

## Basic Operations

### Starting the Service
```bash
sudo systemctl start meshtasticd
```

### Stopping the Service
```bash
sudo systemctl stop meshtasticd
```

### Checking Status
```bash
sudo systemctl status meshtasticd
```

## Using MeshForge

In the **Service Management** panel:

1. View current status
2. Click **Start** or **Stop** buttons
3. View live logs
4. Enable/disable autostart

## Hands-On Exercise

Navigate to the Service panel and:
1. Check if meshtasticd is running
2. Try stopping and starting the service
3. View the logs for any errors
""",
                    has_assessment=True,
                ),
            ]
        )
        self.courses[getting_started.id] = getting_started

        # Course 2: Mesh Networking Fundamentals
        mesh_fundamentals = Course(
            id="mesh-fundamentals",
            title="Mesh Networking Fundamentals",
            description="Understand how mesh networks work",
            difficulty=Difficulty.BEGINNER,
            icon="network-workgroup-symbolic",
            estimated_hours=1.0,
            tags=["mesh", "networking", "theory"],
            lessons=[
                Lesson(
                    id="mf-01-topology",
                    title="Network Topologies",
                    duration_minutes=15,
                    content="""# Network Topologies

Understanding network shapes helps you design better mesh networks.

## Traditional Topologies

### Star Topology
```
     [Node]
        |
[Node]-[HUB]-[Node]
        |
     [Node]
```
- Central point of failure
- Simple to manage
- Wi-Fi routers work this way

### Mesh Topology
```
[Node]---[Node]---[Node]
   \\       |       /
    \\      |      /
     [Node]---[Node]
        \\    /
         [Node]
```
- No single point of failure
- Self-healing
- Meshtastic uses this!

## Mesh Advantages

1. **Redundancy** - Multiple paths between nodes
2. **Range Extension** - Messages hop through nodes
3. **Resilience** - Network adapts to node failures

## Meshtastic Specifics

- **Hop Limit** - Max 7 hops by default
- **Flooding** - Messages broadcast to all neighbors
- **Store & Forward** - Nodes can cache messages

## Key Concept: Hop Count

When you send a message:
1. Your node broadcasts it
2. Neighbors rebroadcast (hop 1)
3. Their neighbors rebroadcast (hop 2)
4. ...continues until hop limit

Lower hop count = fresher message = higher priority
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="mf-02-lora",
                    title="LoRa Technology Basics",
                    duration_minutes=15,
                    content="""# LoRa Technology

LoRa (Long Range) is the radio technology powering Meshtastic.

## What Makes LoRa Special?

| Property | Value |
|----------|-------|
| Range | Up to 15+ km (line of sight) |
| Power | Very low (years on battery) |
| Data Rate | Low (0.3 - 50 kbps) |
| Frequency | 915 MHz (US), 868 MHz (EU), etc. |

## LoRa Parameters

### Spreading Factor (SF)
- SF7 = Fast, short range
- SF12 = Slow, long range
- Higher SF = more range, less speed

### Bandwidth (BW)
- 125 kHz, 250 kHz, 500 kHz
- Wider = faster, less range

### Coding Rate (CR)
- Error correction level
- 4/5, 4/6, 4/7, 4/8
- Higher = more redundancy

## Trade-offs

```
Range ←————————————→ Speed
  SF12, BW125          SF7, BW500
  Long Range           Short Fast
```

## Meshtastic Presets

| Preset | Use Case |
|--------|----------|
| Short Fast | Dense urban, quick messages |
| Medium | Balanced default |
| Long Slow | Maximum range |
| Very Long Slow | Extreme distance |

## Frequency Bands

- **US (FCC)**: 902-928 MHz
- **EU (ETSI)**: 863-870 MHz
- **ANZ**: 915-928 MHz

Always use the correct band for your region!
""",
                    has_assessment=True,
                    resources=[
                        {"title": "Semtech LoRa", "url": "https://www.semtech.com/lora"},
                    ]
                ),
            ]
        )
        self.courses[mesh_fundamentals.id] = mesh_fundamentals

        # Course 3: RF Propagation
        rf_course = Course(
            id="rf-propagation",
            title="RF Propagation Fundamentals",
            description="Learn how radio signals travel and optimize your setup",
            difficulty=Difficulty.INTERMEDIATE,
            icon="network-wireless-signal-excellent-symbolic",
            estimated_hours=1.5,
            tags=["rf", "radio", "propagation", "antenna"],
            lessons=[
                Lesson(
                    id="rf-01-basics",
                    title="Radio Wave Basics",
                    duration_minutes=15,
                    content="""# Radio Wave Fundamentals

Understanding radio waves helps you get the best range.

## Key Properties

### Frequency (MHz)
- How fast the wave oscillates
- Meshtastic: 868-928 MHz range
- Lower frequencies penetrate better

### Wavelength (λ)
```
λ = c / f
λ = 300 / f(MHz) meters
```
At 915 MHz: λ ≈ 33 cm

### Power (dBm)
- Decibels relative to 1 milliwatt
- 0 dBm = 1 mW
- 20 dBm = 100 mW
- 30 dBm = 1 W

## dB Math Made Easy

- **+3 dB** = Double the power
- **+10 dB** = 10x the power
- **-3 dB** = Half the power

## Path Loss

Signals weaken with distance (Free Space Path Loss):

```
FSPL = 20×log₁₀(d) + 20×log₁₀(f) + 20×log₁₀(4π/c)
```

Simplified for 915 MHz:
```
FSPL(dB) ≈ 32 + 20×log₁₀(d_km)
```

| Distance | Path Loss |
|----------|-----------|
| 1 km | 32 dB |
| 10 km | 52 dB |
| 100 km | 72 dB |
""",
                    panel_reference="tools",
                    has_assessment=True,
                ),
                Lesson(
                    id="rf-02-fresnel",
                    title="Fresnel Zones & Line of Sight",
                    duration_minutes=15,
                    content="""# Fresnel Zones

Clear line of sight isn't enough - you need Fresnel zone clearance.

## What is a Fresnel Zone?

An elliptical region around the direct line between antennas where radio waves travel.

```
        Fresnel Zone
    ___________________
   /                   \\
TX ●                     ● RX
   \\___________________/
         Ground
```

## First Fresnel Zone Radius

```
r = 17.3 × √(d / (4 × f))
```
Where:
- r = radius in meters
- d = distance in km
- f = frequency in GHz

At 915 MHz, 10 km:
```
r = 17.3 × √(10 / (4 × 0.915)) ≈ 28.6 m
```

## The 60% Rule

You need **60% clearance** of the first Fresnel zone for good signal:
- 100% blocked = 6 dB loss
- 60% clear = near-optimal

## Practical Example

For a 10 km link at 915 MHz:
- Fresnel radius at midpoint: ~29 m
- 60% clearance needed: ~17 m
- Add antenna heights accordingly

## Obstructions

| Obstruction | Effect |
|-------------|--------|
| Trees | 10-20 dB loss |
| Buildings | 15-30+ dB loss |
| Hills | Signal blocked |
| Water | Good reflection |

## Tools in MeshForge

Use the **System Tools** panel:
- Fresnel zone calculator
- Link budget calculator
- Antenna height planner
""",
                    panel_reference="tools",
                    has_assessment=True,
                    resources=[
                        {"title": "RF Line of Sight", "url": "https://www.everythingrf.com/rf-calculators/fresnel-zone-calculator"},
                    ]
                ),
            ]
        )
        self.courses[rf_course.id] = rf_course

        # Course 4: Advanced Configuration
        advanced_config = Course(
            id="advanced-config",
            title="Advanced Node Configuration",
            description="Master node settings and optimize performance",
            difficulty=Difficulty.INTERMEDIATE,
            icon="preferences-system-symbolic",
            estimated_hours=1.5,
            tags=["config", "advanced", "optimization"],
            lessons=[
                Lesson(
                    id="ac-01-channels",
                    title="Channel Configuration",
                    duration_minutes=15,
                    panel_reference="radio_config",
                    content="""# Channel Configuration

Channels define how your mesh communicates.

## Primary vs Secondary Channels

- **Primary (0)**: Main mesh communication
- **Secondary (1-7)**: Additional encrypted channels

## Channel Settings

### Name
- Identifies the channel
- Broadcast to other nodes

### PSK (Pre-Shared Key)
- Encryption key
- Options:
  - `none` - No encryption
  - `default` - Standard Meshtastic key
  - `random` - Generate unique key
  - Custom 32-byte hex

### Role
| Role | Purpose |
|------|---------|
| PRIMARY | Main channel |
| SECONDARY | Additional channel |
| DISABLED | Channel off |

## Channel Presets

MeshForge offers presets in Radio Config:

| Preset | SF | BW | Use Case |
|--------|----|----|----------|
| Short Fast | 7 | 250 | Urban, quick |
| Medium | 11 | 250 | Balanced |
| Long Slow | 11 | 125 | Range priority |
| Very Long | 12 | 125 | Maximum range |

## Best Practices

1. **Use unique PSK** for private groups
2. **Match settings** across all nodes
3. **Test presets** in your environment
4. **Document your config**
""",
                    has_assessment=True,
                ),
            ]
        )
        self.courses[advanced_config.id] = advanced_config

        # Course 5: Reticulum/RNS
        rns_course = Course(
            id="reticulum-rns",
            title="Reticulum Network Stack",
            description="Build resilient networks with RNS",
            difficulty=Difficulty.ADVANCED,
            icon="network-transmit-receive-symbolic",
            estimated_hours=2.0,
            tags=["rns", "reticulum", "lxmf", "advanced"],
            lessons=[
                Lesson(
                    id="rns-01-intro",
                    title="Introduction to Reticulum",
                    duration_minutes=20,
                    panel_reference="rns",
                    content="""# Reticulum Network Stack

Reticulum (RNS) is a cryptographic networking stack for resilient communications.

## What is Reticulum?

- **Delay-tolerant** - Works with intermittent connectivity
- **Encrypted by default** - End-to-end encryption
- **Transport agnostic** - LoRa, TCP, Serial, etc.
- **Self-configuring** - No manual routing needed

## Key Concepts

### Destinations
Unique cryptographic identities:
```
<hash>.<type>.<app>.<aspect>
```

### Interfaces
Physical transport layers:
- LoRa (via Meshtastic)
- TCP (internet bridging)
- Serial (direct connect)

### Links
Encrypted tunnels between destinations.

## LXMF Protocol

Lightweight Extensible Message Format:
- Store-and-forward messaging
- Works over Reticulum
- Powers Sideband, NomadNet

## MeshForge Integration

The RNS panel provides:
- Interface management
- Gateway configuration
- LXMF messaging
- Network statistics

## Why Use RNS?

| Feature | Meshtastic | RNS |
|---------|------------|-----|
| Encryption | Channel-based | Per-message |
| Routing | Flooding | Address-based |
| Store & Forward | Limited | Full |
| Internet Bridge | Basic | Advanced |
""",
                    has_assessment=True,
                    resources=[
                        {"title": "Reticulum Docs", "url": "https://reticulum.network"},
                    ]
                ),
            ]
        )
        self.courses[rns_course.id] = rns_course

        # Course 6: AREDN Integration
        aredn_course = Course(
            id="aredn-mesh",
            title="AREDN Mesh Networks",
            description="Amateur radio emergency data networks",
            difficulty=Difficulty.ADVANCED,
            icon="network-server-symbolic",
            estimated_hours=2.0,
            tags=["aredn", "ham", "amateur", "wifi"],
            lessons=[
                Lesson(
                    id="aredn-01-intro",
                    title="Introduction to AREDN",
                    duration_minutes=20,
                    panel_reference="aredn",
                    content="""# AREDN Mesh Networks

Amateur Radio Emergency Data Network - high-bandwidth mesh for hams.

## What is AREDN?

- **Amateur radio** firmware for routers
- **Part 97** operation (licensed hams only)
- **WiFi frequencies** with ham allocations
- **Self-healing** mesh topology

## Requirements

- **License**: Technician class or higher
- **Hardware**: Supported router (MikroTik, Ubiquiti)
- **Frequency**: 900 MHz, 2.4 GHz, 5.8 GHz

## AREDN vs Meshtastic

| Feature | Meshtastic | AREDN |
|---------|------------|-------|
| License | Unlicensed | Ham required |
| Range | 10+ km | 20+ km |
| Bandwidth | ~1 kbps | 1-50+ Mbps |
| Power | mW | Up to 10W |
| Use Case | Text/GPS | Video, VoIP |

## Network Architecture

```
[Node]=====[Node]=====[Node]
   |          |          |
[LAN]      [LAN]      [LAN]
```

- Nodes connect via RF
- Each node has local LAN
- Services advertised network-wide

## MeshForge Integration

The AREDN panel provides:
- Node discovery & scanning
- Link quality monitoring
- Service browser
- MikroTik setup wizard
""",
                    has_assessment=True,
                    resources=[
                        {"title": "AREDN Docs", "url": "https://docs.arednmesh.org"},
                    ]
                ),
            ]
        )
        self.courses[aredn_course.id] = aredn_course

        # Course 7: AI-Assisted Development
        ai_dev_course = Course(
            id="ai-development",
            title="AI-Assisted Development",
            description="Master AI coding, debugging, and code review for mesh systems",
            difficulty=Difficulty.INTERMEDIATE,
            icon="system-run-symbolic",
            estimated_hours=3.0,
            tags=["ai", "coding", "debugging", "security", "review"],
            lessons=[
                Lesson(
                    id="ai-01-intro",
                    title="Introduction to AI-Assisted Development",
                    duration_minutes=15,
                    content="""# AI-Assisted Development

Learn to leverage AI assistants for more effective mesh network software development.

## What is AI-Assisted Development?

AI-assisted development uses large language models and AI tools to:

- **Generate Code** - Create implementations from descriptions
- **Debug Issues** - Analyze errors and suggest fixes
- **Review Code** - Identify security and quality issues
- **Document** - Generate clear documentation

## Why AI for Mesh Development?

Mesh networking involves complex domains:

```
MESH DEVELOPMENT DOMAINS
========================
- RF propagation physics
- Protocol specifications
- Network topology
- Hardware interfaces
- Security patterns
- Regulatory compliance
```

AI assistants can help bridge knowledge gaps and accelerate development.

## Key Principles

1. **Context is Everything** - AI needs project context
2. **Trust but Verify** - Always review generated code
3. **Security First** - Never compromise on security
4. **Iterative Refinement** - Build on AI suggestions

## In This Course

You'll learn:
- Effective prompting for code generation
- Systematic debugging with AI
- Automated code review techniques
- Security best practices
""",
                    resources=[
                        {"title": "AI Dev Practices", "url": "docs/ai_development_practices.md"},
                    ]
                ),
                Lesson(
                    id="ai-02-prompting",
                    title="Effective Prompting for Code Generation",
                    duration_minutes=20,
                    content="""# Prompting for Code Generation

Writing effective prompts is key to getting useful code from AI.

## The CLEAR Framework

```
C - Context    : Project, language, constraints
L - Language   : Be specific and technical
E - Examples   : Show expected input/output
A - Ask        : Clear, specific request
R - Review     : Specify validation criteria
```

## Example: Mesh Message Handler

### Poor Prompt
> "Write a function to handle mesh messages"

### Better Prompt
> "Write a Python function that handles incoming Meshtastic mesh
> messages. The function should:
> - Accept a protobuf MeshPacket object
> - Validate the sender node ID (8 hex chars)
> - Check message size against MAX_PAYLOAD (237 bytes)
> - Log reception with RSSI and SNR values
> - Return a typed MessageResult dataclass
> - Use type hints and docstrings
> - Handle malformed packets gracefully"

## Domain-Specific Context

For mesh networking, always include:

```python
# CONTEXT TO PROVIDE
# ------------------
# LoRa constraints:
#   - Max payload: 237 bytes
#   - Duty cycle limits
#   - SF/BW/CR settings
#
# Meshtastic specifics:
#   - Node ID format
#   - Channel configuration
#   - Encryption expectations
#
# Security requirements:
#   - Input validation
#   - No command injection
#   - Secure defaults
```

## Iterative Refinement

1. **Start general** - Get basic structure
2. **Add constraints** - Security, performance
3. **Request tests** - Unit test generation
4. **Ask for review** - Security audit
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="ai-03-debugging",
                    title="AI-Powered Debugging Techniques",
                    duration_minutes=25,
                    content="""# Debugging with AI Assistance

Learn systematic approaches to debugging mesh network issues with AI.

## The TRACE Method

```
T - Track     : Capture full error context
R - Reproduce : Create minimal reproduction
A - Analyze   : Break down with AI assistance
C - Correct   : Apply targeted fix
E - Ensure    : Verify with tests
```

## Providing Error Context

### Minimal (Less Effective)
> "My mesh node isn't connecting"

### Comprehensive (More Effective)
> "Error when connecting to Meshtastic node:
>
> Environment:
> - OS: Raspberry Pi OS (Debian 12)
> - Python: 3.11.2
> - meshtastic: 2.2.10
> - Device: RAK WisBlock via /dev/ttyACM0
>
> Error:
> ```
> serial.serialutil.SerialException:
> [Errno 13] could not open port /dev/ttyACM0:
> [Errno 13] Permission denied
> ```
>
> Steps to reproduce:
> 1. Run `meshtasticd --serial-port /dev/ttyACM0`
> 2. Error appears immediately
>
> What I've tried:
> - Verified device exists with `ls -la /dev/ttyACM0`
> - User is in dialout group"

## RF-Specific Debugging

For mesh network issues, capture:

```python
debug_info = {
    'node_id': node.my_node_num,
    'hardware': node.hardware_model,
    'firmware': node.firmware_version,

    # RF metrics
    'rssi': packet.rx_rssi,
    'snr': packet.rx_snr,
    'hop_count': packet.hop_limit,

    # Timing
    'rx_time': packet.rx_time,
    'latency_ms': calculated_latency,

    # Channel config
    'channel': channel_settings,
    'modem_preset': modem_config,
}
```

## Common Mesh Issues

| Symptom | Likely Cause | Debug Focus |
|---------|--------------|-------------|
| No packets | RF/antenna | RSSI, connections |
| Intermittent | Interference | Channel, duty cycle |
| Slow | High hop count | Routing, topology |
| Timeout | Config mismatch | Channel settings |
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="ai-04-security",
                    title="Security-First AI Code Generation",
                    duration_minutes=20,
                    content="""# Security in AI-Generated Code

AI can introduce security vulnerabilities. Learn to identify and prevent them.

## OWASP Top 10 in Mesh Context

| Vulnerability | Mesh Risk | Prevention |
|--------------|-----------|------------|
| Injection | Command exec | Shell=False, validate |
| Auth Bypass | Node spoofing | Verify node IDs |
| Data Exposure | Message logging | Sanitize logs |
| XXE | Config parsing | Safe parsers |
| Broken Access | Admin functions | Role checks |

## Security Review Patterns

Always verify AI-generated code for:

### 1. Input Validation
```python
# BAD - AI might generate
def process_message(data):
    return json.loads(data)

# GOOD - Add validation
def process_message(data: str) -> dict:
    if not isinstance(data, str):
        raise TypeError("Expected string")
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("Message too large")
    return json.loads(data)
```

### 2. Command Execution
```python
# BAD - Command injection risk
def run_mesh_command(user_cmd):
    os.system(f"meshtastic {user_cmd}")

# GOOD - Safe execution
ALLOWED_COMMANDS = {'--info', '--nodes', '--ch-index'}

def run_mesh_command(cmd: str) -> str:
    if cmd not in ALLOWED_COMMANDS:
        raise ValueError(f"Command not allowed: {cmd}")
    result = subprocess.run(
        ['meshtastic', cmd],
        shell=False,
        capture_output=True,
        timeout=30
    )
    return result.stdout.decode()
```

### 3. Path Operations
```python
# BAD - Path traversal
def read_config(name):
    with open(f"/etc/meshforge/{name}") as f:
        return f.read()

# GOOD - Validate path
def read_config(name: str) -> str:
    safe_name = Path(name).name  # Strip directory
    config_path = Path("/etc/meshforge") / safe_name
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {name}")
    return config_path.read_text()
```

## Security Checklist for AI Code

```
□ All user input validated
□ No shell=True with variables
□ Paths resolved and checked
□ Credentials not hardcoded
□ Errors don't leak info
□ Logging sanitized
□ Dependencies audited
```
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="ai-05-review",
                    title="Automated Code Review with AI",
                    duration_minutes=20,
                    content="""# AI-Powered Code Review

Learn to use AI for systematic code review and quality assurance.

## Review Categories

### 1. Security Review
Focus on vulnerabilities and attack vectors.

### 2. Quality Review
Check readability, maintainability, patterns.

### 3. Performance Review
Identify bottlenecks and inefficiencies.

### 4. Domain Review
Mesh-specific correctness and best practices.

## Review Prompt Template

```
Review this [language] code for a mesh networking application:

```[code]```

Please analyze:

1. SECURITY
   - Input validation issues
   - Injection vulnerabilities
   - Authentication/authorization gaps
   - Sensitive data handling

2. QUALITY
   - Code organization
   - Naming conventions
   - Error handling
   - Documentation completeness

3. PERFORMANCE
   - Algorithm efficiency
   - Resource management
   - Unnecessary operations
   - Caching opportunities

4. MESH-SPECIFIC
   - Payload size considerations
   - Duty cycle awareness
   - Protocol compliance
   - RF metric handling

Format findings as:
[SEVERITY] Category: Issue
  Location: file:line
  Problem: description
  Fix: suggested solution
```

## Interpreting Review Results

| Severity | Action | Timeline |
|----------|--------|----------|
| CRITICAL | Must fix | Before merge |
| HIGH | Should fix | This sprint |
| MEDIUM | Plan fix | Next sprint |
| LOW | Consider | Backlog |
| INFO | Informational | Optional |

## Automated Review Integration

```python
# Example: Pre-commit review hook
def pre_commit_review(files: List[str]) -> bool:
    reviewer = AICodeReviewer()
    all_passed = True

    for file in files:
        findings = reviewer.review_file(file)
        critical = [f for f in findings
                   if f.severity == Severity.CRITICAL]

        if critical:
            print(f"BLOCKED: {file}")
            for finding in critical:
                print(f"  - {finding.message}")
            all_passed = False

    return all_passed
```
""",
                    has_assessment=True,
                ),
                Lesson(
                    id="ai-06-practical",
                    title="Practical Exercise: AI Development Workflow",
                    duration_minutes=30,
                    content="""# Practical: Complete AI Development Workflow

Apply everything you've learned in a real-world exercise.

## Scenario

You need to create a **Mesh Node Health Monitor** that:
- Checks node connectivity every 60 seconds
- Logs RSSI, SNR, and battery level
- Alerts when signal drops below threshold
- Stores metrics for analysis

## Step 1: Design with AI

Prompt the AI for architecture:

> "Design a Python class for monitoring Meshtastic node health.
> Requirements:
> - Periodic checks (configurable interval)
> - Metric collection: RSSI, SNR, battery, uptime
> - Alert thresholds with callbacks
> - SQLite storage for history
> - Async-compatible
>
> Provide class structure with method signatures."

## Step 2: Generate Implementation

Request implementation for each method:

> "Implement the check_node_health() method for the
> NodeHealthMonitor class. It should:
> - Connect to meshtasticd API on localhost:4403
> - Query node telemetry
> - Return NodeMetrics dataclass
> - Handle connection timeouts gracefully
> - Include type hints and docstring"

## Step 3: Debug Any Issues

If you encounter errors:

> "Error when running health check:
> ```
> ConnectionRefusedError: [Errno 111] Connection refused
> ```
>
> The meshtasticd service is running (verified with systemctl).
> Config shows api_port: 4403.
>
> What could cause this and how to fix?"

## Step 4: Security Review

Request security audit:

> "Review this NodeHealthMonitor implementation for
> security issues, particularly:
> - API connection handling
> - Data sanitization before storage
> - Error message information leakage
> - Configuration validation"

## Step 5: Test Generation

Generate tests:

> "Generate pytest tests for NodeHealthMonitor covering:
> - Successful health check
> - Connection failure handling
> - Threshold alert triggering
> - Database storage verification
> - Configuration validation"

## Expected Outcome

A production-ready monitoring component with:
- ✅ Clean, documented code
- ✅ Comprehensive error handling
- ✅ Security-reviewed implementation
- ✅ Test coverage

## Reflection Questions

1. What context was most important to provide?
2. Where did you need to correct AI suggestions?
3. What security issues were initially missed?
4. How would you improve the workflow?
""",
                    has_assessment=True,
                ),
            ]
        )
        self.courses[ai_dev_course.id] = ai_dev_course

    def get_course(self, course_id: str) -> Optional[Course]:
        """Get a course by ID"""
        return self.courses.get(course_id)

    def get_all_courses(self) -> List[Course]:
        """Get all available courses"""
        return list(self.courses.values())

    def get_courses_by_difficulty(self, difficulty: Difficulty) -> List[Course]:
        """Get courses filtered by difficulty"""
        return [c for c in self.courses.values() if c.difficulty == difficulty]

    def get_courses_by_tag(self, tag: str) -> List[Course]:
        """Get courses containing a specific tag"""
        return [c for c in self.courses.values() if tag.lower() in [t.lower() for t in c.tags]]

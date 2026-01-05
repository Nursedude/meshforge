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

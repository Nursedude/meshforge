# MeshForge Version Framework

> Three-Tier Product Architecture
> PRO | Amateur Radio | .io (Portable)

---

## Overview

MeshForge offers three distinct product tiers to serve different user needs:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MESHFORGE PRODUCT TIERS                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │             │    │             │    │             │         │
│  │  MeshForge  │    │  MeshForge  │    │  MeshForge  │         │
│  │    PRO      │    │   Amateur   │    │     .io     │         │
│  │             │    │             │    │             │         │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘         │
│         │                  │                  │                 │
│    Full Suite         Ham-Focused        Lightweight            │
│    Enterprise         Part 97 Tools      Web-First              │
│    All Features       ARES/RACES         Plugin-Based           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tier 1: MeshForge PRO

### Target Audience
- Professional network operators
- Emergency management agencies
- Research institutions
- Commercial IoT deployments
- Advanced hobbyists

### Feature Set

```
MESHFORGE PRO - Complete Feature Matrix
═══════════════════════════════════════

Core Systems
├── Dashboard & Monitoring
├── Service Management
├── Configuration Management
├── Radio Configuration
└── Hardware Detection

Network Features
├── Reticulum/RNS Integration
│   ├── LXMF Messaging
│   ├── Gateway Configuration
│   └── Network Bridging
├── AREDN Mesh Integration
│   ├── Node Discovery
│   ├── MikroTik Setup Wizard
│   └── VLAN Configuration
└── Meshtastic CLI Interface

Visualization
├── Interactive Node Map
│   ├── Real-time Tracking
│   ├── Offline Tiles
│   └── Topo Layers
├── Network Topology View
└── Signal Analysis

Tools
├── RF Calculators
├── Fresnel Zone Calculator
├── Link Budget Analysis
├── Antenna Planning
└── Propagation Modeling

Learning
├── MeshForge University
├── Interactive Assessments
└── Certification Tracking

Integration
├── MQTT Home Automation
├── API Access
├── Webhook Support
└── External Tool Plugins

Advanced
├── Multi-node Management
├── Fleet Deployment
├── Automated Provisioning
└── Enterprise Logging
```

### Technical Specifications

| Component | Specification |
|-----------|---------------|
| Platform | Linux (x86_64, ARM64) |
| GUI | GTK4 + libadwaita |
| Memory | 512MB+ recommended |
| Storage | 500MB+ |
| Python | 3.10+ |
| Display | 1024x768 minimum |

### Pricing Model
- **Open Source Core**: Always free
- **PRO Support License**: Optional paid support
- **Enterprise Deployment**: Custom consulting

---

## Tier 2: MeshForge Amateur Radio Edition

### Target Audience
- Licensed amateur radio operators
- ARES/RACES volunteers
- Ham radio clubs
- Emergency communication teams
- Part 97 experimenters

### Feature Set

```
MESHFORGE AMATEUR - Ham-Focused Features
════════════════════════════════════════

Core (Shared with PRO)
├── Dashboard
├── Service Management
├── Radio Configuration
└── Hardware Detection

Ham-Specific Features
├── Callsign Integration
│   ├── Automatic identification
│   ├── QRZ.com lookup
│   └── FCC database query
├── Band Plan Reference
│   ├── Part 97 allocations
│   ├── Mode recommendations
│   └── Power limits display
├── ARES/RACES Tools
│   ├── Net checklist
│   ├── Traffic handling forms
│   ├── ICS-213 message format
│   └── Tactical callsign management
└── Contest/Field Day Mode
    ├── Rapid logging
    ├── Duplicate checking
    └── Score tracking

AREDN Integration (Full)
├── Part 97 WiFi mesh
├── High-bandwidth data
└── Repeater linking

Propagation Tools
├── HamClock Integration
├── Space Weather
├── Band Conditions
└── Propagation Predictions

Digital Modes Support
├── APRS gateway
├── Winlink interface
├── JS8Call bridge
└── FT8/FT4 spotting

Learning
├── Ham University Track
│   ├── Technician prep
│   ├── General prep
│   └── Extra prep
├── Emergency Comms courses
└── RF fundamentals
```

### Compliance Features

```
REGULATORY COMPLIANCE
─────────────────────

□ Automatic station ID (configurable interval)
□ Power output verification
□ Frequency accuracy display
□ Third-party traffic restrictions
□ Prohibited content filtering
□ Logging for FCC inspection
□ Part 97 reference integration
```

### Distinct UI Elements

| Element | Amateur Edition Specific |
|---------|-------------------------|
| Status Bar | Shows callsign + grid square |
| Header | Part 97 quick reference |
| Sidebar | ARES/RACES section |
| Settings | Callsign configuration |
| About | License info display |

---

## Tier 3: MeshForge.io (Portable Edition)

### Target Audience
- Web-first users
- Casual mesh participants
- Mobile device users
- Plugin developers
- Try-before-install users

### Philosophy

```
MESHFORGE.IO DESIGN PRINCIPLES
══════════════════════════════

1. LIGHTWEIGHT
   - Minimal core footprint
   - Load only what's needed
   - Fast initial load

2. PORTABLE
   - Browser-based
   - No installation required
   - Works on any device

3. EXTENSIBLE
   - Plugin architecture
   - Community marketplace
   - Easy upgrade path

4. PROGRESSIVE
   - Basic → Advanced journey
   - Unlock features as needed
   - Paid plugins for advanced
```

### Core Features (Base .io)

```
MESHFORGE.IO BASE
═════════════════

Included (Free Forever)
├── Basic Dashboard
│   └── Node status, connection info
├── Simple Configuration
│   └── Essential radio settings
├── Map View (Basic)
│   └── Node locations, OpenStreetMap
├── Message Interface
│   └── Send/receive text
└── Getting Started Guide
    └── Intro lessons

Plugin-Enabled (Free)
├── RF Calculator Plugin
├── Band Plan Plugin
├── Basic Monitoring Plugin
└── Community Plugins

Plugin-Enabled (Premium)
├── Advanced AREDN Tools
├── Professional Mapping
├── Fleet Management
├── API Access
└── Priority Support
```

### Plugin Architecture

```
PLUGIN SYSTEM OVERVIEW
══════════════════════

┌─────────────────────────────────────────────────┐
│                 MeshForge.io Core               │
├─────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │ Event   │  │ State   │  │  UI     │        │
│  │ Bus     │  │ Manager │  │ Slots   │        │
│  └────┬────┘  └────┬────┘  └────┬────┘        │
│       │            │            │              │
│       └────────────┼────────────┘              │
│                    │                           │
│              Plugin API                        │
└────────────────────┼───────────────────────────┘
                     │
    ┌────────────────┼────────────────┐
    │                │                │
┌───┴───┐       ┌────┴────┐      ┌────┴────┐
│ Core  │       │Community│      │ Premium │
│Plugins│       │ Plugins │      │ Plugins │
└───────┘       └─────────┘      └─────────┘
```

### Upgrade Paths

```
                    UPGRADE PATHS
═══════════════════════════════════════════════════

 .io Base ────────► .io + Plugins ────────► PRO
    │                     │                   │
    │                     │                   │
    ▼                     ▼                   ▼
 Free forever       Modular paid        Full features
 Essential only     Pick what you need  Enterprise ready


 .io Base ────────► Amateur Edition
    │                     │
    │                     │
    ▼                     ▼
 Casual user         Licensed ham
 Exploring hobby     Part 97 compliance
```

---

## Feature Comparison Matrix

| Feature | PRO | Amateur | .io Base | .io + Plugins |
|---------|-----|---------|----------|---------------|
| Dashboard | Full | Full | Basic | Configurable |
| Service Mgmt | Yes | Yes | Limited | Plugin |
| Radio Config | Advanced | Advanced | Basic | Plugin |
| RNS/Reticulum | Yes | Yes | No | Plugin |
| AREDN | Yes | Yes | No | Plugin |
| Node Map | Full | Full | Basic | Plugin |
| HamClock | Yes | Yes | No | Plugin |
| RF Tools | Full | Full | Basic | Plugin |
| University | Full | Ham Track | Intro | Plugin |
| ARES/RACES | Yes | Yes | No | Plugin |
| Callsign Mgmt | Optional | Built-in | No | Plugin |
| API Access | Yes | Yes | No | Premium |
| Offline Mode | Yes | Yes | Limited | Plugin |
| Plugin Support | Yes | Yes | Core | Full |

---

## Technical Architecture

### Shared Core Library

```python
# src/core/meshforge_core.py

"""
MeshForge Core - Shared across all editions

This module provides the fundamental functionality
shared by PRO, Amateur, and .io editions.
"""

class MeshForgeCore:
    """
    Core functionality shared across all editions.

    Edition-specific features are loaded via:
    - Feature flags (PRO vs Amateur)
    - Plugin system (.io)
    """

    def __init__(self, edition: str = "pro"):
        self.edition = edition
        self.features = self._load_features()
        self.plugins = []

    def _load_features(self) -> dict:
        """Load features based on edition"""
        base_features = {
            "dashboard": True,
            "config": True,
            "radio": True,
            "hardware": True,
        }

        if self.edition == "pro":
            return {
                **base_features,
                "rns": True,
                "aredn": True,
                "university": True,
                "mqtt": True,
                "api": True,
            }
        elif self.edition == "amateur":
            return {
                **base_features,
                "rns": True,
                "aredn": True,
                "university": True,
                "callsign": True,
                "ares_races": True,
                "band_plan": True,
            }
        else:  # .io
            return {
                **base_features,
                "plugins": True,
            }

    def register_plugin(self, plugin):
        """Register a plugin (primarily for .io)"""
        if self.features.get("plugins"):
            self.plugins.append(plugin)
            plugin.activate(self)
```

### Edition Detection

```python
# src/core/edition.py

"""
Edition detection and feature gating
"""

import os
from enum import Enum

class Edition(Enum):
    PRO = "pro"
    AMATEUR = "amateur"
    IO = "io"

def detect_edition() -> Edition:
    """
    Detect which edition is running.

    Priority:
    1. Environment variable MESHFORGE_EDITION
    2. Config file ~/.config/meshforge/edition
    3. Default to PRO
    """
    # Check environment
    env_edition = os.environ.get("MESHFORGE_EDITION", "").lower()
    if env_edition in ["pro", "amateur", "io"]:
        return Edition(env_edition)

    # Check config
    config_path = Path.home() / ".config" / "meshforge" / "edition"
    if config_path.exists():
        edition_str = config_path.read_text().strip().lower()
        if edition_str in ["pro", "amateur", "io"]:
            return Edition(edition_str)

    # Default
    return Edition.PRO

def has_feature(feature: str) -> bool:
    """Check if current edition has a feature"""
    edition = detect_edition()
    features = get_edition_features(edition)
    return features.get(feature, False)
```

---

## UI Differentiation

### Color Schemes

| Edition | Primary | Accent | Theme |
|---------|---------|--------|-------|
| PRO | #1a73e8 (Blue) | #34a853 (Green) | Professional |
| Amateur | #fbbc04 (Gold) | #ea4335 (Red) | Ham tradition |
| .io | #673ab7 (Purple) | #00bcd4 (Cyan) | Modern web |

### Branding Elements

```
PRO Edition
───────────
Logo: Full MeshForge icon + "PRO" badge
Tagline: "Professional Mesh Management"
Splash: Network topology animation

Amateur Edition
───────────────
Logo: MeshForge icon + callsign field
Tagline: "When All Else Fails"
Splash: Morse code pattern animation

.io Edition
───────────
Logo: Simplified MeshForge mark
Tagline: "Mesh Made Simple"
Splash: Minimal loading indicator
```

---

## Deployment Options

### PRO Edition
```bash
# Full installation
curl -fsSL https://meshforge.org/install.sh | sudo bash

# Or pip
pip install meshforge[pro]

# Docker
docker run -d meshforge/pro
```

### Amateur Edition
```bash
# Amateur-specific
curl -fsSL https://meshforge.org/install.sh | sudo bash -s -- --edition amateur

# Or pip
pip install meshforge[amateur]
```

### .io Edition
```bash
# Web deployment
npm install @meshforge/io

# Self-hosted
docker run -d meshforge/io

# Or visit
https://app.meshforge.io
```

---

## Roadmap

### Phase 1: Foundation (Current)
- [x] PRO edition feature complete
- [x] Core library extraction
- [ ] Edition detection system
- [ ] Feature flag implementation

### Phase 2: Amateur Edition
- [ ] Callsign integration
- [ ] ARES/RACES tools
- [ ] Band plan reference
- [ ] Ham-specific UI theme

### Phase 3: .io Edition
- [ ] Plugin architecture
- [ ] Web interface
- [ ] Plugin marketplace
- [ ] Freemium model

### Phase 4: Ecosystem
- [ ] Cross-edition sync
- [ ] Shared plugin format
- [ ] Community marketplace
- [ ] Enterprise features

---

*This framework guides MeshForge product development.*
*Version: 1.0 | Date: 2026-01-05*

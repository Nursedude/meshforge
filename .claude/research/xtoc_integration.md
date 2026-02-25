# XTOC/XCOM Integration Research for MeshForge

> **Date:** 2026-02-22
> **Sources:** [XTOC Home](https://www.mkme.org/xtocapp/) | [XTOC Store](https://store.mkme.org/product/xtoc-tactical-operations-center-software-suite/) | [XCOM Store](https://store.mkme.org/product/xcom-offline-radio-communication-suite/)

## Overview

XTOC (Tactical Operations Center) and XCOM (Offline Radio Communication Suite) are commercial PWA products by MKME.org for offline-first tactical operations over low-bandwidth transports (Meshtastic, MeshCore, Reticulum/RNode, SMS, QR, MANET). This document analyzes which features and reliability patterns MeshForge should adopt.

**Key finding:** MeshForge already has ~90% of the foundational infrastructure. XTOC is a tactical UI layer — it creates structured packets and relies on external transports. MeshForge is the opposite — a networking powerhouse that could benefit from XTOC's structured data approach.

---

## XTOC Feature Analysis

### 1. Structured Message Templates (HIGH VALUE)

XTOC uses typed templates instead of free-form text:
- **SITREP** - Situation reports
- **TASK** - Work assignments with status
- **CONTACT** - Personnel/unit info
- **RESOURCE** - Equipment/supply tracking
- **ASSET** - Vehicle/equipment registry
- **ZONE** - Geographic area markings (circles/polygons)
- **MISSION** - Operation definitions
- **EVENT** - Timeline entries
- **CHECKIN/LOC** - Position reports (multi-unit)

**MeshForge today:** Has `CanonicalMessage` types (TEXT, TELEMETRY, POSITION, COMMAND, ACK, TRACEROUTE, NODEINFO) and ARES/RACES ICS-213 templates in `src/amateur/ares_races.py` with priority levels (ROUTINE/PRIORITY/IMMEDIATE/FLASH).

**Gap:** No SITREP, TASK, RESOURCE, or ZONE structured types.

### 2. Compact Packet Protocol (HIGH VALUE)

XTOC's `X1` protocol:
```
X1.<T>.<M>.<ID>.<P>/<N>.<PAYLOAD>
```
- `T` = template ID (1=SITREP, 4=CHECKIN, etc.)
- `M` = mode (C=CLEAR, S=SECURE)
- `ID` = Crockford Base32 message ID (dedup/reassembly)
- `P/N` = part / total parts
- Payload = base64url-encoded binary structured data
- Auto-chunking per transport limits
- Out-of-order reassembly + deduplication

**MeshForge today:** Has chunking in `messaging.py`, dedup via MD5 in `message_queue.py`. No transport-aware chunk sizing or structured binary packing.

### 3. Mesh Auto-Ingest (HIGH VALUE)

Received mesh packets are automatically decoded and applied to timeline + tactical map with zero operator clicks. MeshForge has `event_bus.py` but doesn't auto-classify or auto-route inbound structured messages.

### 4. Tactical Map (MEDIUM-HIGH VALUE)

XTOC features: zone markings, ATAK/KML/CoT integration, SATCOM/TLE overlay. MeshForge has Folium maps and node tracking but no zones, tactical markers, or ATAK interop.

### 5. Multi-Transport (MEDIUM VALUE)

Same packet works across: clipboard, QR, email, mesh, Reticulum/RNode, MANET, Winlink, voice relay. MeshForge already bridges Meshtastic/RNS/MeshCore but lacks QR code or manual relay workflows.

### 6. Encryption Modes (MEDIUM VALUE)

CLEAR (ham-legal, no encryption) vs SECURE (AES for non-ham). MeshForge relies on transport-level encryption only.

### 7. Decentralized Log (ALREADY HAVE)

Local-first, no central server. MeshForge already follows this pattern.

### 8. Offline PWA (LOW RELEVANCE)

Browser-based. MeshForge's TUI approach is better for headless mesh nodes.

---

## XCOM Companion App

Mostly radio reference tools (repeater maps, shortwave schedules, QSO logbook). MeshForge already covers mesh-relevant parts via `node_tracker.py` and `amateur_radio_mixin.py`.

---

## Reliability Comparison

### XTOC provides:
1. Out-of-order reassembly
2. Dedup by message ID
3. Transport-aware chunking
4. Store-and-forward positions

### MeshForge already has (more sophisticated):
1. Circuit breaker pattern
2. Exponential backoff retries
3. Dead letter queue
4. 14-state message lifecycle tracking
5. Priority queuing (LOW/NORMAL/HIGH/URGENT)
6. Bridge health monitoring (HEALTHY/DEGRADED/DISCONNECTED)

**MeshForge's reliability infrastructure exceeds XTOC's.** Adopt XTOC's out-of-order reassembly and transport-aware chunking; keep everything else.

---

## Implementation Roadmap

**Decision:** MeshForge-native format internally + X1 import/export for XTOC interop.

### Phase 1: Structured Tactical Messages
- New `src/tactical/` package: `models.py`, `codec.py`, `timeline.py`
- New `src/launcher_tui/tactical_ops_mixin.py`
- Extend `CanonicalMessage` with SITREP/TASK/CHECKIN/RESOURCE/ZONE/MISSION/EVENT/ASSET
- Auto-ingest via `message_routing.py` → timeline
- Event bus integration for real-time TUI updates

### Phase 2: Compact Packet Codec + X1 Interop
- New `src/tactical/x1_codec.py` (X1 import/export)
- New `src/tactical/chunker.py` (transport-aware, out-of-order reassembly)
- Per-transport limits: Meshtastic 228B, RNS/LXMF ~500B, SMS 160 chars

### Phase 3: Tactical Map Enhancements
- Zone polygons on Folium maps
- Tactical markers (incident, resource, hazard, checkpoint, rally)
- KML/KMZ export for ATAK
- CoT XML generator for TAK ecosystem

### Phase 4: QR Code Transport
- QR generation for tactical packets (optional `qrcode` dependency)
- TUI "Generate QR" action

### Phase 5: Ham Compliance Mode
- CLEAR/SECURE encryption mode on CanonicalMessage
- AES-256-GCM envelope for SECURE
- Visual badge in message display

---

## Integration Architecture

```
                    ┌─────────────────────────────────────┐
                    │         MeshForge Tactical Layer     │
                    │                                     │
  XTOC X1 Packets ─┤  x1_codec.py ──► models.py          │
                    │                    │                 │
  MeshForge Native ─┤  codec.py ────────┤                 │
                    │                    │                 │
                    │              timeline.py (SQLite)    │
                    │                    │                 │
                    │              event_bus ──► TUI       │
                    │                    │                 │
                    │              coverage_map (zones)    │
                    │                    │                 │
                    │              kml_export (ATAK)       │
                    └────────────────────┼────────────────┘
                                         │
                    ┌────────────────────┼────────────────┐
                    │     Existing MeshForge Gateway       │
                    │                                     │
                    │  canonical_message.py                │
                    │  message_routing.py (3-way)          │
                    │  message_queue.py (reliable)         │
                    │  rns_bridge.py / mqtt_bridge.py      │
                    └─────────────────────────────────────┘
                              │         │         │
                        Meshtastic    RNS      MeshCore
```

---

*22nd research document. Updated 2026-02-22.*

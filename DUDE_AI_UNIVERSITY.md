# DUDE AI UNIVERSITY: Project Continuity & Knowledge Base

**Filename:** DUDE_AI_UNIVERSITY.md
**Last Updated:** January 2026
**Maintainer:** Nursedude
**Status:** Active Development (Target: April 2026 Launch)

---

## 1. THE MISSION
**MeshForge** is not a toy. It is an **Infrastructure-Grade Network Appliance**.
It provides off-grid, failover communications by bridging **Reticulum Network Stack (RNS)** and **Meshtastic** (LoRa).

* **Primary Goal:** Reliability. If the grid fails, this code must not.
* **Target Date:** April 2026.
* **Target Audience:** Network Engineers, Ham Radio Operators, Emergency Comms.

---

## 2. THE ARCHITECTURE (The "April Shift")
We have moved away from a monolithic desktop app on the Pi. The system is now split into two distinct components:

### A. MeshForge (The Appliance)
* **Platform:** Raspberry Pi (Linux).
* **Nature:** Headless, Silent Daemon.
* **Role:** The "Muscle". Handles hardware (SPI/I2C/USB), runs the Bridge, and manages the meshtasticd service.
* **Interface:** REST API (FastAPI) & Secure Sockets. **NO GUI** (No GTK, No Web Server).

### B. Supervisor NOC (The Manager)
* **Platform:** Windows (Desktop).
* **Nature:** Rich GUI (High-performance visualization).
* **Role:** The "Brain". Handles mapping, configuration, telemetry visualization, and fleet management.
* **Connection:** Connects to MeshForge via Local Network (TCP) or RNS.

---

## 3. CORE VALUES & CODING STANDARDS

### The "Anti-Bloat" Rule
* **Principle:** "Perfection is achieved when there is nothing left to take away."
* **Rule:** If a library is not critical, remove it.
* **Rule:** The Raspberry Pi must run cool and lean. Minimize RAM usage.

### Security & Hardening
* **Rule:** The API must never run as root.
* **Rule:** All inputs from the bridge must be sanitized.
* **Rule:** "Dead Man's Switch" is mandatory for updates.

### Code Style
* **Language:** Python 3.10+
* **Typing:** Strict Type Hinting is required.
* **Testing:** TDD (Test Driven Development) where possible.

---

## 4. THE "GOLD NUGGETS" (Key Logic to Preserve)
1.  **Frequency Calculator:** The djb2 hash logic.
2.  **Hardware Detection:** The logic that auto-scans USB/SPI ports.
3.  **RNS Bridge:** The translation layer.

---

## 5. DEVELOPMENT ROADMAP
| Phase | Timeline | Focus |
| :--- | :--- | :--- |
| **1. Anti-Bloat** | Jan 15 - Feb 15 | Strip GTK/Web UI. Create main.py API skeleton. |
| **2. Security** | Feb 15 - Mar 15 | Hardening the Bridge. API Auth. |
| **3. Reliability** | Mar 15 - Apr 15 | "Dead Man's Switch" & Rollback logic. |
| **4. Launch** | April 2026 | Full Release. |

---

## 6. CONTEXT FOR AI ASSISTANTS
* **User:** Nursedude (Lead Architect).
* **Assistant:** Claude/Gemini (Co-Pilot).

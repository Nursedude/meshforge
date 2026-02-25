# MeshForge: Building the First Multi-Protocol Mesh Bridge with an AI Development Partner

*What happens when a HAM operator and Claude Code build infrastructure that didn't exist before*

**Published:** February 22, 2026
**Reading time:** ~3 minutes
**Author:** Nursedude (WH6GXZ)
**AI Partner:** Dude AI (Claude Opus 4.6)

---

## The Problem Nobody Was Solving

Here's the thing about mesh networks in 2026: they don't talk to each other.

Meshtastic runs LoRa at 915 MHz. Reticulum encrypts everything over any transport layer. MeshCore uses companion radios with a different protocol entirely. AREDN operates on ham microwave bands. Each ecosystem has its own tools, its own interfaces, its own community. If you're running emergency comms and your team has a mix of devices, you're running four separate networks that happen to be in the same room.

I'm a HAM General (WH6GXZ) and an infrastructure engineer by trade. I'm also a registered nurse. When I think about disaster response, I think about the person in the field who needs one screen, not four. Nobody was building the bridge between these protocols. So I started building it.

The catch: I'm one person. The scope of bridging four mesh ecosystems from the wire level up is not a weekend project. That's where the AI development model comes in.

---

## What MeshForge Actually Is

MeshForge is not a single application. It's an ecosystem spanning five repositories that collectively provide mesh network operations, monitoring, alerting, visualization, and tooling.

The core is a **Network Operations Center** that runs on a $35 Raspberry Pi. Plug in a LoRa radio, run the installer, and you get:

- **Gateway bridge** --- the first open-source Meshtastic-to-Reticulum bridge, rewritten in v0.5.4 to use MQTT transport with zero interference to existing web clients
- **42+ REST API endpoints** across four services (map server, Prometheus metrics, config API, protobuf device control)
- **Terminal UI** with 46 mixin-dispatched menus covering radio management, RF engineering, diagnostics, monitoring, and messaging
- **AI diagnostics** that work fully offline --- a 20+ topic knowledge base with rule-based analysis, no cloud dependency
- **RF engineering tools** --- link budgets, Fresnel zone calculations, path loss modeling, site planning with coverage maps
- **Traffic inspection** --- Wireshark-grade packet capture for both Meshtastic and RNS networks
- **Prometheus metrics** and coverage maps with SNR-based link quality visualization

On the alpha branch (v0.6.0), MeshForge adds **3-way routing** between Meshtastic, Reticulum, and MeshCore through a canonical message format that normalizes all three protocols into a unified representation. That's the part that didn't exist anywhere before.

The numbers: 274+ Python files. 153,000+ lines. 1,743 tests across 50 test files, 100% pass rate. Four enforced security rules. Zero violations.

---

## The AI Development Model

This is the part that matters for AI developers.

MeshForge is built with Claude Code as a full development partner. Not autocomplete. Not "generate a function." A persistent AI agent that holds context across the entire codebase and operates under a structured configuration.

The `CLAUDE.md` file in the repository root is effectively an operating system for AI-assisted development. It defines architecture rules, security constraints (no `shell=True`, no bare `except:`, no `Path.home()` under sudo), file size limits, service management patterns, and development principles. The AI agent --- we call it "Dude AI" --- operates as network engineer, physicist, programmer, and project manager within those constraints.

The human+AI workflow works like this: I set the vision and make architectural decisions. Claude executes systematically --- writing code, running 1,743 tests, self-auditing against security rules, managing the 55-file documentation system. When something goes wrong, we have a postmortem process. When the AI misses the same bug eight times (it happened --- port 37428, ask me about it), we document the failure pattern so it doesn't happen again.

The `auto_review.py` system runs self-audits across all 274+ files. The `diagnostic_engine.py` provides rule-based analysis. The `knowledge_base.py` holds 20+ mesh networking topics for offline reference. Every one of these was built through the human+AI partnership, tested, and documented.

This isn't theoretical. This is production infrastructure being built by one HAM operator and an AI agent, shipping beta releases to real deployments.

---

## Roadmap: Where This Goes

MeshForge follows a deliberate phase model toward v1.0:

**Stability** (current, v0.5.x) --- Eliminate technical debt. Every file under 1,500 lines. Gateway hardened with MQTT architecture. Service management centralized.

**Reliability** (v0.6-0.8) --- Hardware testing across RAK, Heltec, uConsole, and meshtasticd. Sensor integration. GPS tracking with GPX export. MeshCore 3-way bridge promoted from alpha.

**Intelligence** (v0.8-0.9) --- Predictive analytics for battery discharge and link quality forecasting. Natural language queries against the knowledge base. AI-assisted configuration review.

**v1.0** --- SDR spectrum analysis (RTL-SDR), NanoVNA antenna integration, firmware flashing, historical playback. The bar: any HAM can deploy confidently and extend with their own Claude agent.

The extensibility piece is deliberate. MeshForge's `CLAUDE.md` patterns library will let any developer with Claude Code run an agent against the codebase, write features, run the full test suite, and ship. The architecture is designed for that.

---

## The Challenges Ahead

**Hardware outpaces software testing.** We can write and test code faster than we can validate it on physical radios. The uConsole AIO V2 support is "code ready" --- the hardware arrives Q2 2026. That gap between code-ready and field-tested is real.

**Protocol incompatibilities are deep.** Meshtastic uses protobuf over LoRa. Reticulum uses its own cryptographic transport. MeshCore has a companion radio model. Bridging these isn't just format conversion --- it's reconciling fundamentally different assumptions about addressing, encryption, and routing.

**The community is niche.** The intersection of HAM operators, mesh networking enthusiasts, and AI developers is small. But it's exactly the kind of community that builds critical infrastructure. Emergency comms, off-grid communication, field science --- these use cases don't need millions of users. They need reliability.

**Turnkey is hard.** The last mile between "works for an engineer" and "works for a non-technical emergency responder" is the hardest engineering problem. We're not there yet. The TUI helps. The installer helps. But truly one-click deployment on heterogeneous hardware is still ahead.

---

## Get Involved

MeshForge is GPL-3.0, fully open source, and actively developed.

- **GitHub**: github.com/Nursedude/meshforge
- **Development Blog**: nursedude.substack.com
- **Callsign**: WH6GXZ

If you're building with Claude Code and want to see what sustained human+AI infrastructure development looks like at 153K+ lines of Python, 1,743 tests, and 26 releases --- come look at the codebase. If you're a HAM or mesh enthusiast who wants professional-grade network visibility without the complexity --- clone the repo, plug in a radio, and run the installer.

*Made with aloha for the mesh community.*

---

*Nursedude is a HAM General class operator (WH6GXZ), registered nurse (RN BSN), and infrastructure engineer building MeshForge from Hawaii. Dude AI is the Claude Code agent that serves as the project's AI development partner.*

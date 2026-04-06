# Second Brain: Building an AI Dev Partner for Mesh Network Operations

*By Nursedude (WH6GXZ) and Claude — April 2026*

---

## The Problem

You're a HAM operator running a 20-node mesh network across multiple protocols — Meshtastic, Reticulum, AREDN. Your infrastructure lives on five Raspberry Pis spread across your property and the island. Three Git repositories hold the software. Each Pi runs different services, different configurations, different deployment profiles.

Something breaks on moc1. The map server isn't showing live nodes. You SSH in, curl the API, read logs, check the systemd service, realize the process is stale from two weeks ago, restart it, verify. That's 15 minutes of context switching across terminals, and you've lost your train of thought on the feature you were building.

Now multiply that by every diagnostic, every deployment, every code review, every cross-repo refactor. The cognitive overhead isn't the code — it's the context.

## The Architecture

What if your AI coding assistant wasn't a stateless chat window, but a persistent team member with its own identity, its own access, and its own memory of your entire domain?

Here's what we built:

### Identity

Claude has its own SSH key pair (`claude@meshforge-fleet`, ed25519). It's not sharing the operator's credentials. It's a separate, auditable identity:

```
~/.claude/ssh/
  id_ed25519           # Claude's private key
  id_ed25519.pub       # Deployed to all 5 Pis
  config               # SSH aliases: moc, moc1, moc2, moc3, pi2w
```

Every action Claude takes on a remote Pi shows up in auth.log under this identity. Revoke one key and Claude loses access. The operator's keys are untouched.

### Fleet Access

Five Pis, all reachable:

| Alias | Role | What Claude can do |
|-------|------|--------------------|
| moc | Full deployment | Deploy code, read logs, check API status |
| moc1 | Full deployment + AREDN | Diagnose map issues, restart services, verify |
| moc2 | Full deployment | Deploy, monitor |
| moc3 | Behind AREDN router | Diagnose AREDN integration |
| pi2w | Field/QA (Pi 2W) | Test lite profile, check bot status |

### Persistent Memory

Claude maintains a file-based memory system that carries across sessions:

- **User profile**: Who the operator is, their expertise, their working style
- **Feedback**: "No silent failures." "Commit after every plan." "Clear pycache on Pi deployments."
- **Project context**: Deployment topology, open issues, the 3.3GB database lesson
- **References**: Repo locations, integration points, port assignments

This isn't conversation history — it's curated domain knowledge that gets read at the start of every session.

### Cross-Repository Awareness

Three repos, one domain:

```
/opt/meshforge              — NOC core, TUI, gateway bridge (60 handlers)
/opt/meshforge-maps         — Leaflet.js web map, 7-tab curses TUI
/opt/meshing_around_meshforge — Bot companion TUI (11 screens)
```

Claude knows how they integrate: meshforge launches meshforge-maps via systemd, meshing_around reads the bot's config.ini, meshforge-maps can run standalone or as a plugin. Changes in one repo may need changes in another.

## What This Enables

### Closed-Loop Operations

The traditional workflow:
1. Write code on dev machine
2. Push to GitHub
3. SSH into target Pi
4. Pull code
5. Clear pycache (easy to forget)
6. Restart service
7. Verify the change worked
8. Switch back to dev work

With Claude as a fleet partner, the entire cycle happens in one conversation:

```
Claude: [writes code, runs tests] → 994 passed
Claude: [pushes to GitHub]
Claude: ssh moc1 'git pull'
Claude: ssh moc1 'find ... -name __pycache__ -exec rm ...'
Claude: ssh moc1 'systemctl restart meshforge'
Claude: ssh moc1 'curl localhost:5000/api/status'
Claude: "Deployed. 11,642 nodes now rendering on moc1:5000."
```

No terminal switching. No lost context.

### Remote Diagnostics

Real example from today: the map server on moc1 wasn't showing live nodes. From VolcanoAI, Claude:

1. SSH'd into moc1
2. Hit `/api/status` — server running, collector active
3. Hit `/api/nodes/geojson` — 133 nodes with GPS, 90 without (total 223)
4. Checked meshtasticd `:9443` — HTTP API down, TCP `:4403` working
5. Found AREDN/RNS/MQTT collectors returning 0
6. Discovered the map server daemon was a stale process from March 23rd — restarting the systemd service didn't touch it

**Diagnosis time: 30 seconds.** What would have taken 15 minutes of manual SSH/curl/grep happened in a single conversation turn.

### Domain-Wide Quality Control

Claude audited all three TUIs in parallel:
- meshforge: 60 handlers, 90 menu items
- meshforge-maps: 7 tabs, 25 key bindings
- meshing_around: 11 screens, 5 action shortcuts

Found 25 silent failure patterns (`except: pass`) across all three repos. Fixed them in one session — replacing bare `pass` with proper `logger.debug()` calls. Committed and pushed to all three repos.

### Cross-Repo Feature Porting

meshforge-maps had public data fallback sources (meshmap.net, RMAP.world, AREDN worldmap). meshforge's built-in maps didn't. Claude:

1. Explored both codebases to understand the collector patterns
2. Created a new mixin file following meshforge's established pattern
3. Integrated it into the existing collector class
4. Added configuration flags (disabled by default)
5. Wrote 21 tests
6. Committed and pushed to meshforge
7. Deployed to moc1 via SSH
8. Verified 11,561 public nodes now available as fallback

## What Makes This Different

This is not ChatGPT in a browser. It's not GitHub Copilot suggesting completions. Here's what's structurally different:

**Persistent identity**: Claude has its own SSH key, its own Git author line (`Co-Authored-By: Claude Opus 4.6`), its own memory files. It's a team member, not a tool.

**Direct execution**: Claude doesn't suggest commands — it runs them. Edits files, runs tests, commits code, pushes repos, SSHs into remote machines, restarts services, verifies results.

**Accumulated domain knowledge**: After weeks of daily sessions, Claude knows: the deployment topology, the MQTT topic structure, the 3.3GB database incident, the user's preference for terse responses, that pycache must be cleared on Pi deployments, that there should be no silent failures anywhere.

**Cross-boundary awareness**: Most AI coding tools work within a single file or repo. Claude operates across repos, across machines, across network boundaries. A change in meshforge-maps may need a corresponding change in meshforge — Claude sees both sides.

**Closed-loop verification**: The conversation doesn't end at "I pushed the code." It ends at "I verified the code is running correctly on the target machine."

## Setup Guide

### Prerequisites

- Raspberry Pi (or any Linux machine) with Claude Code installed
- GitHub repos for your project
- SSH access to target machines

### Step 1: Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

### Step 2: Create Project Instructions

Create `CLAUDE.md` in your repo root. This is the instruction file Claude reads at the start of every session. Include:

- Source layout and key files
- Patterns to follow (and anti-patterns to avoid)
- Security rules
- Testing instructions
- Known gotchas

### Step 3: Generate Claude's SSH Identity

```bash
mkdir -p ~/.claude/ssh
ssh-keygen -t ed25519 -C "claude@your-project" \
  -f ~/.claude/ssh/id_ed25519 -N ""
```

Create `~/.claude/ssh/config`:
```
Host target-pi
  HostName 192.168.x.x
  User your-user
  IdentityFile ~/.claude/ssh/id_ed25519
```

Deploy the public key to target machines (from a regular terminal):
```bash
ssh-copy-id -i ~/.claude/ssh/id_ed25519.pub user@target
```

### Step 4: Build Memory Over Time

Claude's memory isn't a one-time setup. It accumulates:

- **User memories**: Your role, expertise, preferences
- **Feedback memories**: Corrections and confirmations that shape behavior
- **Project memories**: Deployment topology, active work, decisions made
- **Reference memories**: Where to find things in external systems

The more you work with Claude, the more context it carries forward.

## For HAM Operators and Science Disciplines

MeshForge was built for mesh network operations — but this pattern applies to any distributed system managed by a small team:

- **Emergency communications (CERT/ARES)**: Manage mesh nodes across a county, diagnose outages during incidents
- **Environmental monitoring**: Sensor networks on remote Pis, data collection and verification
- **Field research**: Deploy and maintain instrumentation across sites
- **Amateur radio clubs**: Shared infrastructure management with audit trail

The common thread: distributed hardware, multiple protocols, limited operators, and no tolerance for silent failures.

## What's Next

- **SSHFS mounts**: Mount remote Pi filesystems locally so Claude can read logs and edit configs as if they were local
- **MCP fleet server**: Structured API on each Pi for service control, health checks, deployment — becoming a meshforge TUI feature ("Fleet Management" tab)
- **Autonomous monitoring**: Scheduled Claude sessions that check fleet health and report anomalies

---

*This white paper was written during the session where the workflow was first demonstrated end-to-end. The moc1 diagnostic, the cross-repo quality audit, and the public data fallback deployment all happened in a single conversation.*

*73 de WH6GXZ*

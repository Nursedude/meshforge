# How to: clean up the radio's node database

**Where:** `5. Configuration → meshtasticd → Node DB Cleanup`
**What it can change:** the radio's list of known nodes (Reset), or the
`MaxNodes` limit in `/etc/meshtasticd/config.d/meshforge-overrides.yaml`.
`config.yaml` itself is never edited.

Use this when the meshtasticd web client crashes when you click a node, or
the node list is full of nodes with no name (usually picked up over MQTT).

---

## The three rows

| Row | What it does | Writes? |
|---|---|---|
| Scan for Phantom Nodes | Looks for nodes with no name. | No (it offers removal) |
| Reset Node Database (removes ALL nodes) | Clears every known node from the radio. | **Yes — the radio** |
| Check MaxNodes Setting | Shows the node limit and lets you lower it. | Yes — the overlay file |

### Scan for Phantom Nodes — usually NOT available on a Pi

The scan reads the radio's `/json/nodes` page, which **meshtasticd never
serves** — only ESP32 firmware does. On a Pi running meshtasticd you will see
`Scan Not Available` and the service's real state (running / NOT running /
UNKNOWN); that is expected, not a fault. If it says NOT running you are
offered the fix in-app. On a device that does serve the page, the scan lists
up to 20 nameless nodes and asks (default **No**) whether to remove them one
by one with `meshtastic --remove-node`; if your meshtastic CLI is too old for
that, it offers a full Reset instead (default **No**).

### Reset Node Database

1. Choose **Reset Node Database**.
2. Read the warning; the default answer is **No**.
3. **Yes** runs `meshtastic --host localhost:4403 --reset-nodedb`.

**What it costs:** the radio forgets every node. Nodes come back as they are
heard — nearby nodes in minutes, distant or quiet ones may take hours.
Favorites, names and positions you saw are gone until heard again. Your
radio's own settings, channels and keys are **not** touched.

`Reset Failed` shows the command's own error. `Command timed out` means the
radio did not answer in 30 s — check `5. Configuration → meshtasticd →
Service Status` before trying again.

### Check MaxNodes Setting

Shows the current limit and where it comes from (`overlay` or
`config.yaml`). Enter a new value between 10 and 500 (Esc keeps it). Rough
guide from the screen: 50 for a small local mesh, 100 for a mesh with MQTT,
200 for a large mesh. The new value goes into the overlay file; you are then
asked to restart meshtasticd (the new limit applies only after a restart).

## Undo

- A Reset cannot be undone — the nodes return only by being heard.
- MaxNodes: run Check MaxNodes Setting again and enter the old value, or
  remove the `MaxNodes` line from the overlay via `5. Configuration →
  meshtasticd → Edit Config Files → Active Hardware Configs`.

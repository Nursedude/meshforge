# mini-dudeai

A small, dependency-free, always-on **rule-loop agent**. Point it at signals
(files, HTTP JSON, a heartbeat) and it fires actions (ntfy push, file
annotation, escalation marker) when conditions appear — edge-triggered and
auto-off, so it alerts on the *transition*, not every tick.

It runs anywhere a stdlib Python 3.9+ runs: a Raspberry Pi, a
[uConsole](https://hackergadgets.com/), a server, your laptop.

## The model: LLM is the compiler, this is the runtime

Borrowed from [wireclaw.io](https://wireclaw.io):

- **The runtime** (this package) loops 24/7 with **no LLM in the hot path** —
  cheap, deterministic, survives reboots.
- **The compiler** (a human, or an LLM session like Claude) *edits the rules
  file* when invoked. It never sits in the loop.

A compiler proposes rules by writing a `<rules>.candidate` file; the runtime
validates and atomic-promotes it on the next tick. The runtime never writes the
canonical rules file itself. Same trust model for any future proposal (e.g.
memory deltas): the agent **proposes**, a human/session **ratifies**.

## Three ways to run it

```bash
# 1. Config file (declarative, no Python):
python3 -m mini_dudeai --config my_config.json
python3 -m mini_dudeai --config my_config.json --once   # one tick, for cron/smoke

# 2. A pre-wired preset bundle:
python3 -m mini_dudeai --preset meshforge_fleet

# 3. SDK — build it in Python with your own adapters (see below).
```

(A `mini-dudeai` console command is declared in `pyproject.toml`; it activates
once the package is pip-installed — packaging is a planned follow-on. Until then
use `python3 -m mini_dudeai`.)

## Config schema

See `configs/mini_dudeai_config.example.json` for a runnable example and
`configs/mini_dudeai_config.schema.json` for the full reference. Minimum:

```json
{
  "interval_s": 30,
  "rules_path": "~/my_rules.json",
  "candidate_path": "~/my_rules.json.candidate",
  "sources": [
    {"kind": "file_mtime", "path": "~/heartbeat", "condition_kind": "heartbeat_stale", "max_age_s": 300},
    {"kind": "http_json", "url": "http://localhost:8080/health",
     "condition_kind": "service_health", "items_path": "components",
     "subject_field": "component_name", "detail_field": "message"}
  ],
  "actions": {
    "ntfy": {"kind": "ntfy", "topic": "your-ntfy-topic"},
    "annotate": {"kind": "annotate", "path": "~/annotations.md"},
    "none": {"kind": "none"}
  }
}
```

Configs are validated before the engine builds (`validate_config`) — a missing
or misspelled field reports the bad field **and its path**, and lists every
error at once.

### Built-in sources

| kind | required | emits when |
|------|----------|-----------|
| `file_mtime` | `path`, `max_age_s` | a file's mtime is older than `max_age_s` (heartbeat/canary) |
| `json_file` | `path`, `condition_kind` | one Condition per item in a JSON file (`items_path` digs nested) |
| `http_json` | `url`, `condition_kind` | one Condition per item from a JSON HTTP endpoint |
| `boot_health` | `state_path`, `clean_exit_path`, `assessment_path` | the box rebooted uncleanly (stdlib-only crash detector) |

### Built-in actions

| kind | required | does |
|------|----------|------|
| `ntfy` | `topic` | POST to an [ntfy](https://ntfy.sh) topic (quiet "cleared" on edge-down) |
| `annotate` | `path` | append a markdown line to a file |
| `propose_escalation` | — | record a structured "look at this" marker in history (side-effect-free) |
| `none` | — | no-op; just records the fire in history |

## Rules

A rule matches Conditions by `kind` + `subject_glob` (+ any extra `match.*` key
that must equal the Condition's `extras`), and runs one action. Edge-triggered,
auto-off, with `cooldown_s`. `match.subject_exclude_globs` (optional list) makes
a rule skip subjects matching any pattern — so a catch-all (`subject_glob: "*"`)
can coexist with a specific known-normal suppressor. See
`configs/mini_dudeai_rules.example.json`.

```json
{
  "id": "service_down",
  "match": {"kind": "service_health", "subject_glob": "*"},
  "action": {"kind": "ntfy", "title": "[RED] service down", "message": "{subject}: {detail}", "priority": "urgent"},
  "cooldown_s": 900
}
```

## Extending it (SDK + registry)

Write a custom Source/Action in Python and register it by name so a JSON config
can reference it:

```python
from mini_dudeai import (
    RuleEngine, register_source, register_action,
    Source, Condition, Action, Outcome,
)

class SerialSensorSource(Source):
    name = "serial_sensor"
    def __init__(self, dev): self.dev = dev
    def collect(self):
        # read self.dev; yield Conditions; emit kind="source_error" on failure,
        # never raise — the engine treats a raising source as a source_error.
        yield Condition(kind="sensor", subject="temp", detail="42C")

register_source("serial_sensor", lambda spec: SerialSensorSource(spec["dev"]))
# now a config can use {"kind": "serial_sensor", "dev": "/dev/ttyUSB0"}
```

Or skip config entirely and wire a `RuleEngine` directly (see the package
docstring in `__init__.py`).

## The observation-only boundary

mini-dudeai **observes and notifies — it does not fix.** No `subprocess`, no
`systemctl`, no `os.system/popen/exec` anywhere in the engine or the built-in
adapters. It reads files/URLs and writes ntfy/annotations/history. This keeps it
safe to run unattended 24/7: the worst it can do on a bad rule is send a
notification. (Custom adapters you register are your responsibility — keep them
to the same bar if you want the same guarantee.)

## What it writes

- **state** (`state_path`) — per-rule edge state + counters (`fire_count_24h`,
  `currently_active`, `last_tick_ts`), atomic-written each tick.
- **history** (`history_path`) — append-only JSONL fire log; the artifact a
  cloud/LLM session reads to see "what fired since I was last here."

Both are plain JSON/JSONL — read them with `jq`, tail them, or load them in
Python via `StateStore` / by line.

## Warm-start brief + "dreams" synthesis

Two off-loop passes turn the raw state/history into something a cloud/LLM
session reads *first* on a warm invocation — so it doesn't start cold:

```bash
python3 -m mini_dudeai --preset meshforge_fleet --brief    # write mini_dudeai_brief.md
python3 -m mini_dudeai --preset meshforge_fleet --dream     # nightly synthesis
```

- **`--brief`** distills current state + recent history into a short
  "what's active, what's escalated, what fired" note (`mini_dudeai_brief.md`).
- **`--dream`** is a low-frequency (nightly) pass that runs *deterministic*
  detectors over the last 24h — chronic flapping, first-time subjects, sustained
  conditions, escalation roll-ups — and emits two artifacts: a first-person but
  diagnostic narrative (`mini_dudeai_dreams.md`) and append-only **candidate
  memory-deltas** (`mini_dudeai_memory_deltas.jsonl`). There is **no LLM** in the
  dream — it is pattern extraction, not generation.

Memory-deltas follow the same trust model as rules: the agent **proposes**, a
session **ratifies**. The daemon never writes canonical memory. A session
accepts/declines a proposal with `resolve_delta(path, key, "ratified"|"rejected")`;
the nightly pass dedups against still-`proposed` deltas so it never re-spams the
same finding. The brief surfaces a count of unratified deltas pointing at the
dream log. Wire `--dream` to a nightly timer
(`templates/systemd/meshforge-mini-dudeai-dream.timer`); it is fully decoupled
from the 30s tick loop, so it can never affect fire behavior.

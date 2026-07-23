"""Standalone ("dude-claw") preset — the personal-agent wiring.

Pi-brain + WireClaw-edge per `.claude/plans/standalone_wireclaw_variant.md`:
  - NatsSensorSource → per-tick OpenClaw sensor polls → kind="sensor_breach"
  - NatsAction → OpenClaw tool_exec actuation (idempotent state-set tools)
  - NtfyAction / FileAnnotateAction / NoopAction (the existing bundle)

Runs as a SECOND mini-dudeai instance beside the fleet daemon: every artifact
path is claw-suffixed, so the #80 single-instance flock (keyed on the state
file) never collides with the fleet instance. boot_health is deliberately NOT
wired — the fleet daemon on the same box already owns crash detection; two
boot-health writers would double-page every hard reset.

Operator values come from env (MF014 — never in repo), loaded by the claw
systemd unit via EnvironmentFile=~/.config/meshforge/mini_dudeai_claw.env:
  MINI_DUDEAI_NATS_SERVER   required  e.g. "localhost:4222" (brain-local bus)
  MINI_DUDEAI_NTFY_TOPIC    required  pages go to the operator's fleet topic
  MINI_DUDEAI_CLAW_DEVICE   required* WireClaw device name (e.g. dudeclaw-01)
  MINI_DUDEAI_NATS_TOKEN    optional  NATS auth token
  MINI_DUDEAI_CLAW_TEMP_THRESHOLD optional, default 55 (°C, chip_temp demo)
  MINI_DUDEAI_CLAW_SENSORS  optional  path to a JSON list of sensor specs
                            ([{device,sensor,threshold,op,tool}...]); when
                            set it REPLACES the default chip-temp sensor and
                            CLAW_DEVICE becomes optional.
  MINI_DUDEAI_CLAW_INSTANCE optional  instance id for a SECOND claw on the
                            same brain box (e.g. dudeclaw-02). When set, every
                            artifact basename is device-suffixed so this
                            instance's flock never collides with the primary
                            claw's. Unset -> primary (un-suffixed) basenames.

First boot seeds ~/mini_dudeai_claw_rules.json from
configs/mini_dudeai_rules.claw.json — only when the rules file does not exist
yet (a bootstrap, never an overwrite); afterwards the candidate/promotion
machinery owns all rule changes.
"""
from __future__ import annotations

import json
import os
import shutil

from ..actions import FileAnnotateAction, NoopAction, NtfyAction
from ..actions.nats_action import NatsAction
from ..candidate import seed_rules_path
from ..engine import RuleEngine
from ..sources.nats_sensor import NatsSensorSource
from .._util import log_info, log_warning

DEFAULT_TEMP_THRESHOLD_C = 55.0

# One constant, two consumers: build_engine's state_path default AND the
# claw_metrics_push tier probe (rules-tier freshness check) — independent
# hardcodes of this basename WOULD drift (honest_failure_modes #5).
CLAW_STATE_BASENAME = "mini_dudeai_claw_state.json"

# Same rule for the live-rules basename: build_engine's default AND
# promote_seed_rules' --seed claw live-path resolution.
CLAW_RULES_BASENAME = "mini_dudeai_claw_rules.json"
# repo-shipped seed. We derive the repo root from THIS file's location (configs/
# sits beside src/), but the configs/mini_dudeai_rules.<seed>.json layout itself
# comes from candidate.seed_rules_path so the bootstrap can't drift from it.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_SEED_PATH = seed_rules_path(_REPO_ROOT, "claw")


def _load_sensors(sensors_path: str | None, device: str | None,
                  temp_threshold: float) -> list[dict]:
    """Resolve the sensor list: explicit JSON file, else the chip-temp default.

    An unreadable/invalid sensors file fails LOUD at build time — falling
    back to the default sensor would be a silent mass-reconfiguration (the
    #80 "error reads as valid" class, applied at the authoring boundary).
    """
    if sensors_path:
        try:
            with open(os.path.expanduser(sensors_path)) as f:
                sensors = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(
                f"MINI_DUDEAI_CLAW_SENSORS={sensors_path!r} unreadable/invalid "
                f"({type(e).__name__}: {e}) — refusing to fall back to the "
                f"default sensor; fix or unset it") from e
        if not isinstance(sensors, list) or not sensors:
            raise ValueError(
                f"{sensors_path}: must be a non-empty JSON list of sensor specs")
        return sensors
    if not device:
        raise ValueError(
            "standalone preset requires MINI_DUDEAI_CLAW_DEVICE (the WireClaw "
            "device name, e.g. dudeclaw-01) unless MINI_DUDEAI_CLAW_SENSORS "
            "points at an explicit sensor list. Operator values live in "
            "~/.config/meshforge/mini_dudeai_claw.env (MF014).")
    # day-1 default: chip temperature via temperature_read — works with NO
    # device-side sensor registration
    return [{
        "device": device, "sensor": "chip_temp",
        "tool": "temperature_read",
        "threshold": temp_threshold, "op": "gt",
    }]


def _seed_rules_if_absent(rules_path: str) -> None:
    """One-time bootstrap: copy the repo seed when no rules file exists yet.

    Only fires on a brand-new install (file absent — nothing overwritten);
    every later change goes through candidate validation + promotion. A
    missing seed is warned, not fatal: the engine already warns loudly every
    tick about an empty ruleset."""
    if os.path.exists(rules_path):
        return
    if not os.path.exists(_SEED_PATH):
        log_warning(f"mini-dudeai claw: no rules at {rules_path} and no seed "
                    f"at {_SEED_PATH} — engine will run with 0 rules")
        return
    shutil.copyfile(_SEED_PATH, rules_path)
    log_info(f"mini-dudeai claw: seeded {rules_path} from {_SEED_PATH}")


def build_engine(
    home: str | None = None,
    rules_path: str | None = None,
    state_path: str | None = None,
    history_path: str | None = None,
    brief_path: str | None = None,
    annotate_path: str | None = None,
    nats_server: str | None = None,
    nats_token: str | None = None,
    claw_device: str | None = None,
    ntfy_topic: str | None = None,
    sensors_path: str | None = None,
    temp_threshold_c: float | None = None,
    sensor_timeout_s: float = 5.0,
    instance: str | None = None,
) -> RuleEngine:
    """Wire the standalone dude-claw engine. All knobs overridable for tests;
    operator-runtime defaults pull from env (see module docstring).

    ``instance`` (env ``MINI_DUDEAI_CLAW_INSTANCE``) enables a SECOND claw on
    the same brain box (dudeclaw-02): when non-empty, every artifact basename
    is device-suffixed via ``claw_telemetry.instance_basename`` so this
    instance's #80 flock (keyed on the state file) never collides with the
    primary claw's. Empty/unset -> the primary keeps its un-suffixed basenames
    (backward-compatible: dudeclaw-01's existing state/history is untouched).
    """
    from .._util import resolve_home
    from ..claw_telemetry import instance_basename
    home = home or resolve_home()

    instance = instance if instance is not None else os.environ.get(
        "MINI_DUDEAI_CLAW_INSTANCE")
    instance = (instance or "").strip()

    def _b(basename: str) -> str:
        # secondary instance -> device-suffixed basename; primary -> unchanged
        return instance_basename(basename, instance) if instance else basename

    rules_path = rules_path or os.path.join(home, _b(CLAW_RULES_BASENAME))
    state_path = state_path or os.path.join(home, _b(CLAW_STATE_BASENAME))
    history_path = history_path or os.path.join(
        home, _b("mini_dudeai_claw_history.jsonl"))
    brief_path = brief_path or os.path.join(home, _b("mini_dudeai_claw_brief.md"))
    annotate_path = annotate_path or os.path.join(
        home, _b("mini_dudeai_claw_annotations.md"))

    nats_server = nats_server or os.environ.get("MINI_DUDEAI_NATS_SERVER")
    if not nats_server:
        raise ValueError(
            "standalone preset requires MINI_DUDEAI_NATS_SERVER env var or "
            "nats_server= arg (the brain's NATS bus, e.g. localhost:4222). "
            "Operator values live in ~/.config/meshforge/mini_dudeai_claw.env, "
            "loaded by the systemd unit via EnvironmentFile= (MF014).")
    nats_token = nats_token or os.environ.get("MINI_DUDEAI_NATS_TOKEN") or None
    claw_device = claw_device or os.environ.get("MINI_DUDEAI_CLAW_DEVICE")
    ntfy_topic = ntfy_topic or os.environ.get("MINI_DUDEAI_NTFY_TOPIC")
    if not ntfy_topic:
        raise ValueError(
            "standalone preset requires MINI_DUDEAI_NTFY_TOPIC env var or "
            "ntfy_topic= arg (pages must reach the operator; MF014 keeps the "
            "topic out of the repo).")
    sensors_path = sensors_path or os.environ.get("MINI_DUDEAI_CLAW_SENSORS")
    if temp_threshold_c is None:
        raw = os.environ.get("MINI_DUDEAI_CLAW_TEMP_THRESHOLD",
                             str(DEFAULT_TEMP_THRESHOLD_C))
        try:
            temp_threshold_c = float(raw)
        except ValueError:
            raise ValueError(
                f"MINI_DUDEAI_CLAW_TEMP_THRESHOLD={raw!r} is not a number")

    sensors = _load_sensors(sensors_path, claw_device, temp_threshold_c)
    _seed_rules_if_absent(rules_path)

    sources = [
        NatsSensorSource(
            server=nats_server, sensors=sensors, kind="sensor_breach",
            token=nats_token, timeout_s=sensor_timeout_s, name="claw_sensors",
        ),
    ]
    actions = {
        "nats": NatsAction(server=nats_server, device=claw_device,
                           token=nats_token, timeout_s=sensor_timeout_s),
        "ntfy": NtfyAction(topic=ntfy_topic),
        "annotate": FileAnnotateAction(path=annotate_path),
        "none": NoopAction(),
    }
    return RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=rules_path + ".candidate",
        brief_path=brief_path,
        # boot_health stays with the fleet daemon — see module docstring
        clean_exit_path=None,
    )

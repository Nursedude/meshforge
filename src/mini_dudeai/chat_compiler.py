"""Phase B chat-compiler: English intent → ONE validated rule candidate.

The WireClaw insight, completed our way: **the LLM is a compiler, the rule
loop is the runtime.** A local LLM (Ollama, on the operator's own hardware —
no cloud, no internet in the loop) is prompted with the rule schema, the live
source/action vocabulary, and the existing ruleset; it emits one rule dict.
The operator RATIFIES it (front-ends own that step), and only then does
`write_candidate()` hand it to the running daemon, which validates and
promotes within a tick. The LLM never touches the hot path.

Trust model (same as the Arc 4 form): LLM proposes → operator ratifies →
runtime validates. Three independent gates; this module is the first one.

Honest failure modes (the #80 checklist, applied at write time):
  - Ollama unreachable / HTTP error / non-JSON reply → CompilerError, loud.
    Never a silent fallback to a template rule.
  - A structurally-invalid compiled rule gets ONE repair round (the validator
    errors are fed back verbatim); still invalid → CompilerError carrying the
    errors. We never "fix" the rule silently — the operator sees exactly what
    the model couldn't do.
  - Lint warnings (`lint_rules_document`) are returned for the ratification
    display, never swallowed — a typo'd match key must reach the human.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .actions.nats_action import IDEMPOTENT_TOOLS
from .candidate import (
    STRUCTURAL_MATCH_KEYS, WELL_KNOWN_EXTRAS_KEYS,
    lint_rules_document, validate_rules_document,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
# Local 3B-class models on a Pi think in tens of seconds; this bounds a
# wedged server, not a slow one.
DEFAULT_TIMEOUT_S = 180.0

_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")


class CompilerError(Exception):
    """Compilation failed loudly. .errors carries the specifics."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class OllamaBackend:
    """Minimal Ollama /api/chat client (stdlib urllib, JSON-format mode)."""

    def __init__(self, url: str = DEFAULT_OLLAMA_URL,
                 model: str = DEFAULT_MODEL,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = float(timeout_s)

    def complete(self, system: str, user: str) -> str:
        """One chat completion → the assistant text. Raises CompilerError on
        any transport/shape failure — the caller never sees a fake reply."""
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode()
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                reply = json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            raise CompilerError(
                f"Ollama at {self.url} failed: {type(e).__name__}: {e} — "
                f"is the server up and the model ({self.model}) pulled?") from e
        content = ((reply.get("message") or {}).get("content"))
        if not content:
            raise CompilerError(
                f"Ollama returned no message content: {str(reply)[:200]}")
        return content


def build_system_prompt(source_kinds: list[str], action_kinds: list[str],
                        existing_rules: list[dict],
                        condition_kinds: list[str] | None = None,
                        notes: str = "") -> str:
    """The compiler's whole worldview: schema + live vocabulary + examples.

    Built fresh per call from the LIVE registries so the prompt can never
    drift from what the runtime actually accepts (checklist #5: one source
    of truth, derived not duplicated).
    """
    existing_ids = sorted(r.get("id", "?") for r in existing_rules)
    cond_kinds = condition_kinds or ["sensor_breach", "source_error"]
    return f"""You compile ONE automation rule for mini-dudeai, a deterministic \
edge-triggered rule engine. Output ONLY a JSON object — the rule itself, no \
wrapper, no prose.

Rule schema (only these top-level keys):
  "id": short snake_case identifier, NOT one of {existing_ids}
  "match": object selecting conditions:
      "kind": one of {sorted(cond_kinds)} (REQUIRED)
      "subject_glob": shell glob over the condition subject (default "*").
          Sensor subjects look like "<device>.<sensor>", e.g. "dudeclaw-01.chip_temp".
      optional extras equality filters: only {sorted(WELL_KNOWN_EXTRAS_KEYS)} plus
          "device"/"sensor" for sensor conditions. NO other match keys — any
          other key is treated as an extras filter and usually a typo.
  "action": object, "kind" one of {sorted(action_kinds)}:
      "ntfy": page the operator. Optional "title", "message" (template with
          {{subject}} and {{detail}}), "priority" (min|default|high|urgent), "tags".
      "nats": drive the WireClaw edge node. "payload" = edge-up tool call,
          optional "payload_down" = edge-down call. Payloads are flat JSON with
          "tool" being ONLY one of {sorted(IDEMPOTENT_TOOLS)} (idempotent
          state-set tools — the retry queue may re-apply them).
          Examples: {{"tool":"led_set","r":255,"g":0,"b":0}},
                    {{"tool":"gpio_write","pin":5,"value":1}},
                    {{"tool":"display_print","row":1,"text":"TEMP HIGH"}}
      "annotate": append a note line. "none": record only.
  "grace_s": number — condition must HOLD this many seconds before firing
      (debounce; use 60+ for sensors so blips don't fire).
  "cooldown_s": number — minimum seconds between fires (use 600+).
  "annotation": one sentence for the human explaining the rule and its intent.

Engine semantics: a rule fires on edge_up (condition newly present, held past
grace_s) and on edge_down (condition cleared) — ntfy sends a quiet "cleared"
notice, nats sends payload_down if present. Sensor THRESHOLDS live in the
sensor configuration, NOT in rules: rules match the presence of a
"sensor_breach" condition. If the user asks for a different numeric threshold
than what is configured, still compile the rule and note the threshold request
in "annotation" so the operator adjusts the sensor config.

Choosing match.kind — do not conflate these:
  - "sensor_breach": a sensor READING crossed its threshold (hot, cold, low
    battery). The sensor is WORKING and reporting a bad value.
  - "source_error": the feed is DARK — reads failing, device unreachable,
    bus down, blind. Words like dark/blind/offline/unreachable/silent/failed
    mean source_error, never sensor_breach.
Durations: convert stated hold times to grace_s in SECONDS ("ten minutes"
means grace_s 600, "a minute" means 60).

Available source kinds (context): {sorted(source_kinds)}
{notes}

Example — intent: "page me urgently and turn the LED red when the claw chip \
overheats, clear it when it recovers":
{{"id": "chip_overheat_page", "match": {{"kind": "sensor_breach", \
"subject_glob": "*.chip_temp"}}, "action": {{"kind": "ntfy", "title": \
"[claw] chip overheat", "message": "{{subject}}: {{detail}}", "priority": \
"urgent", "tags": ["fire"]}}, "grace_s": 60, "cooldown_s": 1800, \
"annotation": "Pages urgently while the chip temp sensor is breaching; quiet \
cleared notice on recovery."}}

Compile exactly what the user asks. Do not invent devices or sensors not
implied by the intent or the context above."""


def _parse_rule_json(text: str) -> dict:
    """Strict parse of the model output → rule dict. The model is told to
    emit only JSON; tolerate (only) surrounding noise by extracting the
    outermost object. Anything else is an error, never a guess."""
    text = text.strip()
    try:
        obj = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise CompilerError(f"model output is not JSON: {text[:160]!r}")
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError as e:
            raise CompilerError(
                f"model output is not valid JSON ({e}): {text[:160]!r}")
    if isinstance(obj, dict) and isinstance(obj.get("rule"), dict):
        obj = obj["rule"]  # some models wrap despite instructions
    if not isinstance(obj, dict):
        raise CompilerError(f"model output is not a rule object: {text[:160]!r}")
    return obj


def _check_rule(rule: dict, existing_rules: list[dict],
                action_kinds: list[str]) -> list[str]:
    """Authoring-time checks beyond the document validator. Returns errors."""
    errors: list[str] = []
    rid = rule.get("id")
    if not isinstance(rid, str) or not _ID_RE.match(rid or ""):
        errors.append(f"id {rid!r} must be 3-64 chars of [a-z0-9_]")
    elif any(r.get("id") == rid for r in existing_rules):
        errors.append(f"id {rid!r} already exists — pick a new id")
    action = rule.get("action")
    if isinstance(action, dict) and action.get("kind") not in action_kinds:
        errors.append(
            f"action.kind {action.get('kind')!r} not in {sorted(action_kinds)}")
    for key in ("grace_s", "cooldown_s"):
        v = rule.get(key)
        if v is not None and not isinstance(v, (int, float)):
            errors.append(f"{key} must be a number, got {type(v).__name__}")
    unknown = set(rule) - {"id", "match", "action", "grace_s", "cooldown_s",
                           "annotation"}
    if unknown:
        errors.append(f"unknown rule keys {sorted(unknown)} — only "
                      f"id/match/action/grace_s/cooldown_s/annotation exist")
    # full-document validation (catches match/action shape)
    _, doc_errors = validate_rules_document(
        {"rules": existing_rules + [rule]})
    errors.extend(e for e in doc_errors if e not in errors)
    return errors


def compile_rule(intent: str, backend: OllamaBackend,
                 existing_rules: list[dict],
                 source_kinds: list[str], action_kinds: list[str],
                 condition_kinds: list[str] | None = None,
                 notes: str = "") -> tuple[dict, list[str]]:
    """English intent → (rule, lint_warnings). One repair round; then loud.

    The returned rule is validated against the FULL document it would join.
    Ratification and write_candidate() belong to the caller — this function
    never writes anything.
    """
    if not intent or not intent.strip():
        raise CompilerError("empty intent — describe the rule you want")
    system = build_system_prompt(source_kinds, action_kinds, existing_rules,
                                 condition_kinds, notes)
    raw = backend.complete(system, intent.strip())
    rule = _parse_rule_json(raw)
    errors = _check_rule(rule, existing_rules, action_kinds)
    if errors:
        # ONE repair round: the validator speaks, the model gets to respond.
        repair = (f"Your rule had these errors:\n- " + "\n- ".join(errors)
                  + "\n\nEmit the corrected rule as a single JSON object, "
                    "nothing else.\n\nOriginal intent: " + intent.strip())
        raw = backend.complete(system, repair)
        rule = _parse_rule_json(raw)
        errors = _check_rule(rule, existing_rules, action_kinds)
        if errors:
            raise CompilerError(
                "compiled rule failed validation after one repair round — "
                "rephrase the intent or use the rule form", errors)
    warnings = lint_rules_document({"rules": [rule]})
    # a 1-rule document always lints "0 rules" clean; drop the irrelevant case
    warnings = [w for w in warnings if "0 rules" not in w]
    return rule, warnings


def render_rule(rule: dict) -> str:
    """Human-readable ratification text — what the operator says yes to."""
    m = rule.get("match") or {}
    a = rule.get("action") or {}
    extras = {k: v for k, v in m.items()
              if k not in STRUCTURAL_MATCH_KEYS}
    lines = [
        f"rule id:    {rule.get('id')}",
        f"WHEN:       kind={m.get('kind')!r}  subject~{m.get('subject_glob', '*')!r}"
        + (f"  extras={extras}" if extras else ""),
        f"HELD FOR:   {rule.get('grace_s', 0)}s before firing "
        f"(cooldown {rule.get('cooldown_s', 0)}s between fires)",
        f"THEN:       {a.get('kind')}"
        + (f" → {json.dumps(a.get('payload'))}" if a.get("payload") else "")
        + (f" / priority={a.get('priority')}" if a.get("priority") else ""),
    ]
    if a.get("payload_down"):
        lines.append(f"ON CLEAR:   {json.dumps(a.get('payload_down'))}")
    if rule.get("annotation"):
        lines.append(f"NOTE:       {rule['annotation']}")
    return "\n".join(lines)

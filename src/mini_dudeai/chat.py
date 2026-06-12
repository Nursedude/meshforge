"""Phase B chat front-end: `python3 -m mini_dudeai.chat`.

Speak a rule, see it compiled, ratify it, watch the running daemon promote
it. The conversational sibling of the TUI rule form — both end at
`write_candidate()`, both leave the human between the LLM and the runtime.

    # interactive, against the claw brain's ruleset:
    python3 -m mini_dudeai.chat --preset standalone

    # one-shot:
    python3 -m mini_dudeai.chat --preset standalone \
        --once "when the claw chip tops the threshold for two minutes, \
                page me high priority"

Env: MINI_DUDEAI_OLLAMA_URL (default http://localhost:11434),
     MINI_DUDEAI_OLLAMA_MODEL (default qwen2.5:3b).
The compiler talks ONLY to the local Ollama server — no cloud, no internet.

Promotion is observed, not assumed: after the candidate is written we watch
the canonical rules file for the new id (the daemon validates + promotes
within one tick). If it doesn't land inside the window we SAY so — a written
candidate with no running daemon must not read as "rule active".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from ._util import read_json, resolve_home
from .candidate import write_candidate
from .chat_compiler import (
    DEFAULT_MODEL, DEFAULT_OLLAMA_URL, CompilerError, OllamaBackend,
    compile_rule, render_rule,
)
from .config import registered_action_kinds, registered_source_kinds

PROMOTE_WAIT_S = 75  # two 30s ticks + slack


def _paths_for_preset(preset: str) -> tuple[str, str, list[str], str]:
    """(rules_path, candidate_path, condition_kinds, notes) per preset."""
    home = resolve_home()
    if preset == "standalone":
        rules = os.path.join(home, "mini_dudeai_claw_rules.json")
        device = os.environ.get("MINI_DUDEAI_CLAW_DEVICE", "")
        notes = (f"The WireClaw edge device is named {device!r}; its chip "
                 f"temperature sensor subject is \"{device}.chip_temp\"."
                 if device else "")
        return rules, rules + ".candidate", ["sensor_breach", "source_error"], notes
    if preset == "meshforge_fleet":
        rules = os.path.join(home, "mini_dudeai_rules.json")
        kinds = ["signal_class", "federation_peer_unhealthy", "digest_stale",
                 "unexpected_reboot", "source_error"]
        return rules, rules + ".candidate", kinds, ""
    raise SystemExit(f"unknown preset {preset!r} (standalone | meshforge_fleet)")


def _load_rules(rules_path: str) -> list[dict]:
    data, err = read_json(rules_path)
    if err == "not found":
        print(f"note: {rules_path} does not exist yet — compiling against an "
              f"empty ruleset")
        return []
    if err:
        raise SystemExit(f"cannot read {rules_path}: {err} — refusing to "
                         f"compile against an unknown ruleset")
    rules = (data or {}).get("rules")
    if not isinstance(rules, list):
        raise SystemExit(f"{rules_path} has no 'rules' list — refusing")
    return rules


def _watch_promotion(rules_path: str, rule_id: str,
                     wait_s: float = PROMOTE_WAIT_S) -> bool:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        data, err = read_json(rules_path)
        if not err and any(r.get("id") == rule_id
                           for r in (data or {}).get("rules") or []):
            return True
        time.sleep(2)
    return False


def _one_intent(intent: str, backend: OllamaBackend, rules_path: str,
                candidate_path: str, condition_kinds: list[str],
                notes: str, assume_yes: bool = False) -> int:
    existing = _load_rules(rules_path)
    print(f"compiling against {len(existing)} existing rule(s) "
          f"[{backend.model} @ {backend.url}] ...")
    try:
        rule, warnings = compile_rule(
            intent, backend, existing,
            source_kinds=registered_source_kinds(),
            action_kinds=registered_action_kinds(),
            condition_kinds=condition_kinds, notes=notes)
    except CompilerError as e:
        print(f"\nCOMPILE FAILED: {e}")
        for err in e.errors:
            print(f"  - {err}")
        return 1

    print("\n" + "=" * 60)
    print(render_rule(rule))
    print("=" * 60)
    for w in warnings:
        print(f"⚠ lint: {w}")

    if not assume_yes:
        answer = input("\nratify this rule? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("not ratified — nothing written.")
            return 1

    ok, errors = write_candidate(candidate_path, existing + [rule])
    if not ok:
        print("CANDIDATE REFUSED (validator):")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"candidate written: {candidate_path}")
    print(f"waiting for the daemon to promote (up to {PROMOTE_WAIT_S}s) ...")
    if _watch_promotion(rules_path, rule["id"]):
        print(f"✓ PROMOTED — rule {rule['id']!r} is live in {rules_path}")
        return 0
    print(f"✗ candidate written but NOT observed promoting within "
          f"{PROMOTE_WAIT_S}s — is the daemon running? "
          f"(systemctl --user status meshforge-mini-dudeai-claw)")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m mini_dudeai.chat",
        description="Describe a rule in English; ratify the compiled result; "
                    "the running daemon promotes it.")
    p.add_argument("--preset", default="standalone",
                   help="standalone (dude-claw, default) | meshforge_fleet")
    p.add_argument("--rules-path",
                   help="explicit rules file (overrides --preset paths)")
    p.add_argument("--ollama-url",
                   default=os.environ.get("MINI_DUDEAI_OLLAMA_URL",
                                          DEFAULT_OLLAMA_URL))
    p.add_argument("--model",
                   default=os.environ.get("MINI_DUDEAI_OLLAMA_MODEL",
                                          DEFAULT_MODEL))
    p.add_argument("--once", metavar="INTENT",
                   help="compile one intent and exit (interactive otherwise)")
    p.add_argument("--yes", action="store_true",
                   help="skip ratification prompt (scripted use; the daemon "
                        "still validates)")
    args = p.parse_args(argv)

    rules_path, candidate_path, cond_kinds, notes = _paths_for_preset(args.preset)
    if args.rules_path:
        rules_path = args.rules_path
        candidate_path = rules_path + ".candidate"
    backend = OllamaBackend(url=args.ollama_url, model=args.model)

    if args.once:
        return _one_intent(args.once, backend, rules_path, candidate_path,
                           cond_kinds, notes, assume_yes=args.yes)

    print("mini-dudeai chat-compiler — describe a rule in English; empty "
          "line or Ctrl-D exits.")
    rc = 0
    while True:
        try:
            intent = input("\nrule> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return rc
        if not intent:
            return rc
        rc = _one_intent(intent, backend, rules_path, candidate_path,
                         cond_kinds, notes)


if __name__ == "__main__":
    raise SystemExit(main())

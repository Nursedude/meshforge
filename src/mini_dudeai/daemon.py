"""Daemon entrypoint. Built so a systemd ExecStart line is one of:

    python3 -m mini_dudeai --preset meshforge_fleet
    python3 -m mini_dudeai --config /path/to/config.json
    python3 -m mini_dudeai --preset meshforge_fleet --once   # smoke test

Used by both the fleet systemd unit and standalone users.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from .config import build_engine_from_config, load_config


def _import_preset(name: str):
    """Resolve a preset name to its build_engine() function.

    Bare name (e.g. "meshforge_fleet") looks up under mini_dudeai.presets.
    Dotted path is treated as a fully-qualified module (third-party presets).
    """
    if "." in name:
        mod = importlib.import_module(name)
    else:
        mod = importlib.import_module(f"mini_dudeai.presets.{name}")
    if not hasattr(mod, "build_engine"):
        raise SystemExit(f"preset {name!r} has no build_engine() function")
    return mod.build_engine


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mini-dudeai",
        description="Wireclaw.io-shaped rule runtime: cloud Claude is the compiler, "
                    "mini-dudeai is the runtime.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--preset", help="Named preset (e.g. meshforge_fleet) OR dotted module path.")
    g.add_argument("--config", help="Path to JSON config file describing sources + actions.")
    p.add_argument("--once", action="store_true",
                   help="Run one tick and exit. Useful for cron and smoke tests.")
    p.add_argument("--brief", metavar="OUT_PATH", nargs="?", const="",
                   help="Write a warm-start brief from current state+history and exit "
                        "(no tick). Optional path; defaults next to the state file as "
                        "mini_dudeai_brief.md.")
    p.add_argument("--interval", type=float, default=None,
                   help="Override tick interval (seconds). Default: preset's default "
                        "or config.interval_s or 30.")
    args = p.parse_args(argv)

    if args.preset:
        build_engine = _import_preset(args.preset)
        engine = build_engine()
        interval = args.interval if args.interval is not None else 30.0
    else:
        config = load_config(args.config)
        engine, interval_default = build_engine_from_config(config)
        interval = args.interval if args.interval is not None else interval_default

    if args.brief is not None:
        from .brief import write_brief
        state_path = engine.state_store.path
        out = args.brief or os.path.join(
            os.path.dirname(state_path) or ".", "mini_dudeai_brief.md")
        write_brief(state_path, engine.history.path, out)
        print(f"mini-dudeai brief: wrote {out}")
        return 0

    if args.once:
        state = engine.tick()
        print(f"mini-dudeai once: rules={state.get('rule_count')} "
              f"conds={state.get('condition_count')} "
              f"fires={state.get('fire_count')} "
              f"src_errors={state.get('error_count')}")
        return 0

    engine.run(interval_s=interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())

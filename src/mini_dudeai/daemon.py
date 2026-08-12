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


def _acquire_instance_lock(lock_path: str):
    """Exclusive advisory lock keyed to the state file. Returns the open file
    (hold it for process lifetime; the kernel releases on exit) or None when
    another process already holds it.

    Why: the daemon and a `--once`/`--dream` invocation share the same state/
    history/deltas paths. Two concurrent writers are a read-modify-write race
    on edge state (duplicate fires, bypassed cooldowns) — refusing loudly is
    the honest behavior, not interleaving quietly.
    """
    import fcntl
    try:
        f = open(lock_path, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            f.close()
        except Exception:
            pass
        return None
    try:
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass
    return f


def _resolve_preset_name(name: str) -> str:
    """``auto`` follows the box's fleet-membership declaration — the same
    fleet_hosts SSOT the TUI Fleet Membership wizard writes and every fleet
    consumer reads (hosts declared → meshforge_fleet, none → standalone).
    Any other name passes through untouched, so deployed units that pin an
    explicit preset keep their exact behavior."""
    if name != "auto":
        return name
    from .rollup import resolve_fleet_hosts
    hosts = resolve_fleet_hosts()
    resolved = "meshforge_fleet" if hosts else "standalone"
    print(f"mini-dudeai: preset auto -> {resolved} "
          f"({len(hosts)} fleet host(s) declared)")
    return resolved


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



def _brief_out_path(engine, requested):
    """Where --brief writes. An explicit path wins; else THE engine's own
    brief_path — the file the daemon writes each tick and warmstart reads.
    The old default invented a THIRD path convention (sibling-of-state
    ``mini_dudeai_brief.md``): invisible on MeshForge where the values
    coincide, but on MeshAnchor it wrote an orphan brief beside the state
    that no reader ever read (2026-08-11, the artifact-path adapter arc).
    Sibling fallback kept only for engines with no brief wired."""
    if requested:
        return requested
    bp = getattr(engine, "brief_path", None)
    if bp:
        return bp
    state_path = engine.state_store.path
    return os.path.join(os.path.dirname(state_path) or ".",
                        "mini_dudeai_brief.md")

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
                        "(no tick). Optional path; defaults to the engine's own "
                        "brief path (the file the daemon writes and "
                        "warmstart reads).")
    p.add_argument("--dream", action="store_true",
                   help="Run the low-frequency synthesis pass (B3): distill "
                        "state+history into a dream log + candidate memory-deltas, "
                        "then exit (no tick). Meant for a nightly cron/timer.")
    p.add_argument("--interval", type=float, default=None,
                   help="Override tick interval (seconds). Default: preset's default "
                        "or config.interval_s or 30.")
    args = p.parse_args(argv)

    if args.preset:
        build_engine = _import_preset(_resolve_preset_name(args.preset))
        engine = build_engine()
        interval = args.interval if args.interval is not None else 30.0
    else:
        config = load_config(args.config)
        engine, interval_default = build_engine_from_config(config)
        interval = args.interval if args.interval is not None else interval_default

    if args.brief is not None:
        from .brief import write_brief
        out = _brief_out_path(engine, args.brief)
        write_brief(engine.state_store.path, engine.history.path, out)
        print(f"mini-dudeai brief: wrote {out}")
        return 0

    if args.dream:
        from .dreams import write_dreams, DELTAS_BASENAME
        state_path = engine.state_store.path
        base = os.path.dirname(state_path) or "."
        summary = write_dreams(
            state_path=state_path,
            history_path=engine.history.path,
            deltas_path=os.path.join(base, DELTAS_BASENAME),
            narrative_path=os.path.join(base, "mini_dudeai_dreams.md"),
        )
        print(f"mini-dudeai dream: detected={summary['detected']} "
              f"appended={summary['appended']} deduped={summary['deduped']} "
              f"-> {summary['narrative_path']}")
        if summary["append_error"] or summary["narrative_error"]:
            print(f"  WARN: append_error={summary['append_error']} "
                  f"narrative_error={summary['narrative_error']}")
        return 0

    # --once and the daemon run a full tick — the SAME writers on the SAME
    # state/history files, a read-modify-write race on edge state. The
    # single-instance lock makes the loser refuse loudly. Scope: tick paths
    # ONLY — the daemon holds the lock for its lifetime, and --dream/--brief
    # write DIFFERENT files (deltas/narrative/brief), so gating them here
    # would make the nightly dream timer refuse forever on every box.
    lock_path = engine.state_store.path + ".lock"
    instance_lock = _acquire_instance_lock(lock_path)
    if instance_lock is None:
        print(f"mini-dudeai: another mini-dudeai process holds {lock_path} "
              f"(the daemon?) — refusing to run a concurrent tick against the "
              f"same state files. Stop the daemon or point --config at "
              f"different paths.", file=sys.stderr)
        return 1

    if args.once:
        state = engine.tick()
        err_names = "; ".join(state.get("source_errors") or [])
        print(f"mini-dudeai once: rules={state.get('rule_count')} "
              f"conds={state.get('condition_count')} "
              f"fires={state.get('fire_count')} "
              f"src_errors={state.get('error_count')}"
              + (f" ({err_names})" if err_names else ""))
        return 0

    engine.run(interval_s=interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())

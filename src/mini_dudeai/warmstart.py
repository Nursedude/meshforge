"""Warm-start emitter — hands mini's brief to a fresh Claude session, honestly.

`brief.py` *writes* the warm-start brief (the daemon / digest call `write_brief`).
This module *reads* it at session-start time and emits it with a freshness banner
re-derived NOW from `state.json`'s ``last_tick_ts``.

Why a second freshness check at read time: the brief file freezes the moment the
daemon stops writing, so its own "🟢 alive" posture line keeps claiming health
long after the watcher died. The only honest signal at read time is how old the
last tick is *right now*. A confidently-stale warm start is worse than a cold one
(the running-but-not-serving trap this project keeps hitting); this module
refuses to present a frozen brief as current — it prepends a 🔴 STALE banner so a
fresh session distrusts it correctly.

Pure core: ``render_warmstart(brief_path, state_path, now_ts) -> str`` — trivially
testable, no clock dependency. The ``__main__`` wrapper resolves standard paths
and, with ``--hook``, emits the SessionStart ``hookSpecificOutput`` JSON envelope.
Self-silencing when mini has never run here (no brief AND no state), so the hook
is harmless on boxes without mini.
"""
from __future__ import annotations

import json
import os

from ._util import (APP_FLEET_PRESET, APP_MINI_UNIT, APP_REPO_DEFAULT,
                    APP_REPO_ENV, APP_VERDICT_SUBDIR, read_json)

#: Past this age (s) since the last tick, the brief is treated as historical.
#: Mirrors brief.DEFAULT_STALE_S (30s tick → >5m means the daemon likely died).
DEFAULT_STALE_S = 300.0


def _age_str(now_ts: float, ts: float | None) -> str:
    """Human age like brief._age, but returns 'unknown' for a missing tick."""
    if not ts:
        return "unknown"
    s = max(0, int(now_ts - float(ts)))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def _read_text(path: str) -> str | None:
    """Return file text, or None on any read error. Never raises."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def render_warmstart(brief_path: str, state_path: str, now_ts: float,
                     stale_s: float = DEFAULT_STALE_S) -> str:
    """Return the warm-start text to inject, with an honest freshness banner.

    Returns ``""`` (silent) when mini has never run here — no brief AND no
    state. That keeps the SessionStart hook harmless on boxes without mini.

    The freshness verdict is re-derived from ``state.json``'s ``last_tick_ts``
    against ``now_ts`` — NOT from the brief's own (possibly frozen) posture
    line, which is the whole point: a dead daemon leaves a brief that lies.
    """
    brief = _read_text(brief_path)
    state, _ = read_json(state_path)
    last_tick: float | None = None
    if isinstance(state, dict):
        lt = state.get("last_tick_ts")
        if lt:
            try:
                last_tick = float(lt)
            except (TypeError, ValueError):
                last_tick = None

    # Nothing here at all → stay silent (don't inject noise on a mini-less box).
    if brief is None and last_tick is None:
        return ""

    age = _age_str(now_ts, last_tick)
    stale = bool(last_tick and (now_ts - last_tick) > stale_s)

    if brief is None:
        # State exists (mini ran) but no brief was generated yet.
        return (
            "## mini-dudeai warm start — no brief yet\n"
            f"mini has ticked (last tick {age} ago) but no brief exists at "
            f"`{brief_path}`. Generate one with "
            f"`python3 -m mini_dudeai --preset {APP_FLEET_PRESET} --brief`.\n"
        )

    if last_tick is None:
        banner = (
            "⚠️ **mini-dudeai warm start — FRESHNESS UNKNOWN** "
            "(no last_tick_ts in state; treat the brief below as historical).\n"
        )
    elif stale:
        banner = (
            f"🔴 **mini-dudeai warm start — STALE** (last tick {age} ago > "
            f"{int(stale_s)}s). The watcher itself may be down; the brief below "
            "is FROZEN at its last write and may misreport current health. "
            "Verify live before trusting it: "
            f"`systemctl --user status {APP_MINI_UNIT}`.\n"
        )
    else:
        banner = (
            f"🟢 **mini-dudeai warm start — FRESH** (last tick {age} ago). "
            "A piece of me was already here; the brief below is current.\n"
        )

    return banner + "\n" + brief


# ---------------------------------------------------------------------------
# CLI / hook entrypoint
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[str, str]:
    """THE paths this app's fleet-preset daemon writes, from the _util adapter.

    2026-08-11: artifact paths joined the adapter seam beside the unit/repo/
    preset names. Hardcoding the MeshForge-convention basenames here meant the
    byte-locked MA copy read locations its daemon never writes (the MA preset
    namespaces artifacts into its own dir) and reported "mini has not run
    here" beside a ticking daemon. Reader and writer now resolve through ONE
    function (honest_failure_modes #4).
    """
    from ._util import app_artifact_paths
    brief_path, state_path, _history = app_artifact_paths()
    return brief_path, state_path


def _current_head() -> str | None:
    """Best-effort current HEAD of THIS app's repo (for calibration
    re-derivation) — repo env/default come from the _util adapter, so the
    byte-locked twin never reads the OTHER app's HEAD on a dual-stack box
    (07-23 audit). None on any failure — re-derivation then mints no new
    verdict and simply surfaces the existing ledger state."""
    import subprocess
    repo = os.environ.get(APP_REPO_ENV, APP_REPO_DEFAULT)
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _read_verdict_marker() -> dict | None:
    """Read honest_status.sh's verdict marker (same path contract as the
    claim-gate). None on absence/parse error."""
    env = os.environ.get("HONEST_VERDICT_PATH")
    if env:
        path = env
    else:
        home = os.environ.get("HOME") or os.path.expanduser("~")
        path = os.path.join(home, APP_VERDICT_SUBDIR, "honest_verdict.json")
    data, _ = read_json(path)
    return data if isinstance(data, dict) else None


def _calibration_block(now_ts: float) -> str:
    """Re-derive open calibration claims against the current HEAD + verdict
    marker, persist any definitive verdicts, and render the warm-brief section.

    Fully fail-safe: any error (import, I/O, subprocess) yields "" — the
    calibration layer must never break the warm-start hook (which already runs
    behind ``2>/dev/null || true``, but defense in depth)."""
    try:
        from . import calibration_ledger as cl
        state = cl.rederive_and_persist(
            cl.ledger_path(), _current_head(), _read_verdict_marker(), now_ts)
        return cl.format_brief_block(state)
    except Exception:  # noqa: BLE001 — never let calibration break warm start
        return ""


def _routing_block(now_ts: float) -> str:
    """The WS-E routing-orientation block — env + measured tier-L competence, so
    a session starts knowing what it can delegate to local. Fully fail-safe (""
    on any error), and GUARDED so a mini without model_router (a partial twin)
    still warm-starts. Renders "" off the manager box (no eval ledger)."""
    try:
        from . import model_router as mroute
        # Re-derive open routing recommendations against the eval ledger first
        # (persist any definitive held/broke verdicts), then render — symmetric
        # with _calibration_block. Persist is best-effort inside; render reads.
        try:
            mroute.rederive_routing_and_persist(now_ts=now_ts)
        except Exception:  # noqa: BLE001 — persist must never break the render
            pass
        return mroute.routing_context_block(now_ts)
    except Exception:  # noqa: BLE001 — never let routing break warm start
        return ""


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    p = argparse.ArgumentParser(
        prog="mini-dudeai-warmstart",
        description="Emit mini-dudeai's warm-start brief with an honest "
                    "freshness banner (for a SessionStart hook or /warmstart).",
    )
    brief_d, state_d = _default_paths()
    p.add_argument("--brief-path", default=brief_d)
    p.add_argument("--state-path", default=state_d)
    p.add_argument("--hook", action="store_true",
                   help="Emit the SessionStart hookSpecificOutput JSON envelope "
                        "instead of plain markdown (for .claude/settings.json).")
    args = p.parse_args(argv)

    now = time.time()
    text = render_warmstart(args.brief_path, args.state_path, now)

    # The calibration ledger is surfaced even on a mini-less box (it tracks MY
    # claims, not mini's fleet posture) — so combine independently of `text`.
    # The routing block (WS-E) rides alongside — it self-silences off the manager
    # box (no eval ledger to report tier-L competence from).
    calib = _calibration_block(now)
    routing = _routing_block(now)
    text = "\n\n".join(s for s in (text, calib, routing) if s.strip())

    if args.hook:
        # Silent when there's nothing to say — don't inject empty context.
        if text.strip():
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            }))
        return 0

    # Plain mode (manual /warmstart): always say something.
    print(text if text.strip()
          else "mini-dudeai: no brief and no state on this box "
               "(mini has not run here).")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

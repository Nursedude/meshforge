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

import datetime
import json
import os

from ._util import (APP_FLEET_PRESET, APP_MINI_UNIT, APP_REPO_DEFAULT,
                    APP_REPO_ENV, APP_VERDICT_SUBDIR, READ_JSON_NOT_FOUND,
                    operator_home, read_json)

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


#: The operator's deferred-work gate ledger — APP-AGNOSTIC on purpose (it is
#: keyed to the human's home, not to MeshForge or MeshAnchor), which is why
#: this byte-locked file may name it directly.
DEFERRED_LEDGER_BASENAME = "deferred_work.json"


def deferred_backlog_line(ledger_path: str, today: str) -> str:
    """One line naming deferred work whose review date has passed, or ``""``.

    WHY THIS EXISTS (2026-08-12). The ledger's contract is "a gated task can
    NEVER fall silent" — it pages ONCE when ``review_after`` passes, and then
    it is done forever. There is no second look. Measured that day: TEN blocked
    tasks were overdue, the oldest by ~7 weeks, on a fleet the operator and I
    work daily. The witness fired exactly as designed; nothing ever surfaced it
    again, and the only reader who could act had no reason to open the file.
    Page-once + a reader who never re-reads = silence — the same shape as the
    detectors this repo keeps finding blind, one layer up.

    So the ledger joins the surface I actually read at session start. No new
    cron, no new state, no new page: an existing artifact rendered where the
    decision gets made.

    Honesty rules, in order:
      - ledger ABSENT  → "" (silent). It lives on the operator's manager box;
        its absence elsewhere is by design, not a failure (inert, not blind).
        Absence is judged by ``read_json``'s FileNotFoundError leg — NOT by an
        ``os.path.exists`` pre-check, which also answers False on EACCES /
        an untraversable parent and would file an unreadable ledger under
        "absent by design" (2026-08-12 review; honest_failure_modes #1).
      - ledger UNREADABLE/malformed → say so. A gate ledger that cannot be
        read is exactly when you must not assume an empty backlog.
      - nothing overdue → "" (silent). A clean board earns no line.
    ``today`` is passed in (ISO ``YYYY-MM-DD``) so this stays clock-free and
    testable, like everything else in this module.

    ⚠️ SHARED SEMANTICS: ``scripts/deferred_work_watch.py`` is the ledger's
    OWNING consumer (it writes, pages, and auto-closes); this line only
    re-surfaces what that watcher would page, so the two predicates must
    agree (honest_failure_modes #5 — two consumers of one artifact). The
    watcher cannot be imported from here (``scripts/`` is not a package, and
    this file is parity-byte-locked into MeshAnchor, which does not carry
    MeshForge's scripts/), so the semantics are duplicated DELIBERATELY and
    pinned by tests. The first cut drifted from it three ways — a task with
    no ``status`` field (the watcher defaults it to "blocked") was skipped
    here; ``ra < today`` hid a task due TODAY that the watcher pages
    (``today < ra_date → continue``, i.e. due when today >= review_after);
    and a lexicographic compare let a hand-typed ``"2026-9-1"`` never come
    due, where the watcher pages it as a ledger error.
    """
    if not ledger_path:
        # An EMPTY path is a misconfiguration (e.g. `Environment=
        # DEFERRED_WORK_LEDGER=` in a unit file), not an absent ledger —
        # the watcher opens '' and pages LEDGER UNREADABLE, so silence
        # here would be the quiet-direction divergence again.
        return ("⚠️ **deferred-work ledger path is empty** (blank "
                "`DEFERRED_WORK_LEDGER`?) — backlog UNKNOWN, not empty.\n")
    data, err = read_json(ledger_path)
    if err == READ_JSON_NOT_FOUND:
        return ""
    if err or not isinstance(data, dict):
        return (f"⚠️ **deferred-work ledger unreadable** (`{ledger_path}`: "
                f"{err or 'not an object'}) — backlog UNKNOWN, not empty.\n")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return (f"⚠️ **deferred-work ledger has no task list** "
                f"(`{ledger_path}`) — backlog UNKNOWN, not empty.\n")
    try:
        today_date = datetime.datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        # A broken caller must not silently empty the board.
        return (f"⚠️ **deferred-work backlog unknown** (bad `today` argument "
                f"{today!r}, want YYYY-MM-DD) — backlog UNKNOWN, not empty.\n")
    overdue = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        # No status field = "blocked" — the watcher's default (its line
        # `t.get("status", "blocked")`), so it must be the default here too.
        if t.get("status", "blocked") != "blocked":
            continue
        # str(): a numeric id must not make sorted() below raise TypeError
        # and take the whole warm-start brief down with it.
        tid = str(t.get("id") or "<unnamed>")
        ra = t.get("review_after")
        # A blocked task with no/unparseable review date can never come due —
        # it is MORE overdue than a dated one, never less. Surface it.
        if not isinstance(ra, str) or not ra.strip():
            overdue.append(tid)
            continue
        try:
            # RAW string, no strip() — the watcher parses raw, and a padded
            # "2026-12-01 " must not be a paged ledger error THERE and a
            # clean not-due-yet HERE (2026-08-12 re-review).
            ra_date = datetime.datetime.strptime(ra, "%Y-%m-%d").date()
        except ValueError:
            # The watcher pages "bad date in ledger" for these; the honest
            # analogue here is to surface the task — a malformed date must
            # never read as "not due yet" forever.
            overdue.append(tid)
            continue
        # Due when today >= review_after — the watcher's boundary, so a task
        # paged this morning is also listed here this morning.
        if ra_date <= today_date:
            overdue.append(tid)
    if not overdue:
        return ""
    shown = ", ".join(f"`{i}`" for i in sorted(overdue)[:6])
    more = f" (+{len(overdue) - 6} more)" if len(overdue) > 6 else ""
    return (f"📋 **{len(overdue)} deferred task(s) past review** — {shown}{more}. "
            f"The ledger pages ONCE and then stays quiet, so this line is the "
            f"only thing that re-surfaces them: `{ledger_path}`.\n")


def render_warmstart(brief_path: str, state_path: str, now_ts: float,
                     stale_s: float = DEFAULT_STALE_S,
                     ledger_path: str | None = None) -> str:
    """Return the warm-start text to inject, with an honest freshness banner.

    Returns ``""`` (silent) when mini has never run here — no brief AND no
    state. That keeps the SessionStart hook harmless on boxes without mini.

    The freshness verdict is re-derived from ``state.json``'s ``last_tick_ts``
    against ``now_ts`` — NOT from the brief's own (possibly frozen) posture
    line, which is the whole point: a dead daemon leaves a brief that lies.
    """
    # The operator's deferred-work backlog rides along: it is the surface that
    # otherwise re-surfaces nothing (see deferred_backlog_line). Resolved from
    # the HUMAN's home, not resolve_home() — the ledger is theirs, not mini's.
    # DEFERRED_WORK_LEDGER is the watcher's own override (deferred_work_watch
    # honors it for drills); honoring it here keeps the two consumers pointed
    # at the SAME ledger under a drill instead of silently diverging.
    if ledger_path is None:
        # Same form as the watcher's binding (`os.environ.get(key, default)`)
        # ON PURPOSE: `get(key) or default` would silently fall through to
        # the REAL ledger on an empty-string override while the watcher
        # pages LEDGER UNREADABLE on it — the two consumers must read the
        # same env var the same way (2026-08-12 re-review; the empty path
        # is then deferred_backlog_line's loud-UNKNOWN case).
        ledger_path = os.environ.get(
            "DEFERRED_WORK_LEDGER",
            os.path.join(operator_home(), DEFERRED_LEDGER_BASENAME))
    today = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
    backlog = deferred_backlog_line(ledger_path, today)

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

    # Nothing here at all → stay silent (don't inject noise on a mini-less box)
    # — EXCEPT an overdue backlog, which is the operator's, not mini's, and must
    # not be hidden just because this box runs no watcher.
    if brief is None and last_tick is None:
        return backlog

    age = _age_str(now_ts, last_tick)
    stale = bool(last_tick and (now_ts - last_tick) > stale_s)

    if brief is None:
        # State exists (mini ran) but no brief was generated yet.
        return (
            "## mini-dudeai warm start — no brief yet\n"
            f"mini has ticked (last tick {age} ago) but no brief exists at "
            f"`{brief_path}`. Generate one with "
            f"`python3 -m mini_dudeai --preset {APP_FLEET_PRESET} --brief`.\n"
            + backlog
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

    return banner + backlog + "\n" + brief


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
        # -c safe.directory: this returns None on ANY git failure, and None
        # means "mint no verdict" — so a git refusal stops calibration
        # re-derivation SILENTLY. Today both twins' mini units run as the repo
        # owner, but a root/service-account host would hit git's dubious-
        # ownership check and the ledger would just quietly stop re-deriving.
        # Scoped to the repo the adapter handed us (2026-09-01, ported from the
        # parity probe's root-blindness defect).
        out = subprocess.run(["git", "-c", f"safe.directory={repo}",
                              "-C", repo, "rev-parse", "HEAD"],
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

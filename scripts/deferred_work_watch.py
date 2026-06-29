#!/usr/bin/env python3
"""Deferred-work gate watcher — the anti-silence organ for gated tasks.

The fleet creed is "silence is the failure mode." A deferred task whose gate
clears with nobody watching is exactly that — it rots. This watcher reads the
durable ledger (~/deferred_work.json) and, the moment a task's review_after
date passes while it is still 'blocked', surfaces it so the work gets done.
Wired into the cron-verdict regime (cron_verdict.sh deferred_work_watch $?) so
#78 probe_cron_verdict_stale guards the watcher ITSELF — it cannot silently die.

Two task kinds, two behaviors (the 2026-06-21 verify upgrade):

* JUDGMENT-gate tasks (no `verify`) — "is the soak clean enough / did the
  hardware arrive". These stay SURFACE-ONLY: page the gate-review once when due
  and leave the judgment to the human/Claude review. That is correct; those are
  not mechanical predicates.

* ACTIVATION tasks (a `verify` argv) — "is the probe actually deployed +
  watchdog-restarted + seed-promoted across the fleet". These are MECHANICAL,
  so the watcher RUNS the predicate against real fleet state every due tick and
  lets the truth drive the outcome: a confirmed pass AUTO-CLOSES the task
  (status->done, stamped, ledger persisted BEFORE the confirmation page); a
  reachable-but-incomplete result nags once and keeps re-checking so the moment
  activation lands it self-closes; an UNREACHABLE/errored check is BLIND —
  surfaced as its own signal and counted CONCERN, NEVER read as done (absence of
  confirmation is not confirmation — honest_failure_modes #2).

The `verify` field is an argv LIST run WITHOUT a shell (operator-authored, but
argv removes the injection surface). Its exit code is the contract, mirroring
honest_status: 0 = VERIFIED · 1 = cleanly INCOMPLETE (not yet) · >=2 / timeout /
exec-error = BLIND (unknown). Anything that is not a clean 0 never closes a task.

Honest-failure (.claude/rules/honest_failure_modes.md): an unreadable/unparseable
ledger is NOT "nothing due" — it exits CONCERN and pages once. An auto-close is
persisted with an atomic write and the confirmation page only fires AFTER the
write succeeds, so the watcher never announces a closure it didn't durably make.

Exit: 0 = ran clean · 2 = ledger error / verify BLIND / save failure (CONCERN)

Test hooks: DEFERRED_WORK_LEDGER overrides the ledger path (sentinels live beside
it, so a temp ledger is fully isolated); DEFERRED_WORK_DRYRUN=1 logs ntfy instead
of sending.
"""
import os
import sys
import json
import subprocess
from datetime import date, datetime

HOME = os.path.expanduser("~")
LEDGER = os.environ.get("DEFERRED_WORK_LEDGER", os.path.join(HOME, "deferred_work.json"))
SENT_DIR = os.path.dirname(LEDGER) or HOME          # sentinels live beside the ledger
LOG = os.path.join(HOME, "deferred_work_watch.log")
# Paging routes through the fleet ntfy SSOT (scripts/fleet_ntfy_push.sh), which
# owns the topic + auth token in ONE place (honest_failure_modes #5) and sends
# via curl — so unicode titles are UTF-8, not latin-1, and the em-dash bug that
# silently killed 68 pages (2026-06-14..29) is structurally impossible here.
# Resolve the SSOT relative to __file__ so cron's absolute invocation finds it.
FLEET_NTFY_PUSH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet_ntfy_push.sh")


def _ntfy_topic():
    """Fleet ntfy topic, resolved like scripts/fleet_ntfy_push.sh (the SSOT):
    $MESHFORGE_NTFY_TOPIC override else ~/.config/fleet_push_topic. The SSOT
    resolves it the same way for the actual send; the watcher mirrors it ONLY to
    observe the one failure the SSOT masks (no topic -> it no-ops exit 0). NEVER
    hardcode it — operator-specific value (MF014); drills repoint via the env var."""
    t = os.environ.get("MESHFORGE_NTFY_TOPIC")
    if t:
        return t.strip()
    try:
        with open(os.path.join(HOME, ".config", "fleet_push_topic")) as f:
            return f.read().strip()
    except OSError:
        return ""


NTFY_TOPIC = _ntfy_topic()
DRYRUN = os.environ.get("DEFERRED_WORK_DRYRUN") == "1"
VERIFY_TIMEOUT_S = 180                                # ssh-fan-out verify must stay bounded


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
    except OSError:
        pass
    print(msg)


def ntfy(title, body, priority="default", tags="alarm_clock"):
    """Page via the fleet ntfy SSOT (scripts/fleet_ntfy_push.sh). Returns True if
    the page was DISPATCHED. The SSOT is best-effort (exit 0 even on a transient
    curl failure — paging is advisory by its design), so the only failure the
    watcher can still witness is the one the SSOT masks: no topic configured ->
    it silently no-ops. We check that here and return False so a DUE page sets
    rc=2 (the witness) instead of writing a sentinel for a page that never sent."""
    if DRYRUN:
        log("[DRYRUN ntfy] %s | %s" % (title, body.replace("\n", " ")[:160]))
        return True
    if not NTFY_TOPIC:
        log("ntfy: no topic configured (set MESHFORGE_NTFY_TOPIC or ~/.config/fleet_push_topic)")
        return False
    try:
        # argv list, no shell (MF002); bounded (MF004). title/priority/tags/message.
        subprocess.run([FLEET_NTFY_PUSH, title, priority, tags, body],
                       timeout=20, capture_output=True, text=True, check=False)
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        # could not even DISPATCH (SSOT missing / hung) — witness it, do not mark
        # the page delivered (honest_failure_modes #9).
        log("ntfy via SSOT could not dispatch: %s" % e)
        return False


def _sentinel(kind, tid):
    return os.path.join(SENT_DIR, ".deferred_work_%s_%s" % (kind, tid))


def _touch(path):
    if DRYRUN:
        return  # DRYRUN must be side-effect-free: writing a sentinel here once
                # silently suppressed a real backlog page (2026-06-29 footgun)
    try:
        with open(path, "w") as f:
            f.write(date.today().isoformat() + "\n")
    except OSError:
        pass


def _rm(path):
    if DRYRUN:
        return  # see _touch: no sentinel mutation under DRYRUN
    try:
        os.remove(path)
    except OSError:
        pass


def run_verify(spec):
    """Run a task's mechanical verify predicate. ``spec`` is an argv LIST (no
    shell). Returns ``(result, detail)`` where result is:
      'verified' (exit 0) · 'not_yet' (exit 1) · 'blind' (exit>=2 / timeout /
      exec-error — UNKNOWN, never a pass). A misconfigured spec is 'blind', not
      a silent pass."""
    if not isinstance(spec, list) or not spec or not all(isinstance(x, str) for x in spec):
        return "blind", "verify spec is not a non-empty argv list of strings: %r" % (spec,)
    try:
        p = subprocess.run(spec, capture_output=True, text=True, timeout=VERIFY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "blind", "verify timed out after %ss" % VERIFY_TIMEOUT_S
    except (OSError, ValueError) as e:
        return "blind", "verify could not run: %s" % e
    lines = (p.stdout or p.stderr or "").strip().splitlines()
    detail = lines[-1].strip() if lines else "exit %d" % p.returncode
    if p.returncode == 0:
        return "verified", detail
    if p.returncode == 1:
        return "not_yet", detail
    return "blind", "exit %d: %s" % (p.returncode, detail)


def save_ledger(data, path):
    """Atomic-rename write so an auto-close is never a torn file."""
    if DRYRUN:
        return  # DRYRUN must not persist auto-closes (side-effect-free test mode)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    try:
        with open(LEDGER) as f:
            data = json.load(f)
        tasks = data["tasks"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        # blindness, not "nothing due": surface loudly, exit CONCERN
        log("LEDGER UNREADABLE/INVALID: %s" % e)
        ntfy("[DEFERRED-WORK] ledger error", "deferred_work.json unreadable/invalid: %s "
             "— gate watching is BLIND until fixed." % e, priority="high", tags="warning")
        return 2

    today = date.today()
    rc = 0
    due = 0
    dirty = False
    closed = []   # (tid, subject, detail) auto-closed this run; confirm AFTER a clean save
    for t in tasks:
        tid = t.get("id", "?")
        status = t.get("status", "blocked")
        if status != "blocked":
            continue
        ra = t.get("review_after", "")
        try:
            ra_date = datetime.strptime(ra, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            log("task %s: unparseable review_after %r — surfacing as ledger error" % (tid, ra))
            ntfy("[DEFERRED-WORK] bad date in ledger",
                 "task %s has unparseable review_after %r — fix ~/deferred_work.json." % (tid, ra),
                 priority="high", tags="warning")
            rc = 2
            continue
        if today < ra_date:
            continue  # not due yet — the normal state until the gate's review date

        subject = t.get("subject", tid)
        sentinel = _sentinel("pinged", tid)
        blind_sentinel = _sentinel("verifyblind", tid)

        # ACTIVATION tasks: run the mechanical predicate against real fleet state
        # EVERY due tick (independent of the nag sentinel) so a pass auto-closes
        # the moment it lands. JUDGMENT tasks have no `verify` and skip this.
        verify_spec = t.get("verify")
        if verify_spec:
            res, detail = run_verify(verify_spec)
            if res == "verified":
                t["status"] = "done"
                t["verified"] = ("%s auto-verified by deferred_work_watch (verify exit 0): %s"
                                 % (today.isoformat(), detail))
                dirty = True
                _rm(sentinel)
                _rm(blind_sentinel)
                closed.append((tid, subject, detail))
                log("VERIFIED+CLOSED task %s: %s" % (tid, detail))
                continue
            if res == "blind":
                rc = 2  # CONCERN — verification is BLIND; never read as done
                if not os.path.exists(blind_sentinel):
                    ntfy("[DEFERRED-WORK] %s — verify BLIND" % subject[:48],
                         "Activation verify for %s could NOT observe a box "
                         "(unreachable / errored) — UNKNOWN, not closing:\n%s\n\n"
                         "How to check: %s" % (tid, detail, t.get("check", "?")),
                         priority="high", tags="warning")
                    _touch(blind_sentinel)
                log("VERIFY BLIND task %s: %s" % (tid, detail))
                continue
            # res == 'not_yet': reachable but activation incomplete. Clear any
            # stale blind marker and fall through to the standard due-nag (once),
            # while continuing to re-verify each tick.
            _rm(blind_sentinel)
            log("verify NOT-YET task %s: %s" % (tid, detail))

        if os.path.exists(sentinel):
            continue  # already paged once; awaiting action (verify-tasks still re-check above)

        # DUE and not yet paged -> surface it
        due += 1
        extra = ("\nThis task self-VERIFIES: the watcher re-runs its activation "
                 "check each tick and will auto-close it the moment activation "
                 "lands fleet-wide — no need to set status by hand.\n"
                 if verify_spec else "")
        body = ("Gate review DUE for deferred task %s:\n%s\n\nGate: %s\nHow to check: %s\n%s\n"
                "Pull it up in a session to execute. When done, set status 'done' (or bump "
                "review_after + rm %s) in ~/deferred_work.json."
                % (tid, subject, t.get("gate", "?"), t.get("check", "?"), extra, sentinel))
        ok = ntfy("[DEFERRED-WORK] %s — gate review due" % subject[:48], body,
                  priority="high", tags="alarm_clock")
        if ok:
            _touch(sentinel)  # no sentinel on ntfy failure = re-page next run (dupe > silence)
        else:
            rc = 2  # send failed -> witness for cron_verdict/#78 (honest_failure_modes #9):
            #          a page that can't deliver must NOT exit clean (the 06-14..29 silent gap)
        log("DUE+PAGED task %s (review_after %s)%s" % (tid, ra, "" if ok else " [ntfy failed, will retry]"))

    # Persist auto-closes BEFORE announcing them — never claim a closure we
    # didn't durably write (honest_failure_modes: reader/writer must agree).
    if dirty:
        try:
            save_ledger(data, LEDGER)
        except OSError as e:
            log("LEDGER SAVE FAILED after auto-close: %s" % e)
            ntfy("[DEFERRED-WORK] ledger save FAILED",
                 "auto-verified task(s) could not be persisted: %s — they will "
                 "re-verify and re-close next run." % e, priority="high", tags="warning")
            rc = 2
            closed = []  # don't announce closures we couldn't persist
    for tid, subject, detail in closed:
        ntfy("[DEFERRED-WORK] %s — activation VERIFIED, closed" % subject[:40],
             "Mechanical verify PASSED for %s:\n%s\n\nAuto-set status=done.\n%s"
             % (tid, subject, detail), priority="default", tags="white_check_mark")

    if due == 0 and rc == 0 and not closed:
        log("ran: %d task(s), none due (next review dates in the future)" % len(tasks))
    return rc


if __name__ == "__main__":
    sys.exit(main())

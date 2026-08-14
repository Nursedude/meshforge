"""Shared execute-and-report helpers for service-ops handler modules.

Q2 dedup (2026-08-14): the run-script → concat stdout+[stderr] →
tail-truncate → verdict-msgbox block existed in THREE copies
(_meshchatx_service_ops, _nomadnet_service_ops, _nomadnet_install_utils —
audit E7), and the "Repair RNS alignment" sudo flow verbatim in TWO
(audit E6). One implementation, so the next fix lands everywhere at once.

Bonus over the copies: run_command_report shows an infobox with the
honest expected duration before a long run — the old callers went dark
for up to 10 minutes (audit B2/B3's class; full Q4 treatment pending).
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def run_script_captured(cmd, timeout=600, tail=2400):
    """Run ``cmd`` captured; return ``(rc, output_tail)``. Never raises.

    ``rc`` is -1 when the process could not run at all (timeout / exec
    error) — the reason is in the output text, so a caller's failure
    message never reads as a clean exit (honest_failure_modes #1).
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, (f"Timed out after {timeout}s. Check the network "
                    f"connection and retry.")
    except (subprocess.SubprocessError, OSError) as e:
        return -1, f"Could not run: {e}"
    out = (proc.stdout or '') + (
        f"\n[stderr]\n{proc.stderr}" if proc.stderr else ''
    )
    return proc.returncode, out[-tail:] if len(out) > tail else out


def run_command_report(ctx, cmd, name, timeout=600, tail=2400,
                       working_note=None):
    """infobox → run → honest verdict msgbox. Returns the exit code.

    The verdict title carries the REAL result — OK only on rc 0, the exit
    code otherwise, FAILED when the process never ran (rc -1).
    """
    minutes = max(1, timeout // 60)
    ctx.dialog.infobox(
        name,
        working_note or (f"Running... (up to ~{minutes} min; "
                         f"the screen updates when done)"))
    rc, out = run_script_captured(cmd, timeout=timeout, tail=tail)
    if rc == 0:
        title = f"{name}: OK"
    elif rc == -1:
        title = f"{name} FAILED"
    else:
        title = f"{name} returned {rc}"
    ctx.dialog.msgbox(title, out or "(no output)")
    return rc


def journal_tail_text(unit=None, lines=50, user=False, since=None,
                      priority=None, quiet=False, no_hostname=False,
                      timeout=10, empty_text="(journal empty for this query)"):
    """Tail a systemd journal as display TEXT — never raises.

    Q2 dedup (audit E2): ~20 handler sites each hand-rolled the same
    unit-tail journal subprocess with their own
    truncation and error handling. NOT this helper's job (stay native at
    their sites): follow mode (``-f``), boot logs (``-b``), and the two
    Class-3 sites already routed through ``_show_command_output``.

    Honest failure: an unreadable journal returns a text that SAYS so —
    it must never read as a quiet/clean journal (honest_failure_modes #2).
    """
    cmd = ['journalctl']
    if user:
        cmd.append('--user')
    if unit:
        cmd += ['-u', unit]
    cmd += ['-n', str(lines), '--no-pager']
    if since:
        cmd += ['--since', since]
    if priority:
        cmd += ['-p', priority]
    if quiet:
        cmd.append('-q')
    if no_hostname:
        cmd.append('--no-hostname')
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        return f"(journal unreadable: {e})"
    if proc.returncode != 0:
        err = (proc.stderr or '').strip()
        return f"(journal unreadable: journalctl exited {proc.returncode}" + (
            f" — {err})" if err else ")")
    return proc.stdout.strip() if proc.stdout.strip() else empty_text


def wait_for_condition(predicate, seconds, label=None, tick=1.0):
    """Poll ``predicate`` for up to ``seconds``, with visible progress.

    Q4 (audit B8): the old inline sleep-loops froze the screen silently
    for 5-15s after a service restart. With ``label`` set, one dot prints
    per tick and the outcome ("up"/"timeout") closes the line, so the
    operator watches the wait instead of wondering if the TUI died.
    Returns True as soon as the predicate is truthy.
    """
    import time as _time
    if label:
        print(f"  {label} ", end="", flush=True)
    ok = False
    for _ in range(max(1, int(seconds / tick))):
        if predicate():
            ok = True
            break
        if label:
            print(".", end="", flush=True)
        _time.sleep(tick)
    if not ok:
        ok = bool(predicate())  # final check after the last sleep
    if label:
        print(" up" if ok else " timeout", flush=True)
    return ok


def repair_rns_alignment(ctx, repo_root):
    """Run ``scripts/rns_alignment.py normalize --yes`` via sudo + report.

    Callers own their preamble (drift probe / confirm dialog); this is the
    shared execute-and-report half. Returns the exit code (-1 = never ran).

    ``sudo -n`` on purpose (Q4, audit B5): the output is CAPTURED, so an
    interactive sudo password prompt would hang invisibly under a cleared
    screen until the 120s timeout. The TUI normally runs as root (sudo
    meshforge) where -n is a no-op; when it doesn't, -n fails fast and
    the honest failure text lands in the report dialog.
    """
    cli = repo_root / 'scripts' / 'rns_alignment.py'
    if not cli.is_file():
        ctx.dialog.msgbox(
            "Script missing",
            f"{cli} not found. Update MeshForge and try again.",
        )
        return -1
    return run_command_report(
        ctx, ['sudo', '-n', 'python3', str(cli), 'normalize', '--yes'],
        "Repair", timeout=120)

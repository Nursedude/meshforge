#!/usr/bin/env python3
"""Wire this box's crons to capture their output, so a FAIL can name its cause.

WHY (2026-07-31): ``cron_verdict.sh`` records ``<ts> <name> <STATUS>`` and
nothing else. On 2026-07-30 ``harness_audit`` logged ``FAIL(1)``; by the next
morning WHICH of its 14 checks went red was unrecoverable, because the crontab
idiom sends job output to ``/dev/null``. The verdict proved a failure happened
and destroyed the only evidence of what it was — a witness that records the
alarm and discards the cause (honest_failure_modes #9).

``cron_verdict.sh`` now preserves ``$OUT_DIR/<name>.out`` on any non-OK verdict.
This rewrites the crontab so that file actually gets written:

    /path/job.sh >/dev/null 2>&1; .../cron_verdict.sh job $?
    /path/job.sh >$HOME/.local/state/meshforge/cron_out/job.out 2>&1; .../cron_verdict.sh job $?

Only lines that BOTH call ``cron_verdict.sh <name>`` AND discard output are
touched. A job already redirecting to its own log keeps it (its evidence
survives; it simply is not linked from the verdict line) — rewriting it would
silently move a log the operator may already tail.

The capture directory is READ FROM ``cron_verdict.sh``, never re-declared here:
the writer of the file and the reader of the file must not carry independent
copies of the path (honest_failure_modes #5 — the 24,000-vs-24,576 shape).

Usage:
    python3 scripts/cron_capture_wire.py            # dry-run: what would change
    python3 scripts/cron_capture_wire.py --apply    # rewrite (backs up first)
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.paths import get_real_user_home  # noqa: E402

VERDICT_SCRIPT = Path(__file__).resolve().parent / "cron_verdict.sh"

# The job's output sink we replace. Matches the exact discard idiom only.
_DISCARD = re.compile(r">\s*/dev/null\s+2>&1")
# `cron_verdict.sh <name>` — the name tells us what to call the capture file.
_VERDICT_CALL = re.compile(r"cron_verdict\.sh\s+([A-Za-z0-9_.-]+)")


class WireError(Exception):
    """A resolution failure — surfaced loud, never guessed past."""


def read_out_dir(script=VERDICT_SCRIPT):
    """Return the capture dir as cron_verdict.sh itself declares it.

    Parses the ``OUT_DIR="${CRON_VERDICT_OUT_DIR:-<default>}"`` line. Raising on
    a miss is deliberate: a silent fallback here would wire every cron to write
    somewhere the verdict script never looks, and every FAIL would then report
    ``out=uncaptured`` forever while the files piled up unread.
    """
    try:
        text = script.read_text()
    except OSError as exc:
        raise WireError(f"cannot read {script}: {exc}") from exc
    m = re.search(r'^OUT_DIR="\$\{CRON_VERDICT_OUT_DIR:-(.+?)\}"', text, re.M)
    if not m:
        raise WireError(
            f"{script} declares no OUT_DIR default — refusing to guess the "
            "capture path (the two halves would drift silently)"
        )
    return m.group(1)


def plan_line(line, out_dir):
    """Return (action, reason, new_line) for one crontab line.

    Actions: 'wire' (rewrite), 'wired' (already), 'skip' (reason says why).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return "skip", "comment/blank", line
    call = _VERDICT_CALL.search(line)
    if not call:
        return "skip", "no cron_verdict call", line
    name = call.group(1)
    target = f"{out_dir}/{name}.out"
    if target in line:
        return "wired", "already captures", line
    # Only rewrite a discard that belongs to the JOB — i.e. appears before the
    # verdict call. A discard after it would be the verdict script's own.
    # Split at the START of the verdict TOKEN, not at the regex match: the match
    # lands inside "/opt/meshforge/scripts/cron_verdict.sh", which would leave
    # the directory prefix on the job side and hide the job/verdict separator.
    cmd_start = line.rfind(" ", 0, call.start()) + 1
    job_part = line[:cmd_start]
    m = _DISCARD.search(job_part)
    if m:
        new_job = job_part[: m.start()] + f'>"{target}" 2>&1' + job_part[m.end():]
        return "wire", f"capture -> {target}", new_job + line[cmd_start:]
    if ">>" in job_part or re.search(r">\s*\S", job_part):
        return "skip", "job already redirects to its own log", line
    # No redirect at all: output goes to cron's mail, which on a headless Pi is
    # read by nobody — the same witness gap wearing a different shape. Insert a
    # capture ahead of the separator that joins job to verdict call.
    sep = re.search(r"(\s*(?:;|\|\|)\s*)$", job_part)
    if not sep:
        return "skip", "cannot locate job/verdict separator", line
    new_job = job_part[: sep.start()] + f' >"{target}" 2>&1' + sep.group(1)
    return "wire", f"capture -> {target} (was cron mail)", new_job + line[cmd_start:]


def read_crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        # An empty crontab also exits nonzero on some cron implementations.
        if "no crontab" in (r.stderr or "").lower():
            return ""
        raise WireError(f"crontab -l failed (rc={r.returncode}): {r.stderr.strip()}")
    return r.stdout


def write_crontab(text):
    r = subprocess.run(["crontab", "-"], input=text, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise WireError(f"crontab install failed (rc={r.returncode}): {r.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the crontab (default: dry-run)")
    args = ap.parse_args()

    try:
        out_dir = read_out_dir()
    except WireError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    home = str(get_real_user_home())
    # cron_verdict.sh writes $HOME-relative; keep the crontab literal portable
    # across boxes with different usernames rather than baking one box's path.
    resolved_dir = out_dir.replace("$HOME", home)
    print(f"capture dir (from cron_verdict.sh): {out_dir}")
    print(f"            resolves here to      : {resolved_dir}\n")

    try:
        current = read_crontab()
    except WireError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    lines = current.splitlines(keepends=True)
    out, changed, already, skipped = [], 0, 0, 0
    for line in lines:
        body = line.rstrip("\n")
        action, reason, new = plan_line(body, out_dir)
        if action == "wire":
            changed += 1
            print(f"  WIRE   {reason}")
            out.append(new + "\n" if line.endswith("\n") else new)
        else:
            if action == "wired":
                already += 1
            elif _VERDICT_CALL.search(body):
                skipped += 1
                print(f"  skip   {_VERDICT_CALL.search(body).group(1)}: {reason}")
            out.append(line)

    print(f"\n{changed} to wire · {already} already wired · "
          f"{skipped} verdict cron(s) left alone")

    if not args.apply:
        if changed:
            print("\nDry-run — re-run with --apply to write.")
        return 0

    if changed:
        Path(resolved_dir).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(resolved_dir).parent / f"crontab.bak-{stamp}"
        backup.write_text(current)
        print(f"backup: {backup}")
        write_crontab("".join(out))
        # Re-read the LIVE crontab rather than trusting the write returned 0 —
        # the installed table is the consumer of record (calibrated_claims #7).
        verify = read_crontab()
        if verify.count("\n") != current.count("\n"):
            print("ERROR: line count changed after install — restore from backup",
                  file=sys.stderr)
            return 1
        still = sum(1 for ln in verify.splitlines()
                    if plan_line(ln, out_dir)[0] == "wire")
        if still:
            print(f"ERROR: {still} line(s) still unwired after install", file=sys.stderr)
            return 1
        print(f"applied — {changed} cron(s) now capture output; verified live")
    else:
        Path(resolved_dir).mkdir(parents=True, exist_ok=True)
        print("nothing to change")
    return 0


if __name__ == "__main__":
    sys.exit(main())

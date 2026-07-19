#!/usr/bin/env python3
"""fleet_truth_spool — ssh-fetch fan-out-unreachable boxes into the truth spool.

Some fleet boxes are unreachable to the /api/fleet/truth HTTP fan-out BY
NETWORK DESIGN: kiai sits behind the site NAT (ssh rides a reverse tunnel,
direct :5000 is filtered), and moc3 runs no map service at all. This cron —
run in the OPERATOR context, where the fleet ssh keys live (the /fleet/dups
precedent: the sandboxed map service never sshes) — fetches each configured
box's OWN localhost endpoints over ssh, plus its raw watchdog.json for
map-less boxes, and writes one freshness-stamped spool file per box. The
collector (utils.fleet_truth_collector) reads the spool ONLY when the direct
fetch failed and only while fresh (SPOOL_STALE_S) — a dead cron makes the
boxes read dark again, never last-known-healthy, and the cron_verdict wiring
(#78) pages the dead cron itself.

Targets file: ~/.config/meshforge/truth_spool_targets — one ssh alias per
line, '#' comments. Absent/empty file = nothing to do (exit 0): boxes are
OPTED IN here, this never guesses.

Exit codes: 0 = ran and wrote a spool file for every target (an unreachable
target still gets an empty-handed spool file — the truth layer surfaces the
darkness; box-down paging belongs to fleet_offline_check, not this organ);
1 = infrastructural failure (no ssh, spool dir unwritable, targets
unreadable).

Crontab (manager box):
    */2 * * * * /usr/bin/python3 /opt/meshforge/scripts/fleet_truth_spool.py \
        >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh truth_spool $?
"""

import json
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils.fleet_truth_collector import SPOOL_SCHEMA, truth_spool_dir  # noqa: E402
from utils.paths import atomic_write_text, get_real_user_home  # noqa: E402

SSH_TIMEOUT_S = 30  # whole remote round-trip (tunnel handshake + 3 curls)

# Section delimiters for the single-ssh-call remote fetch. One ssh session
# per box (the reverse-tunnel handshake is the expensive part), three
# payloads separated by markers no JSON body can legitimately start with.
_REMOTE_CMD = (
    "echo __TRUTH_SLO__; curl -s -m 6 http://localhost:5000/fleet/slo || true; "
    "echo; echo __TRUTH_STATUS__; curl -s -m 6 http://localhost:5000/api/status || true; "
    "echo; echo __TRUTH_RAWWD__; cat /var/lib/meshforge/watchdog.json 2>/dev/null || true"
)
_SECTIONS = ("__TRUTH_SLO__", "__TRUTH_STATUS__", "__TRUTH_RAWWD__")
_SECTION_KEYS = {"__TRUTH_SLO__": "slo", "__TRUTH_STATUS__": "status",
                 "__TRUTH_RAWWD__": "raw_watchdog"}


def targets_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "truth_spool_targets"


def read_targets(path: Path) -> list:
    """One ssh alias per line; comments/blanks ignored. Absent file = []."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def parse_sections(blob: str) -> dict:
    """Split the delimited remote output into {slo, status, raw_watchdog},
    each json-parsed or None. Garbage in any section → None for that section
    (unobservable, never a guess)."""
    out = {"slo": None, "status": None, "raw_watchdog": None}
    current = None
    buf: list = []

    def _flush():
        if current is None:
            return
        text = "\n".join(buf).strip()
        if not text:
            return
        try:
            doc = json.loads(text)
        except ValueError:
            return
        if isinstance(doc, dict):
            out[_SECTION_KEYS[current]] = doc

    for line in blob.splitlines():
        if line.strip() in _SECTIONS:
            _flush()
            current = line.strip()
            buf = []
        else:
            buf.append(line)
    _flush()
    return out


def fetch_box(alias: str) -> dict:
    """One bounded ssh round-trip. Any failure → all-None sections (the spool
    records the attempt; the truth layer renders the darkness)."""
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             alias, _REMOTE_CMD],
            capture_output=True, text=True, timeout=SSH_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {"slo": None, "status": None, "raw_watchdog": None}
    if proc.returncode != 0 and not proc.stdout:
        return {"slo": None, "status": None, "raw_watchdog": None}
    return parse_sections(proc.stdout)


def main() -> int:
    targets = read_targets(targets_path())
    if not targets:
        print("truth_spool: no targets configured — nothing to do")
        return 0

    try:
        spool_dir = truth_spool_dir()
        spool_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"truth_spool: FATAL spool dir unwritable: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for alias in targets:
        sections = fetch_box(alias)
        doc = {
            "schema": SPOOL_SCHEMA,
            "alias": alias,
            "fetched_at": time.time(),
            **sections,
        }
        got = [k for k, v in sections.items() if v is not None]
        try:
            atomic_write_text(spool_dir / f"{alias}.json",
                              json.dumps(doc, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"truth_spool: FATAL write failed for {alias}: {exc}",
                  file=sys.stderr)
            failures += 1
            continue
        print(f"truth_spool: {alias}: {'+'.join(got) if got else 'empty-handed'}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

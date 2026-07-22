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
import re
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
    "echo; echo __TRUTH_RAWWD__; cat /var/lib/meshforge/watchdog.json 2>/dev/null || true; "
    # The box's OWN role declaration, read as a FILE — deliberately, because
    # this is exactly the case where the HTTP surface does not exist. A
    # gateway-only box declares `meshforge-map: disabled` (too heavy for a
    # ~1GB board), so /fleet/slo and /api/status above legitimately return
    # nothing and the NOC has no other way to learn WHY. Without this, a
    # correctly-provisioned box is indistinguishable from a broken one and
    # darkens the whole fleet verdict forever (2026-07-20).
    "echo; echo __TRUTH_DEPLOY__; "
    "cat ${XDG_CONFIG_HOME:-$HOME/.config}/meshforge/deployment.json 2>/dev/null || true"
)
_SECTIONS = ("__TRUTH_SLO__", "__TRUTH_STATUS__", "__TRUTH_RAWWD__",
             "__TRUTH_DEPLOY__")
_SECTION_KEYS = {"__TRUTH_SLO__": "slo", "__TRUTH_STATUS__": "status",
                 "__TRUTH_RAWWD__": "raw_watchdog",
                 "__TRUTH_DEPLOY__": "deployment"}


#: Service whose absence removes a box's whole HTTP truth surface
#: (/api/status + /fleet/slo are both served by it).
_HTTP_SURFACE_SERVICE = "meshforge-map"
#: Role-catalog service states that mean "this box is not supposed to run it".
_NOT_EXPECTED = ("disabled", "absent")


def http_surface_expected(deployment_raw) -> "Optional[bool]":
    """Does this box's DECLARED role expect the HTTP truth surface to exist?

    ``True`` / ``False`` when the box's own ``deployment.json`` names a role
    the catalog knows; ``None`` when we cannot tell — no declaration, unknown
    role, or an unloadable catalog.

    ``None`` is load-bearing and must never collapse to ``False``: the NOC
    treats "not expected" as an ACCEPTED blind spot that stops tainting the
    fleet verdict, so guessing it would silence a genuinely broken box. An
    undeclared box keeps darkening the verdict, which is the honest default
    (honest_failure_modes #2 — unobservable is not permission to look away).
    """
    if not isinstance(deployment_raw, dict):
        return None
    role = deployment_raw.get("role")
    if not isinstance(role, str) or not role:
        return None
    try:
        import importlib.util
        script = Path(__file__).resolve().parent / "provision_role.py"
        spec = importlib.util.spec_from_file_location("provision_role", script)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: py3.12+ @dataclass resolves annotations via
        # sys.modules[cls.__module__], so an unregistered module raises
        # AttributeError mid-import and this whole check silently degrades to
        # "undecidable". Same pattern (and same comment) as
        # watchdog_probes_drift._plan_role_actions — the repo already paid for
        # this once; a test caught it the second time.
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        role_def = mod.resolve_role(mod.load_roles(mod.DEFAULT_ROLES_FILE), role)
    except Exception:
        return None            # catalog/tooling unavailable → indeterminate
    services = role_def.get("services")
    if not isinstance(services, dict):
        return None
    state = services.get(_HTTP_SURFACE_SERVICE)
    if not isinstance(state, str):
        # The role catalog says nothing about the map for this role. Silence
        # is not a declaration — stay indeterminate rather than inventing one.
        return None
    return state.strip().lower() not in _NOT_EXPECTED


def targets_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "truth_spool_targets"


_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def read_targets(path: Path) -> list:
    """One ssh alias per line; comments/blanks ignored. Absent file = [].

    Aliases are validated: they ride into ssh argv (a leading ``-`` would
    parse as an ssh option — an option-injection hole) and shape the spool
    filename (``/``/``..`` would traverse). An operator-owned file is a soft
    trust boundary, not an excuse to skip input validation (security rules).
    Rejected lines are skipped LOUDLY on stderr, never silently dropped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not _ALIAS_RE.match(line):
            print(f"truth_spool: SKIP invalid alias {line!r} "
                  "(must match [A-Za-z0-9][A-Za-z0-9._-]*)", file=sys.stderr)
            continue
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
        # Resolved HERE, not at the NOC's render time: the role catalog and
        # the box's declaration are both in hand at this moment, and the
        # consumer should read a decided boolean rather than re-derive it.
        # None (undecidable) is written through as null, never as False.
        doc["http_surface_expected"] = http_surface_expected(
            sections.get("deployment"))
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

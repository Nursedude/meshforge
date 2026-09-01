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
    "cat ${XDG_CONFIG_HOME:-$HOME/.config}/meshforge/deployment.json 2>/dev/null || true; "
    # ── Map-less enrichment (2026-07-22) ──────────────────────────────────
    # A gateway-only box has no /api/status or /fleet/slo (the curls above
    # return nothing), so mini/radio/services were accepted-blind on the
    # monitor. These three NON-HTTP raw reads — the same class of read as
    # raw_watchdog above — let a correctly-provisioned map-less gateway still
    # report those subsystems. All perturb NOTHING: mini is a file, radio is a
    # /proc LISTEN read (never a :4403 connect — #17/#75), services is
    # systemctl state.
    "echo; echo __TRUTH_RAWMINI__; cat $HOME/mini_dudeai_state.json 2>/dev/null || true; "
    "echo; echo __TRUTH_RADIO__; "
    # :4403 = 0x1133, LISTEN state 0A. /proc read only, never a connect.
    "_t=$(awk '$4==\"0A\" && $2 ~ /:1133$/{f=1} END{print (f?\"true\":\"false\")}' "
    "/proc/net/tcp /proc/net/tcp6 2>/dev/null); "
    "ls /dev/ttyUSB* /dev/ttyACM* >/dev/null 2>&1 && _u=true || _u=false; "
    "printf '{\"tcp_listening\":%s,\"usb_present\":%s}' \"${_t:-false}\" \"$_u\"; "
    "echo; echo __TRUTH_SERVICES__; "
    "{ printf '{'; _f=1; "
    "for u in meshtasticd rnsd $(systemctl list-unit-files 'meshforge-*.service' "
    "--no-legend 2>/dev/null | awk '{print $1}'); do "
    "_a=$(systemctl is-active \"$u\" 2>/dev/null); _e=$(systemctl is-enabled \"$u\" 2>/dev/null); "
    "[ $_f -eq 1 ] || printf ','; _f=0; "
    "printf '\"%s\":{\"active\":\"%s\",\"enabled\":\"%s\"}' \"$u\" \"${_a:-unknown}\" \"${_e:-unknown}\"; "
    "done; printf '}'; }"
)
_SECTIONS = ("__TRUTH_SLO__", "__TRUTH_STATUS__", "__TRUTH_RAWWD__",
             "__TRUTH_DEPLOY__", "__TRUTH_RAWMINI__", "__TRUTH_RADIO__",
             "__TRUTH_SERVICES__")
_SECTION_KEYS = {"__TRUTH_SLO__": "slo", "__TRUTH_STATUS__": "status",
                 "__TRUTH_RAWWD__": "raw_watchdog",
                 "__TRUTH_DEPLOY__": "deployment",
                 "__TRUTH_RAWMINI__": "raw_mini", "__TRUTH_RADIO__": "radio_probe",
                 "__TRUTH_SERVICES__": "services"}


#: Service whose absence removes a box's whole HTTP truth surface
#: (/api/status + /fleet/slo are both served by it).
_HTTP_SURFACE_SERVICE = "meshforge-map"
#: Service whose absence removes the box's LOCAL probe coverage. Judged
#: separately from the HTTP surface: the spool reads watchdog.json as a raw
#: FILE, so a map-less box that still runs a watchdog keeps owing us that
#: signal (moc3). Only a role that declares the unit absent/disabled is
#: exempt — and then the cell is `absent` (the organ is not here), never
#: `accepted_blind` (which claims it may be running unseen).
_WATCHDOG_SERVICE = "meshforge-watchdog"
#: Role-catalog service states that mean "this box is not supposed to run it".
_NOT_EXPECTED = ("disabled", "absent")


def _unit_expected(deployment_raw, unit: str) -> "Optional[bool]":
    """Does this box's DECLARED role expect ``unit`` to be running?

    ``True`` / ``False`` when the box's own ``deployment.json`` names a role
    the catalog knows AND that role says something about ``unit``; ``None``
    when we cannot tell — no declaration, unknown role, unloadable catalog,
    or a role that is simply silent about this unit.

    ``None`` is load-bearing and must never collapse to ``False``: the NOC
    treats "not expected" as a gap that stops tainting the fleet verdict, so
    guessing it would silence a genuinely broken box. An undeclared box keeps
    darkening the verdict, which is the honest default (honest_failure_modes
    #2 — unobservable is not permission to look away).
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
    state = services.get(unit)
    if not isinstance(state, str):
        # The role catalog says nothing about this unit for this role. Silence
        # is not a declaration — stay indeterminate rather than inventing one.
        return None
    return state.strip().lower() not in _NOT_EXPECTED


def http_surface_expected(deployment_raw) -> "Optional[bool]":
    """Does the declared role expect /api/status + /fleet/slo to exist?"""
    return _unit_expected(deployment_raw, _HTTP_SURFACE_SERVICE)


def watchdog_expected(deployment_raw) -> "Optional[bool]":
    """Does the declared role expect a LOCAL watchdog on this box?

    2026-08-31. Without this, a box whose role legitimately runs no watchdog
    (the zero-class `field-node` tier — ~46 MB RSS does not fit a 512MB-class
    board) darkened the fleet verdict FOREVER, because `watchdog` is a
    core-observability subsystem and is deliberately NOT part of the
    HTTP-surface exemption. A permanently-dark verdict is not a safe default:
    it trains the reader to ignore `dark`, which costs more than the gap it
    was flagging.

    The distinction this restores is the domain's oldest one — `inert` (the
    organ is not here, by declaration) is not `indeterminate` (we should be
    able to see it and cannot). Only the second is a finding.

    ⚠️ This does NOT make such a box unwatched: reachability always taints,
    and `mini`, `services` and `radio` are still observed through the ssh
    spool and still taint when they fail. What is given up is the box's LOCAL
    probe coverage, which is exactly what the role declaration says it gave up.
    """
    return _unit_expected(deployment_raw, _WATCHDOG_SERVICE)


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
        doc["watchdog_expected"] = watchdog_expected(sections.get("deployment"))
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

"""wan_path — the fleet's eyes on the internet: where does the path out lose?

Born 2026-09-06, the night every cloud push and git push failed while every
surface we owned said "fine": the VPS was healthy, the LAN was clean, DNS
answered, ssh sessions opened. What nobody measured was the PATH — 5%, then
10%, then 25% packet loss to every distant host, 0% to the router and to the
ISP-near anycast resolvers. The operator found it by watching the cloud map
go stale.

So this measures a LADDER, on cadence, and names the rung that is losing:

    lan   the default gateway (this box's first hop)
    edge  the ISP's first hop beyond the router
    near  anycast resolvers that sit inside or next to the ISP (1.1.1.1 ...)
    far   the hosts the fleet actually depends on (cloud VPS, GitHub, PyPI)

The verdict is a pure function of the rung results (``classify``), so the
distinction that matters — "my LAN is broken" vs "my ISP's access is broken"
vs "the internet beyond my ISP is lossy" — is tested, not eyeballed at
midnight. A rung that could not be measured (DNS failed, ping absent) is
UNKNOWN on its own line, never folded into a clean verdict.

Targets: ``~/.config/meshforge/wan_targets.txt`` (operator values, MF014),
one per line ``<rung> <label> <host>``; ``#`` comments. Without the file the
defaults below are used, with the gateway and edge hop discovered live and
the cloud host read from ``/etc/default/meshforge-cloud-push`` when present.

Outputs (``$XDG_STATE_HOME/meshforge/``, default ``~/.local/state/meshforge``):
  wan_path.json           latest run (atomic replace)
  wan_path_history.jsonl  one compact line per run, trimmed to 7 days

Wire on the manager in the cron_verdict idiom (``--verdict`` makes this
script leave the verdict itself, so CONCERN is expressible)::

    */10 * * * * /opt/meshforge/scripts/wan_path_probe.py --verdict >"$HOME/.local/state/meshforge/cron_out/wan_path.out" 2>&1
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.paths import get_real_user_home

RUNGS = ("lan", "edge", "near", "far")

#: Loss (percent) at or above which a rung is called broken.
LOSS_FAIL_PCT = 5.0
#: Ping count and interval per target: 20 probes at 0.2 s = 4 s per target,
#: enough for 5% resolution without hammering anything.
PING_COUNT = 20
PING_INTERVAL_S = 0.2
PING_WAIT_S = 2
#: History retention: 7 days at a 10-minute cadence.
HISTORY_MAX_LINES = 7 * 24 * 6
#: A state file older than this is stale on its own axis (2.5 cadences).
STALE_AFTER_S = 25 * 60

DEFAULT_NEAR = (("cloudflare-dns", "1.1.1.1"), ("google-dns", "8.8.8.8"))
DEFAULT_FAR = (("github", "github.com"), ("pypi", "pypi.org"))


@dataclass
class RungResult:
    rung: str
    label: str
    host: str
    sent: int = 0
    received: int = 0
    loss_pct: Optional[float] = None     # None = could not measure
    avg_ms: Optional[float] = None
    mdev_ms: Optional[float] = None
    error: Optional[str] = None          # why loss_pct is None
    #: The address actually probed. A named host is resolved ONCE and that
    #: address is what ping receives, so the row cannot claim one endpoint
    #: and measure another. None until measured; equal to ``host`` for an
    #: IP literal.
    addr: Optional[str] = None


@dataclass
class Verdict:
    status: str            # ok | concern | fail | unknown
    cause: str             # clean | lan | edge | transit | unknown
    message: str
    worst_far_loss: Optional[float] = None
    unmeasured: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------

def targets_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "wan_targets.txt"


def parse_targets(text: str) -> List[RungResult]:
    """``<rung> <label> <host>`` per line; malformed lines are returned as
    UNKNOWN rung results with an error, never silently dropped — a typo in
    the targets file must show up as a missing rung, not as a clean run."""
    out: List[RungResult] = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.split("#", 1)[0].strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) != 3 or parts[0] not in RUNGS:
            out.append(RungResult(rung="far", label="line-%d" % n, host="?",
                                  error="malformed targets line %d: %r" % (n, s[:60])))
            continue
        out.append(RungResult(rung=parts[0], label=parts[1], host=parts[2]))
    return out


def _default_gateway() -> Optional[str]:
    rc, out = _run(["ip", "route", "show", "default"], timeout=5)
    if rc != 0:
        return None
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def _edge_hop(probe_host: str = "1.1.1.1") -> Optional[str]:
    """The ISP's first hop: whoever answers 'Time to live exceeded' at TTL 2."""
    rc, out = _run(["ping", "-n", "-c", "3", "-i", "0.3", "-W", "1", "-t", "2",
                    probe_host], timeout=8)
    m = re.search(r"From (\S+?)[: ].*Time to live exceeded", out)
    return m.group(1) if m else None


def _cloud_host() -> Optional[str]:
    try:
        with open("/etc/default/meshforge-cloud-push", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*CLOUD_HOST=[\"']?([^\"'\s]+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def default_targets() -> List[RungResult]:
    out: List[RungResult] = []
    gw = _default_gateway()
    out.append(RungResult("lan", "gateway", gw) if gw else
               RungResult("lan", "gateway", "?", error="no default route"))
    edge = _edge_hop()
    out.append(RungResult("edge", "isp-hop", edge) if edge else
               RungResult("edge", "isp-hop", "?", error="TTL-2 hop did not answer"))
    for label, host in DEFAULT_NEAR:
        out.append(RungResult("near", label, host))
    cloud = _cloud_host()
    if cloud:
        out.append(RungResult("far", "cloud-vps", cloud))
    for label, host in DEFAULT_FAR:
        out.append(RungResult("far", label, host))
    return out


def load_targets() -> List[RungResult]:
    p = targets_path()
    if p.is_file():
        try:
            return parse_targets(p.read_text(encoding="utf-8"))
        except OSError as exc:
            return [RungResult("far", "targets", "?", error="targets file unreadable: %s" % exc)]
    return default_targets()


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

_IPV4_LITERAL = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def resolve_host(host: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve ``host`` to ONE IPv4 address; return ``(addr, error)``.

    Why this exists (2026-09-06, the trace that followed the first night):
    ``github.com`` round-robins between GitHub's own AS (``140.82.x``, US-east,
    ~118 ms, and that day 20-35% lossy behind a transit provider) and an Azure
    edge (~61 ms, clean). Pinging the NAME meant consecutive runs measured
    DIFFERENT endpoints and filed both under the label ``github`` — the history
    then showed a path flapping between 0% and 30% when in truth there were two
    steady paths, one lossy and one clean. Same defect class as the rest of this
    module: a measurement that does not say what it measured.

    An IP literal resolves to itself, so the lan/edge/near rungs are untouched.
    Failure is an ERROR, never a silent fallback to the name — a rung that
    could not be resolved is unmeasured, which is its own honest state.
    """
    if not host or host == "?":
        return None, "no host"
    if _IPV4_LITERAL.match(host):
        return host, None
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        return None, "no IPv4 address for %r (this ladder is IPv4-only): %s" % (host, exc)
    for info in infos:
        addr = info[4][0]
        if addr:
            return addr, None
    return None, "DNS returned no A record"


def _run(cmd: Sequence[str], timeout: float):
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except (OSError, ValueError) as exc:
        return 125, str(exc)


_PING_STATS = re.compile(r"(\d+) packets transmitted, (\d+) received")
_PING_RTT = re.compile(r"= [\d.]+/([\d.]+)/[\d.]+/([\d.]+) ms")


def parse_ping(out: str) -> Dict[str, Optional[float]]:
    """Pure parse of ping's summary. Missing stats → sent=0 (unmeasured)."""
    m = _PING_STATS.search(out)
    if not m:
        return {"sent": 0, "received": 0, "loss_pct": None, "avg_ms": None, "mdev_ms": None}
    sent, recv = int(m.group(1)), int(m.group(2))
    loss = (100.0 * (sent - recv) / sent) if sent else None
    r = _PING_RTT.search(out)
    return {"sent": sent, "received": recv, "loss_pct": loss,
            "avg_ms": float(r.group(1)) if r else None,
            "mdev_ms": float(r.group(2)) if r else None}


def measure(target: RungResult, count: int = PING_COUNT) -> RungResult:
    if target.error:
        return target
    # Resolve BEFORE probing and probe the address, so the recorded endpoint is
    # provably the one measured. Handing ping the name would let it resolve to a
    # different address than the one we report (see resolve_host).
    addr, dns_err = resolve_host(target.host)
    if dns_err:
        return RungResult(target.rung, target.label, target.host,
                          error="unmeasured: %s" % dns_err)
    rc, out = _run(["ping", "-n", "-c", str(count), "-i", str(PING_INTERVAL_S),
                    "-W", str(PING_WAIT_S), addr],
                   timeout=count * PING_INTERVAL_S + PING_WAIT_S * 3 + 5)
    stats = parse_ping(out)
    if stats["sent"] == 0:
        # ping never ran a probe: unresolvable name, no ping binary, or a
        # timeout before the summary. Unmeasured is its own state.
        reason = out.strip().splitlines()[-1][:80] if out.strip() else "no output"
        return RungResult(target.rung, target.label, target.host,
                          error="unmeasured (rc=%s): %s" % (rc, reason), addr=addr)
    return RungResult(target.rung, target.label, target.host,
                      sent=stats["sent"], received=stats["received"],
                      loss_pct=stats["loss_pct"], avg_ms=stats["avg_ms"],
                      mdev_ms=stats["mdev_ms"], addr=addr)


# --------------------------------------------------------------------------
# verdict — pure
# --------------------------------------------------------------------------

def rung_name(r: RungResult) -> str:
    """``label`` for an IP target, ``label@addr`` for a resolved name.

    The verdict message is what lands in cron_verdicts.log and in a page, and
    it has to survive being read a week later next to a different sample of
    the same label."""
    if r.addr and r.addr != r.host:
        return "%s@%s" % (r.label, r.addr)
    return r.label


def _fmt(r: RungResult) -> str:
    if r.loss_pct is None:
        return "%s ?" % rung_name(r)
    return "%s %.0f%%/%sms" % (rung_name(r), r.loss_pct,
                                ("%.0f" % r.avg_ms) if r.avg_ms is not None else "?")


def classify(results: Sequence[RungResult], fail_pct: float = LOSS_FAIL_PCT) -> Verdict:
    """Name the rung that is losing.

    Order matters and each step needs its rung MEASURED: a LAN rung that
    could not be measured cannot exonerate the LAN, so the verdict for
    everything past it is at best 'unknown' about where the loss lives.
    """
    by = {r: [x for x in results if x.rung == r] for r in RUNGS}
    unmeasured = [x.label for x in results if x.loss_pct is None]

    def worst(rung):
        vals = [x.loss_pct for x in by[rung] if x.loss_pct is not None]
        return max(vals) if vals else None

    lan, edge, near, far = (worst(r) for r in RUNGS)

    if lan is not None and lan > 0:
        return Verdict("fail", "lan",
                       "first hop is losing: " + ", ".join(_fmt(x) for x in by["lan"]),
                       far, unmeasured)
    if edge is not None and edge >= fail_pct:
        return Verdict("fail", "edge",
                       "ISP access hop is losing: " + ", ".join(_fmt(x) for x in by["edge"])
                       + " (lan clean)", far, unmeasured)
    if far is None:
        return Verdict("unknown", "unknown",
                       "no far target could be measured (%s)" % ", ".join(unmeasured or ["?"]),
                       None, unmeasured)
    far_desc = ", ".join(_fmt(x) for x in by["far"])
    ctx = "edge %s, lan %s" % (
        ("%.0f%%" % edge) if edge is not None else "?",
        ("%.0f%%" % lan) if lan is not None else "?")
    if far >= fail_pct:
        where = "transit" if (near is not None and near < fail_pct) else "edge-or-transit"
        return Verdict("fail", where,
                       "loss beyond the ISP: %s (%s, near %s)" % (
                           far_desc, ctx, ("%.0f%%" % near) if near is not None else "?"),
                       far, unmeasured)
    if far > 0:
        return Verdict("concern", "transit",
                       "light loss beyond the ISP: %s (%s)" % (far_desc, ctx), far, unmeasured)
    status = "ok" if not unmeasured else "concern"
    msg = "path clean: %s (%s)" % (far_desc, ctx)
    if unmeasured:
        msg += "; unmeasured: " + ", ".join(unmeasured)
    return Verdict(status, "clean", msg, far, unmeasured)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else get_real_user_home() / ".local" / "state"
    return root / "meshforge"


def state_path() -> Path:
    return state_dir() / "wan_path.json"


def history_path() -> Path:
    return state_dir() / "wan_path_history.jsonl"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_state(results: Sequence[RungResult], verdict: Verdict, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    try:
        host = socket.gethostname().split(".")[0]
    except OSError:
        host = "?"
    return {
        "generated_at": now,
        "host": host,
        "status": verdict.status,
        "cause": verdict.cause,
        "message": verdict.message,
        "worst_far_loss_pct": verdict.worst_far_loss,
        "unmeasured": list(verdict.unmeasured),
        "fail_pct": LOSS_FAIL_PCT,
        "rungs": [asdict(r) for r in results],
    }


def history_line(state: dict) -> str:
    """One compact row. ``r`` keeps the per-label loss series CONTINUOUS (so a
    24 h trend stays comparable); ``a`` records the endpoint each sample came
    from, for the labels where that can vary. Rows written before 2026-09-06
    carry no ``a`` — readers must treat it as optional, not as "same endpoint"."""
    rungs = state["rungs"]
    compact = {"t": int(state["generated_at"]), "s": state["status"], "c": state["cause"],
               "r": {"%s:%s" % (r["rung"], r["label"]): r["loss_pct"] for r in rungs}}
    addrs = {"%s:%s" % (r["rung"], r["label"]): r["addr"] for r in rungs
             if r.get("addr") and r["addr"] != r.get("host")}
    if addrs:
        compact["a"] = addrs
    return json.dumps(compact, separators=(",", ":"))


def write_state(state: dict) -> None:
    _atomic_write(state_path(), json.dumps(state, indent=1) + "\n")
    hp = history_path()
    try:
        old = hp.read_text(encoding="utf-8").splitlines() if hp.is_file() else []
    except OSError:
        old = []
    lines = (old + [history_line(state)])[-HISTORY_MAX_LINES:]
    _atomic_write(hp, "\n".join(lines) + "\n")


def read_history(since_s: float = 24 * 3600, now: Optional[float] = None) -> List[dict]:
    """History rows newer than ``since_s``; a corrupt line is skipped but
    counted in ``_dropped`` on the first row so the gap is visible."""
    now = time.time() if now is None else now
    hp = history_path()
    if not hp.is_file():
        return []
    rows, dropped = [], 0
    try:
        for line in hp.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                dropped += 1
                continue
            if isinstance(d, dict) and isinstance(d.get("t"), (int, float)) and now - d["t"] <= since_s:
                rows.append(d)
    except OSError:
        return []
    if rows and dropped:
        rows[0]["_dropped"] = dropped
    return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="measure the path out, rung by rung")
    ap.add_argument("--verdict", action="store_true",
                    help="also leave a cron_verdict.sh line (OK/CONCERN/FAIL)")
    ap.add_argument("--count", type=int, default=PING_COUNT)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--auto-trace", action="store_true",
                    help="on a red verdict, run a throttled path trace so the "
                         "localization is already waiting (utils.wan_autotrace)")
    args = ap.parse_args(argv)

    results = [measure(t, args.count) for t in load_targets()]
    verdict = classify(results)
    state = build_state(results, verdict)
    try:
        write_state(state)
    except OSError as exc:
        print("warn: could not write state: %s" % exc, file=sys.stderr)

    if not args.quiet:
        for r in results:
            shown = r.host if (not r.addr or r.addr == r.host) else "%s -> %s" % (r.host, r.addr)
            print("  %-5s %-14s %-34s %s" % (
                r.rung, r.label, shown,
                ("loss %5.1f%%  avg %6.1f ms  jitter %5.1f ms" % (
                    r.loss_pct, r.avg_ms or 0.0, r.mdev_ms or 0.0))
                if r.loss_pct is not None else "UNMEASURED — %s" % r.error))
        print("%s (%s): %s" % (verdict.status.upper(), verdict.cause, verdict.message))

    # Auto-trace BEFORE the verdict so the localization the operator will read
    # is already on disk when the page/brief picks the verdict up. Never let a
    # trace failure change the ladder's own verdict — the ladder is the
    # measurement of record; the trace is commentary on it.
    if getattr(args, "auto_trace", False):
        try:
            from utils.wan_autotrace import autotrace
            ran, why = autotrace(state)
            if not args.quiet:
                print("auto-trace: %s — %s" % ("ran" if ran else "skipped", why))
        except Exception as exc:            # noqa: BLE001 - never break the ladder
            print("warn: auto-trace failed: %s: %s" % (type(exc).__name__, exc),
                  file=sys.stderr)

    if args.verdict:
        status = {"ok": "OK", "concern": "CONCERN", "fail": "FAIL", "unknown": "FAIL"}[verdict.status]
        cv = Path(__file__).resolve().parents[2] / "scripts" / "cron_verdict.sh"
        _run([str(cv), "wan_path", status, verdict.message], timeout=20)
    return {"ok": 0, "concern": 0, "fail": 1, "unknown": 2}[verdict.status]

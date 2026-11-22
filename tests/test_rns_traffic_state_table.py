"""WITNESS-TUI leg [6/6] — the FULL state table, EXACT oracle (Fable r4, 2026-11-21).

r3 measured the previous table's oracle as weak: it checked counts and
booleans, so 13 behavioural mutants (tx/rx swapped, the wrong interface named,
the third interface dropped, a timeout called "returned nothing", ...) passed
all of it, and the original clobber bug passed every row. 3456 rows were 850
distinct inputs.

This table:
  * builds each case from STRUCTURED inputs (interfaces, shapes, values) and
    renders the subprocess output from them;
  * DEDUPES on the real inputs the code sees (the -F response, the plain
    response when the spec says it is read, running, instance_ok, binary);
  * has an oracle that predicts, from the inputs and the SPEC below alone,
    EVERY rendered line, every issue / warning / blind-spot string, the
    filter state, the reason and the subprocess call count — compared with ==.

SPEC (what leg [6/6] must say):
  rows      every listed interface (Shared Instance INCLUDED) whose ↑ and ↓
            accepted counters were both read, in listed order, never
            collapsed by name; none when the view timed out or carries an
            rnstatus error line (the listing is not trusted).
  row line  "  NAME: ↑TX ↓RX"; RX-only (rx > 0, tx 0, not Shared Instance)
            "  NAME: RX-ONLY (↑TX ↓RX)" + issue "Interface NAME is RX-only (no TX)".
  verdict   "      rx accepted RX · filtered X" — X is the Filtered ↓ read
            under COUNTED (+ the ⚠ suffix when > 0), "n/a (this rnstatus has
            no -F)" under NO_FILTER, "UNKNOWN" under UNKNOWN, "UNKNOWN (no
            Filtered line for this interface)" when COUNTED printed none.
  warning   one line counting the rows with filtered ↓ > 0 under COUNTED.
  blind     accepted: not installed > timed out > "rnstatus error: LINE" >
            (nothing listed) rnstatus's own error / cannot connect / "listed
            no interfaces" > N listed without readable counters: NAMES.
            filtered: UNKNOWN -> its reason; COUNTED -> rows without a
            Filtered line, named. NO_FILTER is an explained absence, not blind.
  no rows   "  Could not read: <accepted reason>" (+ the reinstall hint when
            rnstatus is not installed).

Run: python3 -m pytest tests/test_rns_traffic_state_table.py -q
"""
import itertools
import os
import subprocess
import sys
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from utils.rns_status_parser import FilterState, read_interface_traffic  # noqa: E402
from handlers._rns_diagnostics_engine import (  # noqa: E402
    interface_traffic_leg, summary_lines,
)

SI_TYPE = "Shared Instance"
TYPES = ("TCPInterface", "RNodeInterface", "UDPInterface")
NAMES = ("peer0/h0:4242", "peer1/h1:4243", "peer2/h2:4244")
TX = ("478 B", "1.20 KB", "3,301 B")
RX = ("254 B", "2.50 KB", "17 B")
SI_TX, SI_RX = "0 B", "900 B"
TX_FILTERED = "13 B"          # never equal to an rx filtered value (tx/rx-filtered swap)

# shape: (traffic, rx_only, filtered ↓ text or None = no Filtered block)
SHAPES = {
    "TZ": ("full", False, "0 B"),
    "TS": ("full", False, "7 B"),          # small: a ">400 B only" warning dies
    "TK": ("full", False, "2.00 KB"),      # units: a "B only" reader dies
    "TM": ("full", False, "55.09 MB"),
    "T":  ("full", False, None),
    "F":  (None,   False, "7 B"),          # Filtered block, no Traffic
    "N":  (None,   False, None),           # header + Status/Mode only
    "R":  ("full", True,  "0 B"),          # RX-only link
    "RK": ("full", True,  "2.00 KB"),
    "Tt": ("tx",   False, "0 B"),          # ↑ read, ↓ missing
    "Rr": ("rx",   False, "0 B"),          # ↓ read, ↑ unreadable
    "TI": ("idle", False, "0 B"),          # ↑0 B ↓0 B: idle, NOT RX-only (r4 P21)
    "Fu": ("full", False, "up-only"),      # Filtered ↑ line only, no ↓ (r4 P6)
}
FULL = ("full", "idle")                    # both accepted counters printed
SI_SHAPES = {                              # Shared Instance variants
    None: None,
    "SZ": ("full", False, "0 B"),
    "SK": ("full", False, "1.10 MB"),
    "SN": (None,   False, None),
    "ST": ("full", False, None),
}

FOOTER = (" Totals       : ↑9 KB  0 bps\n                ↓9 KB  0 bps\n"
          " Filtered     : ↑0 B  0 bps\n                ↓8.00 KB  0 bps\n")
# A dotted header after the last block. Its arrows must never land in
# traffic or filtered (r3 nit: _HDR_RE had no '.').
DOTTED = "    Path Rqs. : ↑3 B  0 bps\n                ↓99 B  0 bps\n"
REJECT = ("usage: rnstatus [-h] [--config CONFIG] [--version] [-a] [-A] [-P] [-l] [-B]\n"
          "                [-t] [-s SORT] [-r] [-j] [-R hash] [-i path] [-w seconds] [-d]\n"
          "                [filter]\nrnstatus: error: unrecognized arguments: -F\n")
CRASH = ("Traceback (most recent call last):\n  File \"rnstatus.py\", line 783, in program_setup\n"
         "KeyError: 'frxb'\n")
NOSHARED = "Could not get shared instance"
ERRWORDS = "Could not connect to peer9/h9:4299: Connection refused"
NOT_FOUND = ("rnstatus binary not found — reinstall MeshForge to restore RNS "
             "(Settings > Updates)")
LONG_ERR = "RuntimeError: " + "rnsd said no " * 15
NO_F_REASON = "this rnstatus has no -F (no filtered counter to read)"
WARN_SUFFIX = " ⚠ dropped by this RNS, not logged (e.g. unstamped announces under enforce_stamps)"


@dataclass(frozen=True)
class Iface:
    type_name: str
    name: str
    shape: tuple        # (traffic, rx_only, filtered)
    tx: str
    rx: str

    @property
    def full_name(self):
        return f"{self.type_name}[{self.name}]"


def _ifaces(si, shapes, dup):
    out = []
    if si is not None:
        out.append(Iface(SI_TYPE, "rns/default", SI_SHAPES[si], SI_TX, SI_RX))
    for k, sh in enumerate(shapes):
        traffic, rx_only, filtered = SHAPES[sh]
        t, n = (TYPES[0], NAMES[0]) if (dup and k == 1) else (TYPES[k], NAMES[k])
        idle = traffic == "idle"
        out.append(Iface(t, n, SHAPES[sh], "0 B" if (rx_only or idle) else TX[k],
                         "0 B" if idle else RX[k]))
    return tuple(out)


def _block(i, with_filtered, dotted):
    traffic, _, filtered = i.shape
    out = f" {i.full_name}\n    Status    : Up\n"
    out += "    Serving   : 2 programs\n" if i.type_name == SI_TYPE else "    Mode      : Full\n"
    if traffic in FULL:
        out += f"    Traffic   : ↑{i.tx}  0 bps\n                ↓{i.rx}  0 bps\n"
    elif traffic == "tx":
        out += f"    Traffic   : ↑{i.tx}  0 bps\n"
    elif traffic == "rx":
        out += f"    Traffic   : ↑n/a\n                ↓{i.rx}  0 bps\n"
    if with_filtered and filtered == "up-only":
        out += f"    Filtered  : ↑{TX_FILTERED}  0 bps\n"
    elif with_filtered and filtered is not None:
        out += f"    Filtered  : ↑{TX_FILTERED}  0 bps\n                ↓{filtered}  0 bps\n"
    if dotted:
        out += DOTTED
    return out + "\n"


def _listing(ifaces, with_filtered, dotted):
    return "".join(_block(i, with_filtered, dotted) for i in ifaces)


# A response: None = timeout; else (stdout, stderr, rc, listed_ifaces, error_line)
# listed_ifaces = the structured interfaces the stdout carries (with whether
# their Filtered blocks are in it); error_line = the rnstatus error-pattern line.
@dataclass(frozen=True)
class Resp:
    stdout: str
    stderr: str
    rc: int
    ifaces: tuple       # interfaces present in stdout
    filtered: bool      # their Filtered blocks are printed
    error_line: object  # str | None


def _respond(outcome, ifaces, is_f, dotted):
    if outcome == "timeout":
        return None
    if outcome == "noshared":
        return Resp("", NOSHARED + "\n", 1, (), False, NOSHARED)
    if outcome == "empty":
        return Resp("", "", 0, (), False, None)
    if outcome == "reject":
        return Resp("", REJECT, 2, (), False, None)
    if outcome == "crash":
        # stock 1.6.1 vs a pre-1.6 rnsd: the first block (no Filtered), then KeyError
        first = ifaces[:1]
        return Resp(_listing(first, False, False), CRASH, 1, first, False, None)
    listing = _listing(ifaces, is_f, dotted)
    if outcome == "footer":
        return Resp(listing + FOOTER, "", 0, ifaces, is_f, None)
    if outcome == "rc1":
        return Resp(listing, "", 1, ifaces, is_f, None)
    if outcome == "rc1long":             # a cause line longer than the 120 cut (r4 P9)
        return Resp(listing, LONG_ERR + "\n", 1, ifaces, is_f, None)
    if outcome == "errwords":
        return Resp(listing, ERRWORDS + "\n", 0, ifaces, is_f, ERRWORDS)
    assert outcome == "ok", outcome
    return Resp(listing, "", 0, ifaces, is_f, None)


@dataclass(frozen=True)
class Case:
    f_out: str
    p_out: str
    ifaces: tuple
    dotted: bool
    running: bool
    instance_ok: bool

    def f_resp(self):
        return _respond(self.f_out, self.ifaces, True, self.dotted)

    def p_resp(self):
        return _respond(self.p_out, self.ifaces, False, self.dotted)


# ------------------------------------------------------------------ oracle

def _listed(resp):
    """rnstatus really listed interfaces: some accepted counter was printed."""
    return resp is not None and any(i.shape[0] is not None for i in resp.ifaces)


def _parse_error_line(resp, missing):
    """The one line naming why a reading failed (None = it did not)."""
    if missing:
        return NOT_FOUND
    if resp.error_line:
        return resp.error_line
    combined = resp.stdout + resp.stderr
    if not combined.strip():
        return f"rnstatus produced no output (exit {resp.rc})"
    if resp.rc != 0:
        err = [l.strip() for l in resp.stderr.splitlines() if l.strip()]
        first = err[-1] if err else combined.strip().splitlines()[0]
        return f"rnstatus exited {resp.rc}: {first[:120]}"
    return None


def oracle(c: Case):
    missing = c.f_out == "missing"
    f = None if missing else c.f_resp()
    plain_needed = False
    if missing:
        state, reason, view, view_missing = FilterState.UNKNOWN, f"rnstatus -F: {NOT_FOUND}", None, True
        calls = 0
    elif f is None:
        state, reason, view, view_missing = FilterState.UNKNOWN, "rnstatus -F timed out", None, False
        calls = 1
    elif not _listed(f) and c.f_out == "reject":
        plain_needed = True
        state, calls = FilterState.NO_FILTER, 2
        view, view_missing = c.p_resp(), False
        reason = NO_F_REASON
        if view is None:
            reason += "; accepted traffic UNKNOWN: rnstatus timed out (rnsd unresponsive)"
        else:
            err = _parse_error_line(view, False)
            if err or not _listed(view):
                reason += "; accepted traffic UNKNOWN: " + (err or "rnstatus reported no interfaces")
    elif _listed(f) and _parse_error_line(f, False) is None:
        state, reason, view, view_missing, calls = FilterState.COUNTED, "", f, False, 1
    else:
        plain_needed = True
        err = _parse_error_line(f, False)
        why = ("partial listing — " + (err or "incomplete run")) if _listed(f) \
            else (err or "reported no interfaces")
        state, reason, calls = FilterState.UNKNOWN, f"rnstatus -F: {why[:160]}", 2
        view, view_missing = c.p_resp(), False

    # ---- what the VIEW printed, per interface (from the structured inputs)
    view_timed_out = view is None and not view_missing and calls > 0
    view_error = None if (view is None) else view.error_line

    def tx_read(i):
        return i.shape[0] in FULL + ("tx",)

    def rx_read(i):
        return i.shape[0] in FULL + ("rx",)

    def filt(i):            # the Filtered ↓ text the view printed, or None
        f = i.shape[2] if view.filtered else None
        return None if f == "up-only" else f

    shown, unread = [], []
    if view is not None and view_error is None:
        for i in view.ifaces:
            if tx_read(i) or rx_read(i) or filt(i) is not None:
                shown.append(i)
            if not (tx_read(i) and rx_read(i)):
                unread.append(i)

    # ---- lines, issues, warnings
    lines, issues, warnings = [], [], []
    dropping = 0
    rx_only_found = False
    for i in shown:
        tx = i.tx if tx_read(i) else "UNKNOWN"
        rx = i.rx if rx_read(i) else "UNKNOWN"
        rx_only = (tx_read(i) and rx_read(i) and i.shape[1] and i.type_name != SI_TYPE)
        if rx_only:
            lines.append(f"  {i.full_name}: RX-ONLY (↑{tx} ↓{rx})")
            issues.append(f"Interface {i.full_name} is RX-only (no TX)")
            rx_only_found = True
        else:
            lines.append(f"  {i.full_name}: ↑{tx} ↓{rx}")
        acc = f"rx accepted {rx}"
        f = filt(i)
        if state is FilterState.NO_FILTER:
            v = f"{acc} · filtered n/a (this rnstatus has no -F)"
        elif state is FilterState.UNKNOWN:
            v = f"{acc} · filtered UNKNOWN"
        elif f is None:
            v = f"{acc} · filtered UNKNOWN (no Filtered line for this interface)"
        else:
            v = f"{acc} · filtered {f}"
            if f != "0 B":
                v += WARN_SUFFIX
                dropping += 1
        lines.append(f"      {v}")
    if rx_only_found:
        lines.append("\n  RX-only interfaces = link establishment failing.")
        lines.append("  Common cause: shared instance port 37428 not bound.")

    # ---- blind spots: completeness is judged from the RESPONSE (exit code,
    # error text, output), never from which reader branch ran (r4 IMPORTANT).
    if view_missing:
        acc_spot = "rnstatus not installed"
    elif view_timed_out:
        acc_spot = "rnstatus timed out"
    elif view_error:
        acc_spot = f"rnstatus error: {view_error}"
    else:
        pe = _parse_error_line(view, False)
        if pe:
            acc_spot = f"partial listing — {pe}" if _listed(view) else pe
        elif not shown and not unread:
            acc_spot = ("rnstatus cannot connect" if (c.running and not c.instance_ok)
                        else "rnstatus listed no interfaces")
        elif unread:
            acc_spot = (f"{len(unread)} interface(s) listed without readable counters: "
                        + ", ".join(i.full_name for i in unread))
        else:
            acc_spot = None
    unobserved = []
    if acc_spot:
        unobserved.append(f"interface traffic ({acc_spot})")
    if state is FilterState.UNKNOWN:
        unobserved.append(f"filtered rx ({reason})")
    elif state is FilterState.COUNTED:
        no_line = [i for i in shown if filt(i) is None]
        if no_line:
            unobserved.append(f"filtered rx ({len(no_line)} interface(s) printed no "
                              "Filtered line: " + ", ".join(i.full_name for i in no_line) + ")")
    if not shown:
        assert acc_spot, "no rows must always say why"
        lines.append(f"  Could not read: {acc_spot}")
        if view_missing:
            lines.append("  Reinstall MeshForge to restore rnstatus (Settings > Updates).")
    if dropping:
        warnings.append(f"{dropping} interface(s) filtered inbound traffic — dropped "
                        "by this RNS without a log line (see [6/6])")
    return dict(state=state, reason=reason, calls=calls, plain_needed=plain_needed,
                lines=lines, issues=issues, warnings=warnings, unobserved=unobserved)


# ------------------------------------------------------------------ cases

F_ALL = ["ok", "footer", "rc1", "rc1long", "crash", "reject", "timeout", "noshared", "empty",
         "missing", "errwords"]
P_ALL = [o for o in F_ALL if o != "missing"]   # EVERY response the plain re-read can get (r4)
F_BIG = [("ok", "ok"), ("footer", "ok"), ("reject", "ok"), ("rc1", "ok")]
TRIPLES = [("TZ", "TS", "TK"), ("TK", "RK", "TM"), ("R", "R", "R"), ("TS", "TS", "TS"),
           ("N", "TZ", "N"), ("TZ", "N", "Rr"), ("Tt", "Rr", "F"), ("T", "TZ", "T"),
           ("TM", "TM", "RK"), ("F", "F", "TK")]
SMALL = [(None, ()), (None, ("TZ",)), ("SK", ("TK",)), ("SZ", ("R", "TS")), (None, ("N",)),
         ("SN", ()), ("ST", ("T", "Rr")), (None, ("TZ", "TK", "RK")), ("SK", ("F",)),
         (None, ("Tt",))]


def _listings():
    out = []
    for si in SI_SHAPES:
        out.append((si, (), False, False))
        for s in SHAPES:
            for dotted in (False, True):
                out.append((si, (s,), False, dotted))
    for a, b in itertools.product(SHAPES, SHAPES):
        out += [(None, (a, b), False, False), (None, (a, b), True, False),
                ("SK", (a, b), False, False)]
    for t in TRIPLES:
        for si in (None, "SK"):
            for dotted in (False, True):
                out.append((si, t, False, dotted))
    return out


def _key(c: Case):
    """The inputs the code actually sees — two cases with the same key are one row."""
    o = oracle(c)
    f = None if c.f_out == "missing" else c.f_resp()
    p = c.p_resp() if o["plain_needed"] else "unread"
    return (c.f_out == "missing", f, p, c.running, c.instance_ok)


def _cases():
    seen, out = set(), []

    def add(c):
        k = _key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)

    for (si, shapes, dup, dotted), (fo, po) in itertools.product(_listings(), F_BIG):
        add(Case(fo, po, _ifaces(si, shapes, dup), dotted, True, True))
    for (si, shapes), fo, po, running, ok, dotted in itertools.product(
            SMALL, F_ALL, P_ALL, (True, False), (True, False), (False,)):
        add(Case(fo, po, _ifaces(si, shapes, False), dotted, running, ok))
    return out


CASES = _cases()


def _proc(stdout="", stderr="", rc=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=rc)


def _fake_run(c: Case, calls):
    def run(cmd, **kw):
        calls.append(list(cmd))
        r = c.f_resp() if "-F" in cmd else c.p_resp()
        if r is None:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 15))
        return _proc(r.stdout, r.stderr, r.rc)
    return run


def _id(n, c: Case):
    # deterministic (never hash(): str hashing is salted per process)
    names = "+".join(f"{i.type_name[:2]}{i.name[4]}" for i in c.ifaces) or "none"
    return (f"{n:05d}-{c.f_out}-{c.p_out}-{names}-{'d' if c.dotted else ''}"
            f"{'r' if c.running else 'R'}{'i' if c.instance_ok else 'I'}")


@pytest.mark.parametrize("c", CASES, ids=[_id(n, c) for n, c in enumerate(CASES)])
def test_state_table(c):
    exp = oracle(c)
    calls = []
    binary = None if c.f_out == "missing" else "/usr/bin/rnstatus"
    with patch('utils.rns_status_parser._find_rnstatus_binary', return_value=binary), \
         patch('subprocess.run', side_effect=_fake_run(c, calls)):
        t = read_interface_traffic()
    leg = interface_traffic_leg(t, running=c.running, instance_ok=c.instance_ok,
                                rnstatus_present=binary is not None)

    assert t.filter_state is exp["state"]
    assert t.filter_reason == exp["reason"]
    assert len(calls) == exp["calls"], calls
    assert leg.lines == exp["lines"]
    assert leg.issues == exp["issues"]
    assert leg.warnings == exp["warnings"]
    assert leg.unobserved == exp["unobserved"]

    summary = "\n".join(summary_lines(leg.issues, leg.warnings, leg.unobserved))
    clean = not (exp["issues"] or exp["warnings"] or exp["unobserved"])
    assert ("All checks passed" in summary) == clean


def test_cases_are_distinct_inputs_and_cover_the_dims():
    keys = [_key(c) for c in CASES]
    assert len(keys) == len(set(keys))
    assert any(len([i for i in c.ifaces if i.type_name != SI_TYPE]) == 3 for c in CASES)
    assert any(c.dotted for c in CASES)
    assert any(c.f_out == "errwords" for c in CASES) and any(c.p_out == "errwords" for c in CASES)
    assert {(c.running, c.instance_ok) for c in CASES} == set(itertools.product((True, False),
                                                                                (True, False)))
    assert any(i.type_name == SI_TYPE and i.shape[2] not in (None, "0 B")
               for c in CASES for i in c.ifaces)
    # every accepted-blind cause is REACHED (r4 nit 4: two never were)
    spots = {u for c in CASES for u in oracle(c)["unobserved"]}
    for cause in ("rnstatus cannot connect", "rnstatus listed no interfaces",
                  "rnstatus timed out", "rnstatus not installed", "rnstatus error: ",
                  "partial listing — ", "without readable counters"):
        assert any(cause in u for u in spots), cause
    # the plain re-read meets every response the -F read can (r4 IMPORTANT)
    assert {c.p_out for c in CASES if oracle(c)["plain_needed"]} == set(P_ALL)
    # RX-only on a non-TCP interface (a "TCP only" RX-only check dies)
    assert any(i.shape[1] and i.type_name == "RNodeInterface" for c in CASES for i in c.ifaces)


def test_oracle_rejects_the_original_clobber_bug():
    # The pre-2026-11-21 reader let the Filtered ↓ overwrite accepted rx.
    c = Case("ok", "ok", _ifaces(None, ("TK",), False), False, True, True)
    exp = oracle(c)
    assert "  TCPInterface[peer0/h0:4242]: ↑478 B ↓254 B" in exp["lines"]
    assert "  TCPInterface[peer0/h0:4242]: ↑478 B ↓2.00 KB" not in exp["lines"]


# ------------------------------------- the wiring into run_rns_diagnostics

def test_apply_traffic_leg_prints_every_line_and_folds_every_entry():
    from handlers._rns_diagnostics_engine import TrafficLeg, apply_traffic_leg
    leg = TrafficLeg(lines=["a", "b"], issues=["i"], warnings=["w"], unobserved=["u"])
    issues, warnings, unobserved, printed = ["i0"], ["w0"], ["u0"], []
    apply_traffic_leg(leg, issues, warnings, unobserved, emit=printed.append)
    assert printed == ["a", "b"]
    assert issues == ["i0", "i"] and warnings == ["w0", "w"] and unobserved == ["u0", "u"]


def test_summary_with_only_warnings_is_not_all_passed():
    s = "\n".join(summary_lines([], ["w"], []))
    assert "All checks passed" not in s
    assert "with warnings" in s

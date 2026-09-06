"""path_trace — where does the path out actually lose? On demand, in the app.

Born 2026-09-06, the afternoon after ``wan_path`` first caught 25-45% transit
loss. The ladder said WHICH RUNG was losing; answering "where does it start"
still meant leaving MeshForge for a scratchpad full of hand-rolled ``ping``
loops — the exact thing ``in_domain_principle`` (MF018) exists to forbid. This
module is that hour, made repeatable, so the next operator (or the next
session) gets the method instead of re-deriving it at midnight.

The method, and the three traps it exists to avoid:

1. **Per-hop loss from a TTL walk is not loss.** Routers rate-limit the
   *generation* of ICMP "time exceeded"; that is a property of the router's
   control plane, not of the path. Measured that day: this box's own LAN
   gateway read **50% "loss"** on a TTL walk while a direct ping to the same
   address read **0%**. So hops are DISCOVERED by TTL walk and then MEASURED
   by direct echo, which is a real round trip to that address.

2. **A hop that never answers echo is OPAQUE, not broken.** Plenty of routers
   drop echo by policy (two on the path traced that day, and the destination
   edge as well). Opaque is its own state and can never be reported as the
   loss point — absence of evidence is not evidence of loss
   (honest_failure_modes #2).

3. **Loss must PERSIST to the target to be real.** A lossy hop followed by
   clean hops is trap 1 wearing a different hat. Only a run of loss that
   reaches the destination localizes anything.

And the one that decides whether any of it matters:

4. **ICMP is not the application.** Destinations rate-limit echo too, so an
   ICMP-only finding is BELIEVED at best. A TCP connect trial against the port
   the app actually uses is the consumer-of-record: that day the pager's host
   completed 2 of 10 handshakes while PyPI completed 10 of 10, which is what
   turned "the graph looks bad" into "the pager is down". ICMP loss with clean
   TCP is POLICING, not a fault, and this module says so rather than paging.

Usable three ways, because the domain has three kinds of operator:
  * TUI      — Network Tools -> "WAN Path Trace" (handlers/network_tools.py)
  * terminal — ``python3 scripts/path_trace.py <host> [...]``
  * a session — ``from utils.path_trace import trace, compare``; the result is
    dataclasses, and ``--json`` prints exactly the same structure.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from utils.wan_path import resolve_host

#: Probes per hop for the direct-echo measurement. 10 gives 10% resolution and
#: keeps a 20-hop trace near two minutes; a careful run uses 20.
DEFAULT_PROBES = 10
#: Probes per TTL during discovery — enough to see whether a hop answers at all.
DISCOVERY_PROBES = 2
DEFAULT_MAX_TTL = 20
#: Loss at or above this counts as present rather than noise.
LOSS_FLOOR_PCT = 10.0
#: Absolute slack a handshake must exceed before its inflation counts as a
#: retransmit — jitter guard for very fast paths, NOT the main test.
HANDSHAKE_SLACK_S = 0.05
#: TCP trials when confirming that ICMP loss is really reaching the app.
DEFAULT_TCP_TRIALS = 10
DEFAULT_TCP_PORT = 443

#: Statuses that mean something is actually wrong with the path or the target.
#: A closed list: every consumer that branches on status must be updated here
#: together, or a new status silently reads as "fine" (honest_failure_modes #7).
FAULT_STATUSES = ("localized", "beyond_visibility", "intermittent", "service",
                  "unreachable")
#: Statuses that mean the path is doing its job.
OK_STATUSES = ("clean", "policing")

#: Echo dispositions for a hop. ``opaque`` is NOT a failure of the path.
ECHO_ANSWERS = "answers"
ECHO_OPAQUE = "opaque"        # identified, but never answers a direct echo
ECHO_UNPROBED = "unprobed"    # never identified, or measurement not attempted


@dataclass
class Hop:
    ttl: int
    addr: Optional[str] = None          # None = no time-exceeded reply at this TTL
    echo: str = ECHO_UNPROBED
    sent: int = 0
    received: int = 0
    loss_pct: Optional[float] = None    # direct-echo loss; None unless echo=answers
    avg_ms: Optional[float] = None

    @property
    def lossy(self) -> bool:
        return self.loss_pct is not None and self.loss_pct >= LOSS_FLOOR_PCT

    @property
    def clean(self) -> bool:
        return self.loss_pct is not None and self.loss_pct < LOSS_FLOOR_PCT


@dataclass
class TcpResult:
    port: int
    trials: int
    ok: int
    avg_connect_s: Optional[float] = None
    #: Per-trial connect seconds. Kept because the MEAN hides the thing we care
    #: about: one retransmitted handshake in ten moves a 0.216 s average to
    #: 0.316 s, which no ratio test on the mean will reliably catch, while the
    #: individual 1.2 s connect is unmistakable.
    times: List[float] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def fail_pct(self) -> float:
        return 100.0 * (self.trials - self.ok) / self.trials if self.trials else 0.0


@dataclass
class Finding:
    """What the trace concluded — and how much of it is actually evidenced."""
    #: Closed vocabulary. ``service`` means the ROUTE is fine and the port is
    #: not; ``intermittent`` means this ICMP sample says clean while the
    #: handshake timing says otherwise — a sampling limit, stated rather than
    #: resolved in the target's favour.
    status: str        # clean | localized | beyond_visibility | intermittent
                       # | policing | service | unreachable | unknown
    message: str
    last_good: Optional[str] = None     # last hop measured clean before the loss
    first_lossy: Optional[str] = None   # first hop whose loss persists to the target
    confidence: str = "believed"        # verified = TCP agrees; believed = ICMP only
    opaque: List[str] = field(default_factory=list)


@dataclass
class TraceResult:
    target: str
    addr: Optional[str] = None
    hops: List[Hop] = field(default_factory=list)
    target_loss_pct: Optional[float] = None
    target_avg_ms: Optional[float] = None
    tcp: Optional[TcpResult] = None
    finding: Optional[Finding] = None
    error: Optional[str] = None
    generated_at: float = 0.0


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------

def _run(cmd: Sequence[str], timeout: float) -> Tuple[int, str]:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except (OSError, ValueError) as exc:
        return 125, str(exc)


_STATS = re.compile(r"(\d+) packets transmitted, (\d+) received")
_RTT = re.compile(r"= [\d.]+/([\d.]+)/")
_FROM = re.compile(r"From (\d{1,3}(?:\.\d{1,3}){3})")


def parse_echo(out: str) -> Dict[str, Optional[float]]:
    """Pure parse of a direct-echo ping. No summary → unmeasured, never zero."""
    m = _STATS.search(out)
    if not m:
        return {"sent": 0, "received": 0, "loss_pct": None, "avg_ms": None}
    sent, recv = int(m.group(1)), int(m.group(2))
    r = _RTT.search(out)
    return {"sent": sent, "received": recv,
            "loss_pct": (100.0 * (sent - recv) / sent) if sent else None,
            "avg_ms": float(r.group(1)) if r else None}


def hop_at_ttl(addr: str, ttl: int, probes: int = DISCOVERY_PROBES) -> Tuple[Optional[str], bool]:
    """Who answers at ``ttl``, and did we arrive? Returns ``(hop_addr, arrived)``.

    The hop address comes from the ``From x.x.x.x`` of a time-exceeded reply.
    A silent TTL yields ``None`` — unknown, and never blamed for anything.
    """
    _rc, out = _run(["ping", "-n", "-c", str(probes), "-i", "0.3", "-W", "2",
                     "-t", str(ttl), addr], timeout=probes * 0.3 + 10)
    if " bytes from " in out:
        return addr, True
    m = _FROM.search(out)
    return (m.group(1) if m else None), False


def echo_hop(addr: str, probes: int = DEFAULT_PROBES) -> Dict[str, Optional[float]]:
    """Direct echo to one address — a real round trip, not a control-plane reply."""
    _rc, out = _run(["ping", "-n", "-c", str(probes), "-i", "0.25", "-W", "2", addr],
                    timeout=probes * 0.25 + 12)
    return parse_echo(out)


def tcp_connect_rate(addr: str, port: int = DEFAULT_TCP_PORT,
                     trials: int = DEFAULT_TCP_TRIALS,
                     connect_timeout: float = 5.0) -> TcpResult:
    """How often a TCP handshake to ``port`` actually completes.

    The consumer-of-record leg: ICMP can be policed at either end, so a loss
    figure that nobody's traffic feels is not an outage. Plain sockets — no
    shell, no curl dependency, and a bounded timeout per trial (MF004 in
    spirit: nothing here may hang a TUI).
    """
    import socket as _sock
    ok, times = 0, []
    for _ in range(trials):
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(connect_timeout)
        t0 = time.monotonic()
        try:
            s.connect((addr, port))
            ok += 1
            times.append(time.monotonic() - t0)
        except OSError:
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass
    return TcpResult(port=port, trials=trials, ok=ok,
                     avg_connect_s=(sum(times) / len(times)) if times else None,
                     times=times)


# --------------------------------------------------------------------------
# verdict — pure, and the whole point of the module
# --------------------------------------------------------------------------

def handshake_inflated(tcp: Optional[TcpResult],
                       target_avg_ms: Optional[float]) -> bool:
    """Did TCP succeed only by RETRANSMITTING? Then the drops are real.

    Caught 2026-09-06 by running this module against the live event it was
    written for: 20% ICMP loss to a host whose handshakes all completed, which
    the first version of ``localize`` cheerfully called "policing — do not
    page". They completed at **0.62 s against a 216 ms path**, while a clean
    control sat at 0.061 s against 60 ms. A handshake is one round trip; three
    round trips means the SYN or its ACK was dropped and resent. Success rate
    alone maps a degraded path onto a healthy-looking value — this module's own
    trap 4, sprung on its author.

    A handshake costs about one round trip. One that costs half again as much,
    plus a jitter floor, was retransmitted — and a retransmission is a dropped
    packet observed at the layer that matters.

    Counted PER CONNECT, never from the mean, because the mean hides exactly the
    case this was written for: one retransmit in ten moved a 0.216 s expectation
    to a 0.314 s average, under every ratio bar tried on the mean, while the
    offending connect itself sat above 1.2 s. When per-trial times are missing
    (an older serialized result) the same rule is applied to the average, which
    still catches heavier loss but will miss a single retransmit — so absence of
    times makes this test LESS sensitive, never more confident.
    """
    return slow_handshakes(tcp, target_avg_ms) > 0


def slow_handshakes(tcp: Optional[TcpResult], target_avg_ms: Optional[float]) -> int:
    """How many completed handshakes cost more than one round trip should."""
    if tcp is None or not target_avg_ms:
        return 0
    expected = target_avg_ms / 1000.0
    if expected <= 0:
        return 0
    ceiling = 1.5 * expected + HANDSHAKE_SLACK_S
    if tcp.times:
        return sum(1 for t in tcp.times if t >= ceiling)
    if tcp.avg_connect_s is None:
        return 0
    return 1 if tcp.avg_connect_s >= ceiling else 0


def localize(hops: Sequence[Hop], target_loss_pct: Optional[float],
             tcp: Optional[TcpResult] = None,
             target_avg_ms: Optional[float] = None,
             floor: float = LOSS_FLOOR_PCT) -> Finding:
    """Name where the loss starts — or say honestly that we cannot see it.

    Pure: takes measurements, returns a Finding. Every trap in the module
    docstring is a branch here, so the reasoning is tested rather than
    re-remembered under pressure.
    """
    opaque = [h.addr for h in hops if h.echo == ECHO_OPAQUE and h.addr]

    # TCP is the arbiter whenever we have it — it outranks every ICMP number
    # below, because it is the layer the application actually uses.
    tcp_clean = tcp is not None and tcp.trials > 0 and tcp.fail_pct < floor
    tcp_bad = tcp is not None and tcp.trials > 0 and tcp.fail_pct >= floor

    if target_loss_pct is None:
        if tcp_bad:
            return Finding("unreachable",
                           "target never answered ICMP and %d/%d TCP handshakes failed — "
                           "down, or filtering both" % (tcp.trials - tcp.ok, tcp.trials),
                           confidence="verified", opaque=opaque)
        if tcp_clean:
            return Finding("policing",
                           "target does not answer ICMP at all, but %d/%d TCP handshakes "
                           "succeeded — the path is fine and echo is filtered"
                           % (tcp.ok, tcp.trials), confidence="verified", opaque=opaque)
        return Finding("unknown",
                       "target did not answer ICMP and no TCP trial was run — "
                       "unmeasured, which is not the same as down",
                       confidence="believed", opaque=opaque)

    if target_loss_pct < floor:
        # Handshake timing outranks a clean ICMP sample. Loss is SAMPLED — ten
        # probes against a path that has read 0/20/30/40% across consecutive
        # runs will sometimes come up empty, and the timing still knows.
        slow = slow_handshakes(tcp, target_avg_ms)
        if slow:
            return Finding("intermittent",
                           "this ICMP sample reads clean (%.0f%%), but %d of %d handshake(s) "
                           "to port %d cost more than a round trip (worst %.2fs against a "
                           "%.0f ms path) — those were retransmitted, so drops ARE happening "
                           "and this sample missed them. Re-run with more probes before "
                           "believing the clean number."
                           % (target_loss_pct, slow, tcp.ok, tcp.port,
                              max(tcp.times) if tcp.times else (tcp.avg_connect_s or 0.0),
                              target_avg_ms),
                           confidence="verified", opaque=opaque)
        if tcp_bad:
            return Finding("service",
                           "path is clean (%.0f%% ICMP loss, handshakes at the expected "
                           "round trip) but %d/%d TCP handshakes to port %d failed — not a "
                           "packet-loss problem; look at the service, not the route"
                           % (target_loss_pct, tcp.trials - tcp.ok, tcp.trials, tcp.port),
                           confidence="verified", opaque=opaque)
        return Finding("clean", "no loss to the target (%.0f%%)" % target_loss_pct,
                       confidence="verified" if tcp_clean else "believed", opaque=opaque)

    # There IS ICMP loss at the target. Does anything actually feel it?
    # "Every handshake succeeded" is only good news if they succeeded FAST —
    # see handshake_inflated.
    inflated = handshake_inflated(tcp, target_avg_ms)
    if tcp_clean and not inflated:
        return Finding("policing",
                       "%.0f%% ICMP loss to the target, but %d/%d TCP handshakes "
                       "succeeded at the expected round trip — rate-limited echo, "
                       "not a lossy path. Do not page on this."
                       % (target_loss_pct, tcp.ok, tcp.trials),
                       confidence="verified", opaque=opaque)

    conf = "believed"
    detail = ""
    if tcp_bad:
        conf = "verified"
        detail = " (confirmed at the app: %d/%d TCP handshakes to port %d failed)" % (
            tcp.trials - tcp.ok, tcp.trials, tcp.port)
    elif inflated:
        conf = "verified"
        detail = (" (all %d handshakes to port %d completed, but %d cost more than a round "
                  "trip — worst %.2fs against a %.0f ms path — so TCP is paying retransmits "
                  "and the drops are real)"
                  % (tcp.ok, tcp.port, slow_handshakes(tcp, target_avg_ms),
                     max(tcp.times) if tcp.times else (tcp.avg_connect_s or 0.0),
                     target_avg_ms))

    # Trap 3: only a run of loss that PERSISTS to the target localizes anything.
    # Walk from the far end back to the last hop that was measured clean.
    measured = [h for h in hops if h.echo == ECHO_ANSWERS]
    first_lossy = None
    first_idx = -1
    for i, h in enumerate(measured):
        if all(x.lossy for x in measured[i:]):
            first_lossy, first_idx = h, i
            break

    if first_lossy is None:
        # Every hop we could measure is clean, yet the target loses. The drop is
        # on a segment we cannot see from here — commonly the RETURN path, which
        # a forward trace structurally cannot observe. Say that; do not invent
        # a hop, and do not blame the opaque ones.
        last_clean = measured[-1] if measured else None
        msg = ("%.0f%% loss to the target, but every hop that answers echo is clean"
               "%s — the drop is on a segment this trace cannot see (most often the "
               "return path, which a forward trace cannot observe)"
               % (target_loss_pct, detail))
        if last_clean is not None:
            msg += "; last hop measured clean: %s at %.0f ms" % (
                last_clean.addr, last_clean.avg_ms or 0.0)
        if opaque:
            msg += ". %d hop(s) answer no echo and are opaque, not implicated: %s" % (
                len(opaque), ", ".join(opaque))
        return Finding("beyond_visibility", msg,
                       last_good=last_clean.addr if last_clean else None,
                       confidence=conf, opaque=opaque)

    # Index tracked in the loop, never recovered with list.index(): Hop is a
    # dataclass, so two hops with identical readings compare EQUAL and a router
    # that appears twice on a path would resolve to the wrong position.
    last_good = measured[first_idx - 1] if first_idx > 0 else None
    msg = "loss begins at %s (%.0f%%) and persists to the target (%.0f%%)%s" % (
        first_lossy.addr, first_lossy.loss_pct or 0.0, target_loss_pct, detail)
    if last_good is not None:
        msg += "; last hop clean: %s (%.0f%%)" % (last_good.addr, last_good.loss_pct or 0.0)
    if opaque:
        msg += ". Opaque (no echo, not implicated): %s" % ", ".join(opaque)
    return Finding("localized", msg,
                   last_good=last_good.addr if last_good else None,
                   first_lossy=first_lossy.addr, confidence=conf, opaque=opaque)


def divergence(a: TraceResult, b: TraceResult) -> Optional[int]:
    """First hop index where two traces stop sharing a path, or None.

    The trick that cracked the 09-06 event: a clean destination and a lossy one
    left the same ISP and split at hop 3 into different transit providers, which
    named the suspect without needing to see inside anyone's network.
    """
    for i in range(min(len(a.hops), len(b.hops))):
        x, y = a.hops[i].addr, b.hops[i].addr
        if x and y and x != y:
            return i
    return None


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def trace(target: str, probes: int = DEFAULT_PROBES, max_ttl: int = DEFAULT_MAX_TTL,
          tcp_port: Optional[int] = DEFAULT_TCP_PORT,
          tcp_trials: int = DEFAULT_TCP_TRIALS,
          progress=None) -> TraceResult:
    """Discover the path to ``target``, measure every hop honestly, and localize.

    ``progress`` is an optional ``callable(stage: str, detail: str)`` so a TUI
    can show life during the two minutes this takes. Pass ``tcp_port=None`` to
    skip the confirmation leg — the Finding then stays BELIEVED, and says so.
    """
    def say(stage, detail=""):
        if progress:
            progress(stage, detail)

    res = TraceResult(target=target, generated_at=time.time())
    addr, err = resolve_host(target)
    if err:
        res.error = err
        res.finding = Finding("unknown", "cannot resolve %s: %s" % (target, err))
        return res
    res.addr = addr

    # 1. Discover the path (cheap probes; identity only, never loss — trap 1).
    say("discover", "walking TTLs toward %s" % addr)
    for ttl in range(1, max_ttl + 1):
        hop_addr, arrived = hop_at_ttl(addr, ttl)
        if arrived:
            break
        res.hops.append(Hop(ttl=ttl, addr=hop_addr))
        say("discover", "ttl %d: %s" % (ttl, hop_addr or "silent"))

    # 2. Measure each identified hop by DIRECT echo — the only honest per-hop
    #    number. A hop that answers no echo is opaque, not lossy (trap 2).
    for hop in res.hops:
        if not hop.addr:
            continue
        say("measure", "echo %s" % hop.addr)
        st = echo_hop(hop.addr, probes)
        if st["sent"] == 0 or st["received"] == 0:
            hop.echo = ECHO_OPAQUE
            hop.sent = int(st["sent"] or 0)
            continue
        hop.echo = ECHO_ANSWERS
        hop.sent = int(st["sent"] or 0)
        hop.received = int(st["received"] or 0)
        hop.loss_pct = st["loss_pct"]
        hop.avg_ms = st["avg_ms"]

    # 3. The target itself.
    say("measure", "echo target %s" % addr)
    st = echo_hop(addr, probes)
    if st["sent"]:
        res.target_loss_pct = st["loss_pct"]
        res.target_avg_ms = st["avg_ms"]

    # 4. Confirm at the layer the application uses (trap 4).
    if tcp_port:
        say("tcp", "%d handshakes to port %d" % (tcp_trials, tcp_port))
        res.tcp = tcp_connect_rate(addr, tcp_port, tcp_trials)

    res.finding = localize(res.hops, res.target_loss_pct, res.tcp, res.target_avg_ms)
    return res


def compare(results: Sequence[TraceResult]) -> List[str]:
    """Cross-trace reading: which targets lose, and where their paths split.

    One trace tells you a destination is lossy. Two tell you whether the fault
    is yours: if a clean target and a lossy one share the first N hops and then
    split, the suspect is what they do NOT share.
    """
    lines: List[str] = []
    lossy = [r for r in results if r.finding and r.finding.status in FAULT_STATUSES]
    clean = [r for r in results if r.finding and r.finding.status in OK_STATUSES]
    if not lossy:
        lines.append("All %d target(s) reachable without loss." % len(results))
        return lines
    lines.append("%d of %d target(s) losing: %s" % (
        len(lossy), len(results), ", ".join(r.target for r in lossy)))
    if not clean:
        lines.append("No clean target measured — with nothing to compare against, "
                     "this cannot separate 'your uplink' from 'one provider'. "
                     "Add a known-good target.")
        return lines
    lines.append("Clean for comparison: %s" % ", ".join(r.target for r in clean))
    for bad in lossy:
        for good in clean:
            i = divergence(bad, good)
            if i is None:
                continue
            shared = i
            lines.append(
                "  %s vs %s: identical for %d hop(s), then split — %s goes via %s, "
                "%s via %s. The suspect is what they do not share."
                % (bad.target, good.target, shared,
                   bad.target, bad.hops[i].addr or "?",
                   good.target, good.hops[i].addr or "?"))
    return lines


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render(res: TraceResult) -> str:
    """Human-readable report — the same text the TUI shows and the CLI prints."""
    out: List[str] = []
    head = "%s" % res.target
    if res.addr and res.addr != res.target:
        head += " (%s)" % res.addr
    out.append("=== path trace: %s ===" % head)
    if res.error:
        out.append("UNMEASURED: %s" % res.error)
        return "\n".join(out)
    out.append("%-4s %-16s %-8s %-7s %s" % ("ttl", "hop", "echo", "loss", "avg"))
    for h in res.hops:
        if not h.addr:
            out.append("%-4d %-16s %-8s %-7s %s" % (h.ttl, "*", "-", "-",
                                                    "no time-exceeded reply"))
        elif h.echo == ECHO_OPAQUE:
            out.append("%-4d %-16s %-8s %-7s %s" % (h.ttl, h.addr, "opaque", "-",
                                                    "answers no echo (policy) — not implicated"))
        else:
            out.append("%-4d %-16s %-8s %-7s %s" % (
                h.ttl, h.addr, "answers", "%.0f%%" % (h.loss_pct or 0.0),
                ("%.1f ms" % h.avg_ms) if h.avg_ms is not None else "-"))
    tl = ("%.0f%%" % res.target_loss_pct) if res.target_loss_pct is not None else "no reply"
    out.append("%-4s %-16s %-8s %-7s %s" % ("--", res.addr or "?", "TARGET", tl,
                                            ("%.1f ms" % res.target_avg_ms)
                                            if res.target_avg_ms is not None else "-"))
    if res.tcp:
        out.append("TCP :%d — %d/%d handshakes ok%s" % (
            res.tcp.port, res.tcp.ok, res.tcp.trials,
            (", avg %.3fs" % res.tcp.avg_connect_s) if res.tcp.avg_connect_s else ""))
    else:
        out.append("TCP — not run; the finding below is ICMP-only")
    if res.finding:
        out.append("")
        out.append("%s [%s]: %s" % (res.finding.status.upper().replace("_", " "),
                                    res.finding.confidence, res.finding.message))
    out.append("")
    out.append("Note: per-hop loss here is DIRECT echo to each hop, not the "
               "traceroute column — routers rate-limit time-exceeded replies, so "
               "that column routinely accuses healthy hops.")
    return "\n".join(out)

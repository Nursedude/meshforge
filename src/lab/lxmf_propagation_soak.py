"""LXMF store-and-forward exerciser — the propagation-leg canary.

Slice 3 of the propagation arc (`.claude/plans/propagation_leg.md`). Sister to
``lxmf_multi_user_synth`` (which only ever sends DIRECT, and therefore never
exercised store-and-forward at all — that gap is why this module exists).

What it proves, per round:

  1. a receiver identity **B** that has never announced is unreachable, so no
     direct path to it can exist;
  2. sender **A** sends with ``desired_method=PROPAGATED``, so the message can
     only reach the configured propagation node's STORE;
  3. B then comes up and pulls it back from that node.

If B receives the message, the node stored and forwarded it. That is the one
property ``probe_lxmf_propagation_node_dark`` cannot see: it watches whether
the node *announces*, so a node that announces perfectly while silently
dropping every stored message reads clean forever. In a small lab, traffic to
an offline peer essentially never happens organically, so without this the
organ can be quietly useless for months while every gate stays green.

Each run writes ONE envelope JSON. ``probe_propagation_soak_degraded`` consumes
it and owns the alerting, including the SILENCE leg — for a fixed-cadence
generator, going quiet IS the failure.

Synthetic traffic is deliberately isolated from real telemetry: this module
builds its OWN LXMRouter instances with throwaway identities and its own
storage, so the gateway's ``delivery_counters`` never see these messages. The
whole #74 arc was making ``confirmation_rate`` honest; feeding it manufactured
round-trips would re-corrupt the exact metric that was fixed.

Usage::

    python3 -m lab.lxmf_propagation_soak --output json
    python3 -m lab.lxmf_propagation_soak --rounds 2 --output text
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional
from utils.tx_guard import assert_rns_tx_allowed

logger = logging.getLogger(__name__)

# Default pass bar. One failed round out of one is a total failure of the
# property under test, so unlike the synth soak's 0.95 ACK ratio there is no
# meaningful partial credit at rounds=1 — the threshold matters once rounds>1.
DEFAULT_OK_RATIO_THRESHOLD = 1.0

# A round is send -> stored -> pull. Bounded generously: the sender must
# produce a propagation stamp and the pull opens a fresh link.
DEFAULT_SEND_TIMEOUT_S = 180.0
DEFAULT_PULL_TIMEOUT_S = 180.0

# Every synthetic message carries this so it is identifiable anywhere it
# surfaces (node store, a stray inbox, a log) and can never be mistaken for
# operator traffic.
MARKER = "MF-PROPSOAK"

# An RNS destination hash is 128 bits = exactly 32 hex chars.
_HASH_RE = re.compile(r"[0-9a-f]{32}")

_STAGE_SEND = "send"
_STAGE_PULL = "pull"
# Pull INFRASTRUCTURE failure: the receiver could not complete a sync with
# the node (no path, link failed, child crashed) — the message may well be
# sitting in the store. Says nothing about store-and-forward, so it is
# indeterminate like a send stall (2026-07-21 review F1: before this stage
# existed, a transient pull-link failure — e.g. rnsd restarting between the
# send and pull children — read as a DEFINITIVE store-and-forward failure
# and paged against a healthy node).
_STAGE_PULL_LINK = "pull_link"


@dataclass
class RoundResult:
    """One send -> store -> pull cycle."""

    seq: int
    ok: bool
    stage: str = ""              # failing stage when not ok
    reason: str = ""
    store_latency_s: Optional[float] = None      # A -> node accepted
    retrieve_latency_s: Optional[float] = None   # B pull -> message in hand
    total_latency_s: Optional[float] = None


@dataclass
class SoakReport:
    """The envelope the probe consumes. Field names deliberately mirror the
    synth soak's so both are read the same way."""

    started_at_iso: str
    finished_at_iso: str
    propagation_node: str
    rounds: int
    total_samples: int
    total_ok: int
    ok_ratio_threshold: float
    # None when the run was INDETERMINATE — it could not exercise the node at
    # all (every send timed out generating a propagation stamp, a sender-local
    # CPU condition). The probe reads None as "held, never healthy", never as a
    # failure: a stamp we were too slow to generate says NOTHING about whether
    # the node stores and forwards (honest_failure_modes #1 — do not map
    # "couldn't test" to "test failed").
    pass_envelope: Optional[bool]
    total_indeterminate: int = 0
    round_results: List[RoundResult] = field(default_factory=list)
    marker: str = MARKER

    @property
    def ok_ratio(self) -> float:
        # Same denominator as the verdict (testable rounds), or the envelope
        # could publish ratio-vs-threshold pairs that contradict its own
        # pass_envelope (review: ratio 0.5 / threshold 1.0 / pass=True when
        # one stall accompanied one success).
        testable = self.total_samples - self.total_indeterminate
        if testable <= 0:
            return 0.0
        return self.total_ok / testable

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok_ratio"] = self.ok_ratio
        # Surface the best-known latency figures at the top level: this is the
        # real SLO of store-and-forward, and nothing in the fleet measured it
        # before this module existed.
        oks = [r for r in self.round_results if r.ok and r.total_latency_s is not None]
        if oks:
            lat = sorted(r.total_latency_s for r in oks)
            d["latency_s"] = {
                "min": round(lat[0], 2),
                "max": round(lat[-1], 2),
                "median": round(lat[len(lat) // 2], 2),
            }
        else:
            # No successful round means no latency to report. Say so with an
            # explicit null rather than omitting the key or emitting 0 — a
            # zero here would read as "instant", the healthiest possible
            # value, for the least healthy possible run.
            d["latency_s"] = None
        return d


def _is_indeterminate(r: RoundResult) -> bool:
    """A round that could not exercise the node.

    Only a COMPLETED-pull failure proves store-and-forward is broken: the
    node accepted the message (send succeeded), served a sync to the
    receiver, and the message was not in it. Everything else says nothing
    about the node:

    - SEND-stage failure — the message never reached the node; most often
      the sender was too slow generating the propagation stamp (CPU-bound
      proof-of-work, high variance on a loaded box). Sender-local.
    - PULL_LINK-stage failure — the receiver could not complete a sync
      (no path, link failed, child crashed/timed out). Transport-local; the
      message may be sitting in the store (2026-07-21 review F1).

    ⚠️ This exclusion creates an ABSORBING state by itself — a node that
    refuses every upload, or a box that can never finish a stamp, reads
    indeterminate forever with every dashboard green. The probe's
    sustained-indeterminate leg exists precisely to page on that (review F2);
    do not remove one without the other.
    """
    return (not r.ok) and r.stage in (_STAGE_SEND, _STAGE_PULL_LINK)


def build_report(
    round_results: List[RoundResult],
    *,
    propagation_node: str,
    started_at_iso: str,
    finished_at_iso: str,
    ok_ratio_threshold: float = DEFAULT_OK_RATIO_THRESHOLD,
) -> SoakReport:
    """Assemble the envelope. Pure — no RNS, so tests cover the verdict logic.

    ``pass_envelope`` is tri-state:
      * True  — at least one round actually exercised the node and the OK ratio
        over the rounds that DID reach it meets the threshold.
      * False — a round reached the node's store and the node failed to return
        it (a PULL-stage failure). This is the only real store-and-forward
        failure, and it is what the probe pages on.
      * None  — the run could not exercise the node at all (every round was
        send-indeterminate) OR produced no rounds. Nothing was proven; the
        probe holds. Never a pass — an empty/untested result satisfying a ratio
        test is honest_failure_modes #1 in arithmetic form.
    """
    total = len(round_results)
    ok = sum(1 for r in round_results if r.ok)
    indeterminate = sum(1 for r in round_results if _is_indeterminate(r))
    real_failures = sum(
        1 for r in round_results if (not r.ok) and not _is_indeterminate(r))
    testable = ok + real_failures      # rounds that actually reached the node

    # The threshold applies over the rounds that ACTUALLY reached the node, so
    # a transient pull hiccup is tolerated exactly as the synth soak tolerates
    # a dropped ACK — but send-stalls are excluded from the denominator, never
    # counted as failures. No testable rounds at all → None (nothing proven).
    if testable == 0:
        passed: Optional[bool] = None
    elif (ok / testable) >= ok_ratio_threshold:
        passed = True
    else:
        passed = False

    return SoakReport(
        started_at_iso=started_at_iso,
        finished_at_iso=finished_at_iso,
        propagation_node=propagation_node,
        rounds=total,
        total_samples=total,
        total_ok=ok,
        total_indeterminate=indeterminate,
        ok_ratio_threshold=ok_ratio_threshold,
        pass_envelope=passed,
        round_results=list(round_results),
    )


def worst_round(round_results) -> Optional[str]:
    """Compact ``round N failed at <stage>: <reason>`` for the worst failure.

    Prefers a DEFINITIVE failure over an indeterminate stall (review F7: on a
    mixed run — round 1 send-stall, round 2 pull-fail — the first-match rule
    reported the one round that is by design NOT a failure as THE failure).
    None when nothing failed or the input is misshaped — a summary helper must
    never raise inside a probe.
    """
    if not isinstance(round_results, list):
        return None
    fallback = None
    for r in round_results:
        if isinstance(r, dict):
            ok, seq, stage, reason = (r.get("ok"), r.get("seq"),
                                      r.get("stage"), r.get("reason"))
        elif isinstance(r, RoundResult):
            ok, seq, stage, reason = r.ok, r.seq, r.stage, r.reason
        else:
            continue
        if ok is False:
            line = f"round {seq} failed at {stage or '?'}: {reason or 'unknown'}"
            if stage not in (_STAGE_SEND, _STAGE_PULL_LINK):
                return line                # definitive failure wins
            if fallback is None:
                fallback = line
    return fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_propagation_node(explicit: Optional[str]) -> Optional[str]:
    """Configured propagation node hash: CLI > gateway.json.

    Read from the same ``gateway.json`` the gateway itself uses, so the drill
    always exercises the node the fleet actually depends on rather than one
    pinned in a script that silently drifts from it.
    """
    if explicit:
        return explicit.strip().lower() or None
    try:
        from utils.paths import get_real_user_home
        cfg = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
        with open(cfg, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        node = (doc.get("rns", {}) or {}).get("propagation_node") or ""
        return node.strip().lower() or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


# --------------------------------------------------------------- live path


def _state_name(msg) -> str:
    import LXMF  # lazy
    for attr in ("DRAFT", "OUTBOUND", "SENDING", "SENT", "DELIVERED",
                 "REJECTED", "CANCELLED", "FAILED"):
        if getattr(LXMF.LXMessage, attr, None) == msg.state:
            return attr
    return str(msg.state)


def _router_storage(role: str) -> str:
    """PERSISTENT per-role LXMF storage (ratchets + state).

    ⚠️ Must NOT be a TemporaryDirectory. RNS caches a destination's ratchet
    public key, so after one successful round the SENDER encrypts to B using a
    ratchet — while a receiver whose storage was wiped no longer holds the
    matching private ratchet and silently fails to decrypt ("Decryption with
    ratchets failed"), dropping the message before the delivery callback. The
    drill then reports "stored but not retrieved" against a perfectly healthy
    node.

    Live-caught 2026-07-21: with throwaway storage the drill passed exactly
    ONCE and failed every run after — the classic pass@1 trap, and a canary
    that cries wolf is worse than no canary. Real clients persist this; so do we.
    """
    from utils.paths import get_real_user_home
    root = (get_real_user_home() / ".local" / "state" / "meshforge"
            / "propagation_soak" / f"router_{role}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return str(root)


def default_identity_path(token: str) -> str:
    """Path to a round's throwaway receiver identity, keyed by a UNIQUE token.

    The receiver identity is regenerated EVERY round, and its filename carries
    a token unique to THIS fire.

    Two reasons the identity is fresh-per-round and unique-per-fire:

    * Fresh per round — RNS caches a destination's ratchet public key at the
      SENDER, outside the LXMF router's storage. With a stable receiver, the
      second and every later round is ratchet-encrypted, and a receiver that no
      longer holds the matching private half silently fails to decrypt
      ("Decryption with ratchets failed") and drops the message before the
      callback, so the drill reports "stored but not retrieved" against a
      healthy node. A virgin identity has no cached ratchet anywhere and models
      a never-seen offline peer honestly. (Live-caught 2026-07-21: the drill
      passed exactly ONCE then failed every later round — the pass@1 trap.)

    * Unique per fire — a FIXED filename is a collision, not a convention
      (honest_failure_modes #8). If two fires overlap (a manual run during the
      scheduled one; ``enable --now`` racing a manual run — live-caught
      2026-07-21 on moc3), the second fire's send overwrites the shared
      identity file, so the first fire's pull loads the WRONG identity and
      retrieves nothing — a false CONCERN against a healthy node. The token
      (the parent's PID + a monotonic-ish counter) makes concurrent fires
      independent.
    """
    from utils.paths import get_real_user_home
    root = (get_real_user_home() / ".local" / "state" / "meshforge"
            / "propagation_soak")
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in token if c.isalnum() or c in "-_")[:64] or "round"
    return str(root / f"round_b_identity_{safe}")


def _new_round_identity(path: str):
    """Create + persist a fresh receiver identity at ``path``."""
    import RNS
    ident = RNS.Identity()
    ident.to_file(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return ident


def _load_round_identity(path: str):
    """Load the identity the send phase created at ``path``. None when absent."""
    import RNS
    if not os.path.isfile(path):
        return None
    return RNS.Identity.from_file(path)


def _src_root() -> str:
    """The ``src`` dir, so a child process can ``-m lab....`` regardless of cwd."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _terminate_process_group(proc) -> None:
    """SIGKILL the child's whole process GROUP, best effort.

    ⚠️ Refuses to signal our OWN group. With ``start_new_session=True`` the
    child always leads its own group so this can't happen — but if that flag
    is ever dropped, ``killpg`` would take out the fire script and this
    parent along with the child. A safety check, not dead code.
    """
    import signal

    try:
        pgid = os.getpgid(proc.pid)
        own = os.getpgid(0)
    except OSError:                        # child already reaped
        pgid = own = None

    if pgid is not None and pgid != own:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except OSError:                    # noqa: BLE001 - fall through
            pass

    try:                                   # last resort: the child alone
        proc.kill()
    except OSError:                        # noqa: BLE001 - already gone
        pass


def _run_role(role: str, body: str, node_hash: str, timeout_s: float,
              identity_path: str) -> dict:
    """Run one role in a CHILD PROCESS and return its JSON result.

    ⚠️ Load-bearing design, learned the hard way 2026-07-21: the sender and the
    receiver must NOT share a process. An earlier version created both
    LXMRouters in one process (calling ``exit_handler()`` on the first before
    building the second) and it worked exactly ONCE, then failed every
    subsequent run at the pull stage — the node had demonstrably served the
    message (its `served to clients` counter advanced and its store drained)
    while the in-process receiver's delivery callback never fired. A two-box,
    two-process drill against the same node passed at the same moment, which is
    what isolated the fault to process sharing rather than to the node.

    That "worked once, then never" is exactly the pass@1 trap: one green run is
    not evidence of a working canary. Each role now gets a clean interpreter,
    mirroring the manual drill that is actually proven.
    """
    import subprocess

    # The child's own phase deadline rides along (review F4: the operator
    # knobs used to stop at the parent — the child kept the module default,
    # so raising PROP_SEND_TIMEOUT on a slow-stamp box silently did nothing).
    cmd = [sys.executable, "-m", "lab.lxmf_propagation_soak",
           "--role", role, "--body", body, "--propagation-node", node_hash,
           "--identity-path", identity_path,
           "--phase-timeout", f"{max(timeout_s - 30.0, 30.0):.0f}"]
    # An orchestration-level failure (child hung/killed/unstartable) proves
    # nothing about the node — "indeterminate" lets run_round classify pull
    # failures honestly (review F1); the send stage is already indeterminate
    # by stage.
    #
    # ⚠️ start_new_session + killpg, NOT subprocess.run(timeout=) — the
    # 2026-08-02 moc3 defect. The send role's propagation stamp is CPU-bound
    # proof-of-work that LXMF farms out to one FORKED worker per core
    # (LXStamper: multiprocessing.get_context("fork"), Process(daemon=True)).
    # subprocess.run's timeout path SIGKILLs only the DIRECT child, and
    # SIGKILL skips the atexit handler that reaps daemonic workers — so every
    # send-timeout orphaned 4 workers INTO THE SERVICE CGROUP, still pegging
    # every core. Type=oneshot then waited on that non-empty cgroup, SIGTERMed
    # (absorbed: the workers inherit RNS's SIGTERM handler by fork), and
    # SIGKILLed 90s later — turning an honest "OK indeterminate" drill into
    # `Result: timeout`, a red unit and a user_timer_unit_failing_any
    # escalation, plus ~6 core-minutes burned after the drill gave up.
    # Killing the process GROUP reaps the workers with the child and does not
    # depend on LXMF's or RNS's signal handling.
    try:
        proc = subprocess.Popen(cmd, cwd=_src_root(), text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as exc:
        return {"ok": False, "indeterminate": True,
                "reason": f"{role} phase could not start: {exc}"}

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_process_group(proc)
        try:                               # reap; never leave a zombie
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # noqa: BLE001 - already killed
            pass
        return {"ok": False, "indeterminate": True,
                "reason": f"{role} phase exceeded {timeout_s:.0f}s"}
    except BaseException:                  # Ctrl-C on a manual run
        _terminate_process_group(proc)
        raise

    out = (stdout or "").strip().splitlines()
    for line in reversed(out):                 # last JSON line is the result
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    err = (stderr or "").strip().splitlines()
    tail = err[-1] if err else f"rc={proc.returncode}, no result line"
    return {"ok": False, "indeterminate": True,
            "reason": f"{role} phase produced no result ({tail})"}


def _phase_send(body: str, node_hash: str, identity_path: str,
                timeout_s: float = DEFAULT_SEND_TIMEOUT_S) -> dict:
    """Child role: send ONE message with desired_method=PROPAGATED."""
    import LXMF
    import RNS
    from lab._lab_common import load_or_create_identity

    # monotonic: these boxes are RTC-less and the timer is Persistent=true, so
    # a catch-up fire right after boot lands exactly when fake-hwclock/NTP
    # steps the wall clock — a time.time() deadline could expire instantly or
    # publish a negative latency (honest_failure_modes #6; review F5).
    t0 = time.monotonic()
    ident_a, _ = load_or_create_identity("prop_soak_a")
    ident_b = _new_round_identity(identity_path)   # virgin, unique per fire

    store = _router_storage("a")
    # autopeer=False: a drill client must never peer our fleet's store out
    # to the foreign nodes we deliberately did not adopt.
    router = LXMF.LXMRouter(storagepath=store, autopeer=False)
    try:
        source = router.register_delivery_identity(
            ident_a, display_name="propsoak-A")
        router.set_outbound_propagation_node(bytes.fromhex(node_hash))
        # OUT destination only. Constructing B's IN destination would
        # REGISTER it in this process and make a local direct delivery
        # possible — passing the drill while proving nothing.
        dest = RNS.Destination(ident_b, RNS.Destination.OUT,
                               RNS.Destination.SINGLE, "lxmf", "delivery")
        msg = LXMF.LXMessage(dest, source, body, MARKER,
                             desired_method=LXMF.LXMessage.PROPAGATED)
        assert_rns_tx_allowed(kind="lxmf_outbound", detail="lab propagation soak")
        router.handle_outbound(msg)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if msg.state in (LXMF.LXMessage.SENT, LXMF.LXMessage.DELIVERED):
                return {"ok": True,
                        "latency_s": round(time.monotonic() - t0, 2)}
            if msg.state in (LXMF.LXMessage.FAILED, LXMF.LXMessage.REJECTED,
                             LXMF.LXMessage.CANCELLED):
                return {"ok": False,
                        "reason": f"send state {_state_name(msg)}"}
            time.sleep(2)
        return {"ok": False,
                "reason": f"send timeout in state {_state_name(msg)}"}
    finally:
        try:
            router.exit_handler()
        except Exception:                  # noqa: BLE001 - best effort
            pass


def _pr_state_name(router, state) -> str:
    """Human name for an LXMRouter PR_* transfer state (best-effort)."""
    try:
        for attr in dir(type(router)):
            if attr.startswith("PR_") and getattr(type(router), attr) == state:
                return attr
    except Exception:                      # noqa: BLE001 - label only
        pass
    return str(state)


def _phase_pull(body: str, node_hash: str, identity_path: str,
                timeout_s: float = DEFAULT_PULL_TIMEOUT_S) -> dict:
    """Child role: bring the receiver up and pull from the propagation node.

    Tri-state (review F1): only a COMPLETED sync that did not contain our
    message is a store-and-forward failure. A sync that never completed
    (no path, link failed) is transport trouble — ``indeterminate: True`` so
    the parent stamps _STAGE_PULL_LINK, never a false CONCERN against a
    node that may be holding the message just fine.
    """
    import LXMF

    t0 = time.monotonic()                  # F5: RTC-less boxes, see _phase_send
    ident_b = _load_round_identity(identity_path)
    if ident_b is None:
        # The send phase owns creating it; its absence means the phases ran out
        # of order, never that store-and-forward failed. Say which.
        return {"ok": False, "indeterminate": True,
                "reason": "no round identity on disk - send phase did not run"}

    store = _router_storage("b")
    router = LXMF.LXMRouter(storagepath=store, autopeer=False)
    received: list = []
    try:
        router.register_delivery_callback(lambda m: received.append(m))
        router.register_delivery_identity(ident_b, display_name="propsoak-B")
        # NOT set_inbound_propagation_node(): it raises NotImplementedError
        # in lxmf 1.0.1+mf.1 — one setter serves both directions.
        router.set_outbound_propagation_node(bytes.fromhex(node_hash))
        try:
            router.request_messages_from_propagation_node(
                ident_b, max_messages=0)
        except Exception as exc:           # noqa: BLE001 - setup, not verdict
            return {"ok": False, "indeterminate": True,
                    "reason": f"pull sync could not start: {exc}"}

        pr_complete = getattr(type(router), "PR_COMPLETE", 0x07)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if any(_matches(m, body) for m in received):
                return {"ok": True,
                        "latency_s": round(time.monotonic() - t0, 2)}
            time.sleep(2)

        pr_state = getattr(router, "propagation_transfer_state", None)
        if pr_state == pr_complete:
            # The node served a full sync and our message was not in it —
            # the one outcome that IS a store-and-forward failure.
            return {"ok": False,
                    "reason": (f"sync completed but message not returned "
                               f"within {timeout_s:.0f}s "
                               f"({len(received)} other message(s) returned)")}
        return {"ok": False, "indeterminate": True,
                "reason": (f"pull sync never completed within "
                           f"{timeout_s:.0f}s (transfer state "
                           f"{_pr_state_name(router, pr_state)}) — transport "
                           f"trouble, not a store-and-forward verdict")}
    finally:
        try:
            router.exit_handler()
        except Exception:                  # noqa: BLE001 - best effort
            pass


def _matches(message, body: str) -> bool:
    """True when a delivered LXMF message is the one this round sent."""
    try:
        content = message.content
        if isinstance(content, (bytes, bytearray)):
            content = content.decode("utf-8", errors="replace")
        return body in str(content)
    except Exception:                          # noqa: BLE001 - never raise
        return False


def run_round(seq: int, node_hash: str, *, send_timeout_s: float,
              pull_timeout_s: float, body: Optional[str] = None) -> RoundResult:
    """One send -> store -> pull cycle, each phase in its own process."""
    body = body or f"{MARKER} seq={seq} t={int(time.time())}"
    t0 = time.monotonic()                  # F5: wall clock is forgeable here

    # A token unique to THIS fire so overlapping fires never share an identity
    # file (honest_failure_modes #8). PID is unique among live processes; seq
    # separates rounds within one fire.
    identity_path = default_identity_path(f"{os.getpid()}_{seq}")
    try:
        sent = _run_role("send", body, node_hash, send_timeout_s + 30.0,
                         identity_path)
        if not sent.get("ok"):
            return RoundResult(seq=seq, ok=False, stage=_STAGE_SEND,
                               reason=str(sent.get("reason", "unknown")))
        store_latency = sent.get("latency_s")

        pulled = _run_role("pull", body, node_hash, pull_timeout_s + 30.0,
                           identity_path)
        if not pulled.get("ok"):
            # F1: a pull the transport never completed is _STAGE_PULL_LINK
            # (indeterminate) — only a completed-sync miss is _STAGE_PULL.
            stage = (_STAGE_PULL_LINK if pulled.get("indeterminate")
                     else _STAGE_PULL)
            return RoundResult(seq=seq, ok=False, stage=stage,
                               store_latency_s=store_latency,
                               reason=str(pulled.get("reason", "unknown")))

        return RoundResult(seq=seq, ok=True,
                           store_latency_s=store_latency,
                           retrieve_latency_s=pulled.get("latency_s"),
                           total_latency_s=round(time.monotonic() - t0, 2))
    finally:
        # Throwaway identity — never leave it lying in state.
        try:
            os.remove(identity_path)
        except OSError:
            pass


def run_soak(*, rounds: int, node_hash: str,
             send_timeout_s: float, pull_timeout_s: float,
             ok_ratio_threshold: float) -> SoakReport:
    started = _now_iso()
    results: List[RoundResult] = []
    for seq in range(1, rounds + 1):
        results.append(run_round(seq, node_hash,
                                 send_timeout_s=send_timeout_s,
                                 pull_timeout_s=pull_timeout_s))
    return build_report(results, propagation_node=node_hash,
                        started_at_iso=started, finished_at_iso=_now_iso(),
                        ok_ratio_threshold=ok_ratio_threshold)


def render_text(report: SoakReport) -> str:
    lines = [
        "LXMF store-and-forward soak",
        f"  node      : {report.propagation_node}",
        f"  rounds    : {report.total_ok}/{report.total_samples} OK "
        f"(threshold {report.ok_ratio_threshold:.2f})",
    ]
    d = report.to_dict()
    if d.get("latency_s"):
        lines.append(f"  latency   : {d['latency_s']}")
    for r in report.round_results:
        if r.ok:
            lines.append(f"  round {r.seq}: OK store={r.store_latency_s}s "
                         f"retrieve={r.retrieve_latency_s}s")
        elif _is_indeterminate(r):
            lines.append(f"  round {r.seq}: INDETERMINATE at {r.stage} — "
                         f"{r.reason}")
        else:
            lines.append(f"  round {r.seq}: FAIL at {r.stage} — {r.reason}")
    if report.pass_envelope is True:
        verdict = "PASS"
    elif report.pass_envelope is False:
        verdict = "FAIL"
    else:
        verdict = "INDETERMINATE (node not exercised — sender too slow?)"
    lines.append(f"  verdict   : {verdict}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--propagation-node", default=None,
                    help="32-hex node hash (default: gateway.json)")
    ap.add_argument("--send-timeout", type=float, default=DEFAULT_SEND_TIMEOUT_S)
    ap.add_argument("--pull-timeout", type=float, default=DEFAULT_PULL_TIMEOUT_S)
    ap.add_argument("--ok-ratio-threshold", type=float,
                    default=DEFAULT_OK_RATIO_THRESHOLD)
    ap.add_argument("--output", choices=("json", "text"), default="text")
    ap.add_argument("--loglevel", default="WARNING")
    ap.add_argument("--check-configured", action="store_true",
                    help="exit 0 if a propagation node is configured, 3 if "
                         "not, 4 if configured but malformed — no RNS, no "
                         "state writes (the fire script's INERT gate)")
    # Internal: the parent re-invokes itself once per role so the sender and
    # receiver never share a process (see _run_role for why that is required).
    ap.add_argument("--role", choices=("send", "pull"), default=None,
                    help=argparse.SUPPRESS)
    ap.add_argument("--body", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--identity-path", default=None, help=argparse.SUPPRESS)
    # Internal: the child's own phase deadline (review F4 — without this the
    # operator's --send-timeout/--pull-timeout stopped at the parent).
    ap.add_argument("--phase-timeout", type=float, default=None,
                    help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.loglevel.upper(), logging.WARNING))

    node = _resolve_propagation_node(args.propagation_node)
    if not node:
        # No configured node is NOT a drill failure — it means this box has
        # nothing to exercise. Emit nothing and exit 0 (3 for the explicit
        # check) so the fire script publishes no envelope and the probe stays
        # INERT rather than reporting a store-and-forward failure that never
        # had a chance.
        print("no propagation_node configured — nothing to exercise",
              file=sys.stderr)
        return 3 if args.check_configured else 0
    if not _HASH_RE.fullmatch(node):
        # Configured but garbage (typo'd hash): a REAL operator error, never
        # silence — before this check it crashed both children on
        # bytes.fromhex and read as a perpetual indeterminate (review).
        print(f"propagation_node malformed (want 32 hex chars): {node!r}",
              file=sys.stderr)
        return 4
    if args.check_configured:
        return 0

    if args.role:
        # Child role: init RNS, do the one job, emit ONE JSON line.
        from utils.rns_init import open_reticulum
        reticulum = open_reticulum("/etc/reticulum", require_listener=True)
        if reticulum is None:
            print("RNS unavailable/degraded — not a store-and-forward verdict",
                  file=sys.stderr)
            return 3
        ipath = args.identity_path or default_identity_path("adhoc")
        if args.role == "send":
            result = _phase_send(args.body or MARKER, node, ipath,
                                 args.phase_timeout or DEFAULT_SEND_TIMEOUT_S)
        else:
            result = _phase_pull(args.body or MARKER, node, ipath,
                                 args.phase_timeout or DEFAULT_PULL_TIMEOUT_S)
        # flush=True is LOAD-BEARING. stdout is a pipe here (block-buffered),
        # and RNS/LXMF teardown can reach os._exit(), which skips the
        # interpreter's flush — the result line is then silently lost and the
        # parent sees "rc=0, no result line" against a perfectly healthy node.
        # Live-caught 2026-07-21 as an INTERMITTENT failure (some runs green,
        # some empty), which is exactly what made it look like a flaky node.
        print(json.dumps(result), flush=True)
        return 0 if result.get("ok") else 1

    # ⚠️ The ORCHESTRATING parent must NOT initialise RNS. Live-caught
    # 2026-07-21: with an RNS instance open in the parent, every child exited 0
    # having written NOTHING to stdout or stderr — silently, so the round was
    # reported as "send phase produced no result" against a healthy node. The
    # same silent-empty-child signature also explains the fire script's earlier
    # zero-byte envelopes. The parent only orchestrates; RNS belongs to the
    # children that actually speak it.

    report = run_soak(rounds=max(1, args.rounds), node_hash=node,
                      send_timeout_s=args.send_timeout,
                      pull_timeout_s=args.pull_timeout,
                      ok_ratio_threshold=args.ok_ratio_threshold)

    # flush=True for the same reason as the child result line above: the fire
    # script redirects stdout to a file and an unflushed envelope publishes as
    # zero bytes, which the probe would read as a SILENCE failure.
    if args.output == "json":
        print(json.dumps(report.to_dict(), indent=2), flush=True)
    else:
        print(render_text(report), flush=True)
    # False (real store-and-forward failure) → 1; True or None (indeterminate,
    # node not exercised) → 0. An indeterminate run is not a failure exit.
    return 1 if report.pass_envelope is False else 0


if __name__ == "__main__":
    raise SystemExit(main())

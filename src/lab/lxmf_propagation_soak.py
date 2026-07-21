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
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

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

_STAGE_SEND = "send"
_STAGE_PULL = "pull"


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
    pass_envelope: bool
    round_results: List[RoundResult] = field(default_factory=list)
    marker: str = MARKER

    @property
    def ok_ratio(self) -> float:
        if self.total_samples == 0:
            return 0.0
        return self.total_ok / self.total_samples

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


def build_report(
    round_results: List[RoundResult],
    *,
    propagation_node: str,
    started_at_iso: str,
    finished_at_iso: str,
    ok_ratio_threshold: float = DEFAULT_OK_RATIO_THRESHOLD,
) -> SoakReport:
    """Assemble the envelope. Pure — no RNS, so tests cover the verdict logic.

    ``pass_envelope`` requires at least one sample: a run that produced NO
    rounds has proven nothing, and must never read as a pass (an empty result
    set satisfying a ratio test is honest_failure_modes #1 in arithmetic form).
    """
    total = len(round_results)
    ok = sum(1 for r in round_results if r.ok)
    ratio = (ok / total) if total else 0.0
    return SoakReport(
        started_at_iso=started_at_iso,
        finished_at_iso=finished_at_iso,
        propagation_node=propagation_node,
        rounds=total,
        total_samples=total,
        total_ok=ok,
        ok_ratio_threshold=ok_ratio_threshold,
        pass_envelope=(total > 0 and ratio >= ok_ratio_threshold),
        round_results=list(round_results),
    )


def worst_round(round_results) -> Optional[str]:
    """Compact ``round N failed at <stage>: <reason>`` for the first failure.

    None when nothing failed or the input is misshaped — a summary helper must
    never raise inside a probe.
    """
    if not isinstance(round_results, list):
        return None
    for r in round_results:
        if isinstance(r, dict):
            ok, seq, stage, reason = (r.get("ok"), r.get("seq"),
                                      r.get("stage"), r.get("reason"))
        elif isinstance(r, RoundResult):
            ok, seq, stage, reason = r.ok, r.seq, r.stage, r.reason
        else:
            continue
        if ok is False:
            return f"round {seq} failed at {stage or '?'}: {reason or 'unknown'}"
    return None


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


def run_round(seq: int, node_hash: str, storage_root: str, *,
              send_timeout_s: float, pull_timeout_s: float) -> RoundResult:
    """One send -> store -> pull cycle. Returns a RoundResult, never raises."""
    import LXMF  # lazy
    import RNS   # lazy
    from lab._lab_common import load_or_create_identity

    body = f"{MARKER} seq={seq} t={int(time.time())}"
    t0 = time.time()

    try:
        ident_a, _ = load_or_create_identity("prop_soak_a")
        ident_b, _ = load_or_create_identity("prop_soak_b")
    except Exception as exc:                       # noqa: BLE001 - reported
        return RoundResult(seq=seq, ok=False, stage=_STAGE_SEND,
                           reason=f"identity error: {exc.__class__.__name__}")

    router_a = None
    router_b = None
    try:
        # --- phase 1: A sends PROPAGATED ---------------------------------
        store_a = os.path.join(storage_root, "a")
        os.makedirs(store_a, mode=0o700, exist_ok=True)
        # autopeer=False: a drill client must never peer our fleet's store
        # outward to foreign nodes.
        router_a = LXMF.LXMRouter(storagepath=store_a, autopeer=False)
        source = router_a.register_delivery_identity(ident_a,
                                                     display_name="propsoak-A")
        router_a.set_outbound_propagation_node(bytes.fromhex(node_hash))

        # Only an OUT destination here. Constructing B's IN destination would
        # REGISTER it with Transport in this process, making a local direct
        # delivery possible — which would pass the drill while proving nothing
        # about the propagation node. B must stay unreachable until phase 2.
        out_dest = RNS.Destination(ident_b, RNS.Destination.OUT,
                                   RNS.Destination.SINGLE, "lxmf", "delivery")
        msg = LXMF.LXMessage(out_dest, source, body, f"{MARKER} {seq}",
                             desired_method=LXMF.LXMessage.PROPAGATED)
        router_a.handle_outbound(msg)

        deadline = time.time() + send_timeout_s
        while time.time() < deadline:
            if msg.state in (LXMF.LXMessage.SENT, LXMF.LXMessage.DELIVERED):
                break
            if msg.state in (LXMF.LXMessage.FAILED, LXMF.LXMessage.REJECTED,
                             LXMF.LXMessage.CANCELLED):
                return RoundResult(seq=seq, ok=False, stage=_STAGE_SEND,
                                   reason=f"send state {_state_name(msg)}")
            time.sleep(2)
        else:
            return RoundResult(seq=seq, ok=False, stage=_STAGE_SEND,
                               reason=f"timeout in state {_state_name(msg)}")
        store_latency = time.time() - t0

        try:
            router_a.exit_handler()
        except Exception:                          # noqa: BLE001 - best effort
            pass
        router_a = None

        # --- phase 2: B comes up and pulls -------------------------------
        t1 = time.time()
        store_b = os.path.join(storage_root, "b")
        os.makedirs(store_b, mode=0o700, exist_ok=True)
        router_b = LXMF.LXMRouter(storagepath=store_b, autopeer=False)
        received: list = []
        router_b.register_delivery_callback(lambda m: received.append(m))
        router_b.register_delivery_identity(ident_b, display_name="propsoak-B")
        # NOT set_inbound_propagation_node() — it raises NotImplementedError
        # in lxmf 1.0.1+mf.1; one setter serves both directions.
        router_b.set_outbound_propagation_node(bytes.fromhex(node_hash))
        router_b.request_messages_from_propagation_node(ident_b, max_messages=0)

        deadline = time.time() + pull_timeout_s
        while time.time() < deadline:
            if any(_matches(m, body) for m in received):
                retrieve = time.time() - t1
                return RoundResult(
                    seq=seq, ok=True,
                    store_latency_s=round(store_latency, 2),
                    retrieve_latency_s=round(retrieve, 2),
                    total_latency_s=round(time.time() - t0, 2),
                )
            time.sleep(2)

        return RoundResult(
            seq=seq, ok=False, stage=_STAGE_PULL,
            store_latency_s=round(store_latency, 2),
            reason=(f"stored but not retrieved within {pull_timeout_s:.0f}s "
                    f"({len(received)} other message(s) returned)"),
        )
    except Exception as exc:                       # noqa: BLE001 - reported
        return RoundResult(seq=seq, ok=False, stage=_STAGE_PULL,
                           reason=f"{exc.__class__.__name__}: {exc}")
    finally:
        for r in (router_a, router_b):
            if r is not None:
                try:
                    r.exit_handler()
                except Exception:                  # noqa: BLE001 - best effort
                    pass


def _matches(message, body: str) -> bool:
    """True when a delivered LXMF message is the one this round sent."""
    try:
        content = message.content
        if isinstance(content, (bytes, bytearray)):
            content = content.decode("utf-8", errors="replace")
        return body in str(content)
    except Exception:                              # noqa: BLE001 - never raise
        return False


def run_soak(*, rounds: int, node_hash: str, storage_root: str,
             send_timeout_s: float, pull_timeout_s: float,
             ok_ratio_threshold: float) -> SoakReport:
    started = _now_iso()
    results: List[RoundResult] = []
    for seq in range(1, rounds + 1):
        results.append(run_round(seq, node_hash, storage_root,
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
        else:
            lines.append(f"  round {r.seq}: FAIL at {r.stage} — {r.reason}")
    lines.append(f"  verdict   : {'PASS' if report.pass_envelope else 'FAIL'}")
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
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.loglevel.upper(), logging.WARNING))

    node = _resolve_propagation_node(args.propagation_node)
    if not node:
        # No configured node is NOT a drill failure — it means this box has
        # nothing to exercise. Emit nothing and exit 0 so the fire script
        # publishes no envelope and the probe stays INERT rather than
        # reporting a store-and-forward failure that never had a chance.
        print("no propagation_node configured — nothing to exercise",
              file=sys.stderr)
        return 0

    import tempfile
    from utils.rns_init import open_reticulum

    reticulum = open_reticulum("/etc/reticulum", require_listener=True)
    if reticulum is None:
        print("RNS unavailable/degraded — not a store-and-forward verdict",
              file=sys.stderr)
        return 3

    with tempfile.TemporaryDirectory(prefix="propsoak-") as tmp:
        report = run_soak(rounds=max(1, args.rounds), node_hash=node,
                          storage_root=tmp,
                          send_timeout_s=args.send_timeout,
                          pull_timeout_s=args.pull_timeout,
                          ok_ratio_threshold=args.ok_ratio_threshold)

    if args.output == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_text(report))
    return 0 if report.pass_envelope else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase-1 I/O edge for the mesh oracle: the responder.

Receives an inbound mesh text and, when it is a query from an allowed sender
(and not rate-limited), reads a read-only snapshot, answers it
(``oracle.intents.answer``), sends the answer DIRECTED back to the sender, and
appends an audit record.

The responder takes ALL side-effecting dependencies as injected callables
(``snapshot_fn`` / ``send_fn`` / ``log_fn`` / ``now_fn``), so it performs no real
I/O itself and is fully unit-testable with fakes. The gateway wires the real
deps (its existing directed reply method and an append-only log).

THE INVARIANT holds: the oracle only READS state, SENDS a directed reply, and
APPENDS to its own audit log (autonomy rung 1 — report). It never controls
services, mutates config, or shells out. Default OFF — opt-in via
``MESHFORGE_ORACLE_ENABLED`` (see ``from_env``).
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, Optional, Set

from .intents import answer, is_query

_TRUE = {"1", "true", "yes", "on"}
_DEFAULT_COOLDOWN_S = 30.0
# Cap on the per-sender cooldown map: the key is the unauthenticated,
# spoofable mesh `from` id, so an attacker (or a buggy node) rotating it
# would otherwise grow this dict without bound (a witness-less RAM leak on
# 1-2GB Pis, the #73 class in memory). Once past the cap we prune entries
# already older than the cooldown — they can no longer gate any decision.
_LAST_ANSWER_CAP = 4096


def _norm(node_id) -> str:
    """Canonical node-id key for allowlist + cooldown.

    Routes through ``node_num_to_id`` (THE shared canonicalizer) so a
    numeric or numeric-string ``from`` (the #34 foreign-publisher class)
    folds to the same ``!hex`` an operator wrote in the allowlist, instead
    of a bare-number key that could never match. Non-numeric junk keeps the
    legacy casing so an already-``!hex`` id is unchanged.
    """
    try:
        from monitoring._mqtt_types import node_num_to_id
        canonical = node_num_to_id(node_id)
        if canonical is not None:
            return canonical
    except Exception:
        pass
    return "!" + str(node_id).lstrip("!").lower()


def _intent_of(text: str) -> Optional[str]:
    from .intents import _parse

    return _parse(text)[0] or None


def _facts_stale(snap) -> bool:
    return bool(getattr(snap, "wd_stale", False) or getattr(snap, "mini_stale", False))


def _send_reason(result, delivered: bool) -> Optional[str]:
    """Extract the non-delivery reason a rich send result carries, or None.

    Structural-dark row 2: the RNS leg returns an ``RnsSendResult`` (bool-
    compatible, plus ``.reason``/``.detail``); the Meshtastic leg still returns a
    plain ``bool``. Duck-typed on purpose — the oracle layer must not import the
    gateway. A plain bool yields None, so the record shape is unchanged there and
    a reason-less non-delivery still means "we genuinely cannot tell" rather than
    being silently relabelled (honest_failure_modes #1).
    """
    if delivered:
        return None
    reason = getattr(result, "reason", "") or ""
    if not isinstance(reason, str) or not reason:
        return None
    detail = getattr(result, "detail", "") or ""
    # 'send_error: <detail>' is the wire format the oracle-delivery probe's
    # classifier already keys on — keep the two in lockstep (checklist #5).
    if reason == "send_error" and detail:
        return f"send_error: {detail}"
    return reason


class MeshOracleResponder:
    """Reactive read-only query responder (the oracle's sanctioned I/O edge)."""

    def __init__(
        self,
        *,
        snapshot_fn: Callable[[], object],
        send_fn: Callable[[str, str, int], bool],
        log_fn: Optional[Callable[[dict], None]] = None,
        now_fn: Callable[[], float] = time.time,
        monotonic_fn: Optional[Callable[[], float]] = None,
        allowlist: Optional[Set[str]] = None,
        allowed_channels: Optional[Set[int]] = None,
        answer_all: bool = False,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        transport: str = "meshtastic",
        consume: bool = True,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._send_fn = send_fn
        self._log_fn = log_fn
        self._now = now_fn
        # Cooldown durations anchor on a MONOTONIC clock, never wall-clock:
        # RTC-less Pis boot on fake-hwclock and NTP steps them, and a
        # backward step made (now-last) negative → a sender stuck
        # suppressed for hours (hfm #6). Records still use wall-clock
        # (now_fn) — a timestamp is what an audit line wants. Defaults to
        # now_fn when a test injects a clock for both.
        self._mono = monotonic_fn or now_fn
        self._allowlist = {_norm(a) for a in (allowlist or set())}
        # Channel tokens are whatever the leg keys on: numeric slot INDICES on
        # the PhoneAPI/MeshCore legs, or channel NAME strings on the MQTT leg
        # (the topic name is fleet-stable, unlike the box-local index). One
        # responder instance == one leg, so a set never mixes the two.
        self._allowed_channels = set(allowed_channels or ())
        self._answer_all = answer_all
        self._cooldown_s = max(0.0, cooldown_s)
        self._transport = transport
        # consume=True (default): a handled query is taken off the wire (the
        # caller returns, not bridging it onward) — the per-mesh-local rail.
        # consume=False (bridge-through): the oracle still answers, but the
        # caller lets the command continue to bridge to the other mesh, so a
        # NOC on the far side sees the activity. Safe because the bridge's own
        # loop-guard skips re-answering already-bridged (tagged) text.
        self.consume = bool(consume)
        self._last_answer: Dict[str, float] = {}

    def _allowed(self, node_key: str, channel: Optional[int] = None) -> bool:
        """Additive access: answer-all OR known node OR on an allowed channel.

        A sender is allowed if (a) the leg answers everyone, (b) their node-id is
        on the per-node allowlist, or (c) the inbound ``channel`` is on the
        channel allowlist — so any node configured for a whitelisted channel can
        use the oracle without being listed individually. Fail-closed is
        preserved: with answer_all off and BOTH sets empty, nobody is allowed
        (an empty channel set can never match, so RNS ``channel=0`` cannot leak
        through).
        """
        if self._answer_all or node_key in self._allowlist:
            return True
        return channel is not None and channel in self._allowed_channels

    def handle(self, from_id: str, text: str, channel: int = 0) -> Optional[str]:
        """Answer a query directed back to ``from_id``; return the reply or None.

        Returns the reply string when the query was TAKEN (so the caller can
        treat it as consumed and not bridge it onward), even if delivery failed.
        Returns None when the text is not a query, the sender is not allowed, or
        the sender is within the cooldown window — leaving normal handling to
        proceed.
        """
        if not is_query(text):
            return None
        node = _norm(from_id)
        if not self._allowed(node, channel):
            self._record(from_id, text, intent=None, reply=None,
                         delivered=False, reason="not_allowlisted")
            return None
        mono = self._mono()
        last = self._last_answer.get(node)
        if last is not None and 0.0 <= (mono - last) < self._cooldown_s:
            # 0.0 <= delta guards a backward clock step: a negative delta is
            # a step, not a fresh answer — treat it as expired, don't strand
            # the sender.
            self._record(from_id, text, intent=None, reply=None,
                         delivered=False, reason="cooldown")
            return None

        snap = self._snapshot_fn()
        reply = answer(text, snap)
        # Rate-limit on ATTEMPT (airtime is spent whether or not it lands).
        self._last_answer[node] = mono
        self._prune_cooldowns(mono)
        try:
            result = self._send_fn(reply, from_id, channel)
            delivered = bool(result)
        except Exception as exc:  # a send must never raise into the bridge
            self._record(from_id, text, intent=_intent_of(text), reply=reply,
                         delivered=False, reason=f"send_error: {exc}",
                         facts_stale=_facts_stale(snap))
            return reply
        self._record(from_id, text, intent=_intent_of(text), reply=reply,
                     delivered=delivered, reason=_send_reason(result, delivered),
                     facts_stale=_facts_stale(snap))
        return reply

    def _prune_cooldowns(self, mono: float) -> None:
        """Bound the cooldown map (spoofable-key RAM-leak guard). Only runs
        past the cap; drops entries already older than the cooldown, which
        can no longer gate any decision."""
        if len(self._last_answer) <= _LAST_ANSWER_CAP:
            return
        cutoff = mono - self._cooldown_s
        self._last_answer = {k: v for k, v in self._last_answer.items()
                             if v > cutoff}

    def _record(self, from_id, text, *, intent, reply, delivered,
                reason=None, facts_stale=None) -> None:
        """Append one audit record (best-effort; never breaks answering)."""
        if self._log_fn is None:
            return
        rec = {
            "ts": self._now(),
            "transport": self._transport,
            "from": from_id,
            "query": (text or "")[:80],
            "intent": intent,
            "answer": reply,
            "delivered": delivered,
        }
        if reason is not None:
            rec["reason"] = reason
        if facts_stale is not None:
            rec["facts_stale"] = facts_stale
        try:
            self._log_fn(rec)
        except Exception:
            pass  # audit log is best-effort; a swallow here is acceptable

    @classmethod
    def from_env(
        cls,
        *,
        snapshot_fn,
        send_fn,
        log_fn=None,
        env=None,
        transport: str = "meshtastic",
        allowlist_env: str = "MESHFORGE_ORACLE_ALLOWLIST",
        allowed_channels: Optional[Set[int]] = None,
        consume_env: str = "MESHFORGE_ORACLE_CONSUME",
    ) -> Optional["MeshOracleResponder"]:
        """Build from ``MESHFORGE_ORACLE_*`` env, or ``None`` if disabled (default).

        - ``MESHFORGE_ORACLE_ENABLED``: 1/true/yes/on to enable (else ``None``);
          shared across legs.
        - ``allowlist_env`` (default ``MESHFORGE_ORACLE_ALLOWLIST``; the RNS leg
          passes ``MESHFORGE_ORACLE_RNS_ALLOWLIST``): comma sender-ids, or ``"*"``
          to answer all. **Fail-closed**: enabled with an EMPTY allowlist (and no
          ``allowed_channels``) answers no one — so a leg is effectively off until
          its allowlist or channel set is configured.
        - ``allowed_channels``: a set of channel tokens a node may be on to be
          answered (additive with the node allowlist). The token type is the
          leg's choice — numeric slot INDICES on the PhoneAPI/MeshCore legs
          (resolved by the caller, which the responder cannot do — it can't query
          the radio), or channel NAME strings on the MQTT leg (the topic name is
          fleet-stable). ``handle`` must be passed the matching token type.
        - ``MESHFORGE_ORACLE_COOLDOWN_S``: per-sender min seconds (default 30).
        - ``consume_env`` (default ``MESHFORGE_ORACLE_CONSUME``): the env var that
          governs consume for THIS leg. 1/true (default) = a handled query is
          consumed (not bridged onward, per-mesh-local). 0/false = bridge-through:
          the oracle answers AND the command still bridges to the other mesh.
          The leg name is a parameter because moc3 is a BIDIRECTIONAL gateway
          (Meshtastic↔RNS): the inbound-Mesh legs read ``MESHFORGE_ORACLE_CONSUME``
          (so meshforge commands can bridge to meshanchor), but the RNS→Mesh leg
          reads its OWN ``MESHFORGE_ORACLE_RNS_CONSUME`` (default consume) so a
          direct LXMF oracle query is never broadcast onto the Meshtastic RF
          channel — bridge-through is enabled per-direction, at no RF-airtime cost.
        """
        env = os.environ if env is None else env
        if str(env.get("MESHFORGE_ORACLE_ENABLED", "")).strip().lower() not in _TRUE:
            return None
        consume = str(env.get(consume_env, "1")).strip().lower() in _TRUE
        raw = str(env.get(allowlist_env, "")).strip()
        tokens = {tok.strip() for tok in raw.split(",") if tok.strip()}
        # '*' ANYWHERE in the list means answer-all — the old `raw == "*"`
        # silently turned '*,!abc' into a dead literal '!*' node key, losing
        # the wildcard intent with no warning.
        answer_all = "*" in tokens
        allowlist = set() if answer_all else tokens
        try:
            cooldown = float(env.get("MESHFORGE_ORACLE_COOLDOWN_S", "") or _DEFAULT_COOLDOWN_S)
        except (TypeError, ValueError):
            cooldown = _DEFAULT_COOLDOWN_S
        return cls(
            snapshot_fn=snapshot_fn,
            send_fn=send_fn,
            log_fn=log_fn,
            monotonic_fn=time.monotonic,
            allowlist=allowlist,
            allowed_channels=allowed_channels,
            answer_all=answer_all,
            cooldown_s=cooldown,
            transport=transport,
            consume=consume,
        )

"""
Message routing and classification for the gateway bridge.

Supports 3-way routing between Meshtastic, MeshCore, and RNS networks.
Extracted from rns_bridge.py to keep file sizes manageable.
Handles routing rule compilation, confidence-scored classification,
and legacy regex-based routing logic.
"""

import re
import logging
from typing import List, Optional, Dict, Any

from utils.safe_import import safe_import

from .bridge_health import MessageOrigin
from .routing_vocab import DEFAULT_ROUTE_OPEN, DEFAULT_ROUTE_SOURCE

# Import routing classifier with confidence scoring
(_RoutingClassifier, _RoutingCategory, _create_routing_system,
 _ClassificationResult, CLASSIFIER_AVAILABLE) = safe_import(
    'utils.classifier',
    'RoutingClassifier', 'RoutingCategory',
    'create_routing_system', 'ClassificationResult',
)

if CLASSIFIER_AVAILABLE:
    RoutingClassifier = _RoutingClassifier
    RoutingCategory = _RoutingCategory
    create_routing_system = _create_routing_system
    ClassificationResult = _ClassificationResult

# Import centralized path utility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class MessageRouter:
    """
    Routes messages between Meshtastic, MeshCore, and RNS based on rules.

    Supports three protocols with configurable routing directions:
    - bidirectional: Route between any two networks
    - mesh_to_rns, rns_to_mesh: Meshtastic ↔ RNS
    - mesh_to_meshcore, meshcore_to_mesh: Meshtastic ↔ MeshCore
    - rns_to_meshcore, meshcore_to_rns: RNS ↔ MeshCore
    - all_to_all: Route to all other networks

    Two classification modes:
    1. Confidence-scored classifier (when utils.classifier is available)
    2. Legacy regex-based routing rules (fallback)
    """

    # Direction mapping: source_network → allowed destination networks
    _DIRECTION_MAP = {
        'bidirectional': None,  # Any direction
        'mesh_to_rns': ('meshtastic', 'rns'),
        'rns_to_mesh': ('rns', 'meshtastic'),
        'mesh_to_meshcore': ('meshtastic', 'meshcore'),
        'meshcore_to_mesh': ('meshcore', 'meshtastic'),
        'rns_to_meshcore': ('rns', 'meshcore'),
        'meshcore_to_rns': ('meshcore', 'rns'),
        'all_to_all': None,  # Any direction
        # A refusing rule applies to any source; its FILTERS pick the
        # messages it refuses (2026-10-06 — before this entry the legacy
        # decider read 'drop' as an unknown direction and ALLOWED the match).
        'drop': None,
    }

    #: ``default_route`` values under which a message no rule matched is
    #: bridged. A direction name (``mesh_to_rns``) opens that SOURCE only;
    #: anything else makes rules an allow-list. Both deciders read this
    #: through ``_default_open()`` (MeshAnchor #151).
    _OPEN_DEFAULT_ROUTES = DEFAULT_ROUTE_OPEN

    # Maximum input length for regex matching to bound execution time
    _REGEX_INPUT_LIMIT = 512

    def __init__(self, config, stats: dict, stats_lock):
        """
        Args:
            config: GatewayConfig instance with routing_rules and settings.
            stats: Shared stats dict (bridge owns it, router updates 'bounced').
            stats_lock: Threading lock for stats updates.
        """
        self.config = config
        self.stats = stats
        self._stats_lock = stats_lock

        # Pre-compile routing rule regexes to avoid re-compilation per message
        # and catch invalid patterns at startup rather than at runtime
        self._compiled_rules = self._compile_routing_rules()

        # Routing classifier with confidence scoring
        self._classifier = None
        self._last_classification: Optional[ClassificationResult] = None
        if CLASSIFIER_AVAILABLE:
            fixes_path = get_real_user_home() / '.config' / 'meshforge' / 'routing_fixes.json'
            rules = [
                {
                    'name': rule.name,
                    'enabled': rule.enabled,
                    'direction': rule.direction,
                    'source_filter': rule.source_filter,
                    'dest_filter': rule.dest_filter,
                    'message_filter': rule.message_filter,
                    'priority': rule.priority
                }
                for rule in self.config.routing_rules
            ]
            self._classifier = create_routing_system(
                rules=rules,
                bounce_threshold=0.3,
                fixes_path=fixes_path,
                # One default_route contract for both deciders (MeshAnchor
                # #151): closed makes the rules an allow-list here too.
                # A callable, read per message: a snapshot let the
                # classifier bridge while legacy refused (review F2).
                default_open=self._default_open,
            )
            logger.info("Routing classifier initialized with confidence scoring")
        # Say the EFFECTIVE policy, once, where the operator reads (the
        # journal). Measured 2026-10-06 on both live gateways: zero rules +
        # bidirectional + classifier = every sender on every network bridged,
        # and the line above was the only thing the gateway said about it.
        # Disabled = refuses everything: WARNING, so it is not mistaken for
        # a quiet healthy gateway (MeshAnchor #151 item 2).
        # Bridges nothing (closed, zero allow rules): WARNING too — a typo'd
        # default_route turns an open gateway closed (review F1).
        (logger.warning if (not self.config.enabled or self._bridges_nothing())
         else logger.info)(self.describe_ingress_policy())
        self._disabled_refusal_seen = False
        self._no_rule_refusal_seen = False

    def _default_open(self):
        """``default_route`` as the deciders read it: True (open), False
        (allow-list), or a frozenset of source networks open by default —
        a direction name opens its source, as a rule's direction does."""
        route = self.config.default_route
        if route in self._OPEN_DEFAULT_ROUTES:
            return True
        if route in DEFAULT_ROUTE_SOURCE:
            return frozenset({DEFAULT_ROUTE_SOURCE[route]})
        return False

    def _bridges_nothing(self) -> bool:
        """Closed default_route and no enabled allow rule: every message on
        every network is refused."""
        return (self._default_open() is False and not any(
            r.enabled and r.direction != "drop"
            for r in self.config.routing_rules))

    def _default_bridges(self, source_network: str) -> bool:
        d = self._default_open()
        return d if isinstance(d, bool) else source_network in d

    def _note_refused_no_rule(self, msg) -> None:
        """Witness for an allow-list refusal: a counter always, INFO once
        (the reason an operator needs), DEBUG after (honest_failure_modes #9).
        """
        with self._stats_lock:
            self.stats['routing_refused_no_rule'] = (
                self.stats.get('routing_refused_no_rule', 0) + 1)
        source_id = (getattr(msg, 'source_address', '')
                     or getattr(msg, 'source_id', '') or '?')
        text = (f"Routing: refused {getattr(msg, 'source_network', '?')} "
                f"message from {source_id[:16]} - no enabled rule matched and "
                f"default_route={self.config.default_route} makes the rules an "
                f"allow-list")
        if not self._no_rule_refusal_seen:
            self._no_rule_refusal_seen = True
            logger.info(text + "; further refusals at DEBUG")
        else:
            logger.debug(text)

    def _rns_allowlist(self):
        """``(ids, policy)`` when an RNS→RF list is declared, else ``None``.
        Defensive: a mocked/absent section reads as no list."""
        try:
            from .rns_ingress_policy import effective_policy
            rns_cfg = getattr(self.config, "rns", None)
            ids = list(rns_cfg.get_bridge_source_identities())
            declared = getattr(rns_cfg, "bridge_source_policy", None)
            pol = effective_policy(ids, declared if isinstance(declared, str)
                                   else None)
        except Exception:  # noqa: BLE001
            return None
        return (ids, pol) if ids else None

    def _rns_allowlist_clause(self) -> str:
        """`` rns_allowlist=<n> policy=<observe|enforce>`` when a list is
        declared, ``""`` when the RNS→RF path is open."""
        al = self._rns_allowlist()
        if al is None:
            return ""
        ids, pol = al
        return f" rns_allowlist={len(ids)} policy={pol}{self._identity_clause(ids)}"

    def _peer_gateways(self) -> int:
        """How many peer gateways the bridge admits by VERDICT_PEER — same
        normalisation as ``_peer_gateway_hash_set`` (32-hex, lowercase)."""
        try:
            raw = self.config.rns.get_peer_gateway_destinations()
        except Exception:  # noqa: BLE001
            return 0
        if not isinstance(raw, (list, tuple)):
            return 0
        return len({h.lower() for h in raw
                    if isinstance(h, str) and len(h) == 32})

    def _rns_to_rf_words(self, *, open_verdict: bool) -> str:
        """What the sentence may say about RNS→RF. The allowlist is a gate in
        the bridge, not a routing rule, so the routing verdict alone cannot
        describe it (2026-10-08: moc read ``policy=enforce`` and "OPEN …
        (RNS→RF included)" in one line). ``open_verdict``: the words follow
        "bridged onward"; otherwise they are a trailing clause ("" = no list)
        that the routing rules must ALSO pass — the gate runs first, so the
        two compose as AND."""
        al = self._rns_allowlist()
        if al is None:
            return "(RNS→RF included)" if open_verdict else ""
        ids, pol = al
        n = len(ids)
        if pol == "enforce":
            p = self._peer_gateways()
            # A peer relays its whole RF segment as one identity (review
            # 2026-10-08), so naming the count alone would hide the surface.
            peers = (f" or {p} peer gateway(s) (each relays its whole mesh "
                     f"segment)" if p else "")
            who = f"the {n} listed identities{peers}, signature-verified (enforce)"
            return (f"except RNS→RF, which admits only {who}" if open_verdict
                    else f"; RNS→RF additionally requires one of {who}")
        logs = "only logs strangers, policy=" + pol
        return (f"(RNS→RF included — the {n}-identity list {logs})"
                if open_verdict else f"; the {n}-identity RNS→RF list {logs}")

    @staticmethod
    def _identity_clause(ids) -> str:
        """`` named=<n>/<m> unnamed=<h8,…> registry=<state>`` — every listed
        identity the registry cannot name is printed, so "name their
        purpose" (operator 2026-10-07) has a visible worklist. Never raises."""
        try:
            from .lxmf_identity_registry import load_registry
            r = load_registry()
            un = r.unnamed(ids)
            clause = f" named={len(ids) - len(un)}/{len(ids)}"
            if un:
                clause += " unnamed=" + ",".join(h[:8] for h in un)
            clause += f" registry={r.state}"
            if r.problems:
                clause += f" registry_problems={len(r.problems)}"
            return clause
        except Exception as e:  # noqa: BLE001
            return f" registry=error({e})"

    def describe_ingress_policy(self) -> str:
        """One sentence naming who this gateway bridges, derived from the
        config and the decider actually in use — never from a hope.

        Two deciders exist and they read the same ``routing_rules``
        differently; the sentence says which one is live:

        * both — a matching ``drop`` rule refuses. With ``default_route``
          bidirectional/all_to_all a message no rule matches is bridged;
          any other value makes the enabled rules an allow-list. (Until
          MeshAnchor #151 the classifier ignored ``default_route``.)
        * classifier, open default — ALLOW rules only add confidence, so
          they restrict nothing; the line says so.
        """
        if not self.config.enabled:
            return ('Routing ingress policy: CLOSED - gateway.json top-level '
                    '"enabled" is false, so every message on every network is '
                    'refused (the Meshtastic/RNS bridge threads do not start; '
                    'a MeshCore handler still receives and refuses); set '
                    '"enabled": true to bridge')
        rules = [r for r in self.config.routing_rules if r.enabled]
        drop = [r for r in rules if r.direction == "drop"]
        allow = [r for r in rules if r.direction != "drop"]
        decider = "classifier" if self._classifier is not None else "legacy"
        head = (f"Routing ingress policy: decider={decider} rules={len(rules)} "
                f"default_route={self.config.default_route}"
                f"{self._rns_allowlist_clause()} — ")
        rns_open = self._rns_to_rf_words(open_verdict=True)
        rns_tail = self._rns_to_rf_words(open_verdict=False)
        knob = ("a rule with direction 'drop' (source_filter/dest_filter/"
                "message_filter pick what it refuses) is the knob that "
                "closes a path")
        if (decider == "classifier"
                and self.config.default_route in self._OPEN_DEFAULT_ROUTES):
            if drop:
                return (head + f"every source network is bridged except what "
                        f"{len(drop)} drop rule(s) refuse "
                        f"({', '.join(r.name for r in drop)}); "
                        f"{len(allow)} allow rule(s) and default_route do not "
                        f"restrict on this decider{rns_tail}")
            return (head + "OPEN on every source network: any sender whose "
                    "message reaches this gateway is bridged onward "
                    f"{rns_open}; " + (f"{len(allow)} allow rule(s) and "
                    "default_route do not restrict on this decider; "
                    if allow else "") + knob)
        if self.config.default_route in self._OPEN_DEFAULT_ROUTES:
            if not drop:
                return (head + f"OPEN: any sender is bridged {rns_open}; "
                        + knob)
            # The drop clause qualifies the OPEN verdict, never the allowlist
            # (review 2026-10-08): state it first, the RNS gate after.
            return (head + f"OPEN: any sender is bridged unless one of "
                    f"{len(drop)} drop rule(s) matches"
                    + (rns_tail or f" {rns_open}") + "; " + knob)
        if self._bridges_nothing():
            return (head + f"ALLOW-LISTED with 0 enabled allow rules: this "
                    f"gateway bridges NOTHING (default_route="
                    f"{self.config.default_route!r} is not open). Set "
                    f"default_route to 'bidirectional', or add allow rules"
                    + rns_tail)
        d_open = self._default_open()
        also = (f" plus every {'/'.join(sorted(d_open))}-sourced message "
                f"(default_route={self.config.default_route} names that source)"
                if isinstance(d_open, frozenset) else "")
        return (head + f"ALLOW-LISTED: only messages matching "
                f"{len(allow)} enabled allow rule(s) are bridged" + also
                + (f", minus {len(drop)} drop rule(s)" if drop else "")
                + "; a sender no rule names is refused" + rns_tail)

    def should_bridge(self, msg) -> bool:
        """
        Check if message should be bridged based on routing rules.

        Uses confidence-scored classifier when available:
        - High confidence (>0.7): Route immediately
        - Low confidence (<0.3): Bounce to queue for review
        - Medium confidence: Route with logging
        """
        if not self.config.enabled:
            # First refusal at INFO, the rest at DEBUG: the handlers word
            # this drop "blocked by routing rules" (#151 item 3), so the
            # router names the real cause once where an operator reads.
            if not getattr(self, '_disabled_refusal_seen', False):
                self._disabled_refusal_seen = True
                logger.info('Routing: refusing every message - gateway.json '
                            'top-level "enabled" is false (set "enabled": '
                            'true to bridge); further refusals at DEBUG')
            else:
                logger.debug("Routing: bridge disabled, dropping message")
            return False

        # Use classifier if available
        if self._classifier:
            return self._classify_message(msg)

        # Fallback to legacy logic
        return self._should_bridge_legacy(msg)

    def _classify_message(self, msg) -> bool:
        """Classify message using confidence-scored routing."""
        # Support both CanonicalMessage (source_address) and BridgedMessage (source_id)
        source_id = getattr(msg, 'source_address', '') or getattr(msg, 'source_id', '')
        dest_id = getattr(msg, 'destination_address', '') or getattr(msg, 'destination_id', '')
        msg_id = f"{msg.source_network}:{source_id}:{msg.timestamp.isoformat()}"

        result = self._classifier.classify(msg_id, {
            'source_network': msg.source_network,
            'source_id': source_id,
            'destination_id': dest_id,
            'content': msg.content,
            'is_broadcast': msg.is_broadcast,
            'metadata': msg.metadata
        })

        self._last_classification = result

        # Handle bounced messages
        if result.bounced:
            if result.metadata.get('refused_no_rule'):
                self._note_refused_no_rule(msg)   # review F4
            with self._stats_lock:
                self.stats['bounced'] += 1
            logger.info(
                f"Message bounced (confidence {result.confidence:.2f}): "
                f"{source_id[:8]}... -> {result.bounce_reason}"
            )
            # Bounced = low confidence: held in the bouncer's review queue,
            # never bridged immediately. (Was `category == QUEUE` — always
            # False today since the classifier never emits QUEUE with
            # action="queue", but if that ever changed it would have bridged
            # exactly the messages held for review — the inverse of intent.)
            return False

        # Log classification decision
        if result.confidence < 0.7:
            logger.debug(
                f"Routing decision (confidence {result.confidence:.2f}): "
                f"{result.category} - {result.reason}"
            )

        # Determine if we should bridge based on category
        if result.category == RoutingCategory.DROP.value:
            if result.metadata.get('refused_no_rule'):
                self._note_refused_no_rule(msg)
            return False
        elif result.category in (
            RoutingCategory.BRIDGE_RNS.value,
            RoutingCategory.BRIDGE_MESH.value,
            RoutingCategory.BRIDGE_MESHCORE.value,
        ):
            return True
        elif result.category == RoutingCategory.QUEUE.value:
            # Queued items need manual review
            return False

        return False

    def _compile_routing_rules(self) -> dict:
        """Pre-compile regex patterns from routing rules at init time.

        Returns a dict mapping rule name to compiled filter patterns.
        Invalid patterns are logged and skipped — the rule will never match.
        """
        compiled = {}
        for rule in self.config.routing_rules:
            filters = {}
            for field in ('source_filter', 'dest_filter', 'message_filter'):
                pattern = getattr(rule, field, '')
                if pattern:
                    try:
                        filters[field] = re.compile(pattern)
                    except re.error as e:
                        logger.warning(
                            f"Invalid regex in rule '{rule.name}' "
                            f"field '{field}': {e} — rule will be skipped"
                        )
                        filters[field] = None  # Mark as broken
            compiled[rule.name] = filters
        return compiled

    def _should_bridge_legacy(self, msg) -> bool:
        """Legacy routing logic (fallback when classifier unavailable)."""
        # Re-compile if routing rules changed since last compile
        current_names = {r.name for r in self.config.routing_rules}
        if current_names != set(self._compiled_rules.keys()):
            self._compiled_rules = self._compile_routing_rules()

        # Determine source network (handle both BridgedMessage and CanonicalMessage)
        source = getattr(msg, 'source_network', '')

        # A refusing rule wins regardless of config order (non-author review
        # 2026-10-07: an allow rule listed first returned True before the
        # drop rule was ever consulted). Walk drop rules first.
        ordered = ([r for r in self.config.routing_rules if r.direction == 'drop']
                   + [r for r in self.config.routing_rules if r.direction != 'drop'])
        for rule in ordered:
            if not rule.enabled:
                continue

            # Check direction against source network
            if not self._direction_allows(rule.direction, source):
                continue

            # Get pre-compiled filters for this rule
            filters = self._compiled_rules.get(rule.name, {})

            # Skip rule entirely if any of its patterns failed to compile
            if any(v is None for v in filters.values()):
                continue

            # Apply pre-compiled regex filters with bounded input
            # Source filter
            source_id = getattr(msg, 'source_id', '') or getattr(msg, 'source_address', '')
            if rule.source_filter:
                compiled = filters.get('source_filter')
                if not compiled or not source_id:
                    continue
                if not compiled.search(source_id[:self._REGEX_INPUT_LIMIT]):
                    continue

            # Destination filter
            if rule.dest_filter:
                compiled = filters.get('dest_filter')
                if not compiled:
                    continue
                dest_id = getattr(msg, 'destination_id', '') or getattr(msg, 'destination_address', '')
                dest = (dest_id or "")[:self._REGEX_INPUT_LIMIT]
                if not compiled.search(dest):
                    continue

            # Message content filter
            if rule.message_filter:
                compiled = filters.get('message_filter')
                if not compiled or not msg.content:
                    continue
                if not compiled.search(msg.content[:self._REGEX_INPUT_LIMIT]):
                    continue

            # All filters passed - this rule matches
            source_id = getattr(msg, 'source_id', '') or getattr(msg, 'source_address', '')
            if rule.direction == 'drop':
                logger.info(
                    "Routing legacy: drop rule '%s' refused %s from %s",
                    rule.name, source_id[:16], source
                )
                return False
            logger.debug(
                "Routing legacy: rule '%s' matched for %s from %s",
                rule.name, source_id[:16], source
            )
            return True

        default_bridge = self._default_bridges(source)
        logger.debug(
            "Routing legacy: no rule matched, default_route=%s -> bridge=%s",
            self.config.default_route, default_bridge
        )
        if not default_bridge:
            self._note_refused_no_rule(msg)
        return default_bridge

    def _direction_allows(self, direction: str, source_network: str) -> bool:
        """
        Check if a routing direction allows messages from the given source.

        Args:
            direction: Routing rule direction string
            source_network: Source network of the message

        Returns:
            True if the direction allows this source network.
        """
        if direction in ('bidirectional', 'all_to_all'):
            return True

        mapping = self._DIRECTION_MAP.get(direction)
        if mapping is None:
            return True  # Unknown direction = allow

        required_source, _ = mapping
        return source_network == required_source

    def get_destination_networks(self, msg) -> List[str]:
        """
        Determine which destination networks a message should be routed to.

        For broadcast messages, returns all networks except the source.
        For directed messages, returns based on routing rules and direction.

        Filters out MeshCore when message originated via internet (MQTT).

        Args:
            msg: BridgedMessage or CanonicalMessage

        Returns:
            List of destination network names.
        """
        source = getattr(msg, 'source_network', '')
        all_networks = ['meshtastic', 'meshcore', 'rns']
        destinations = []

        # Check internet origin filtering for MeshCore
        via_internet = getattr(msg, 'via_internet', False)
        origin = getattr(msg, 'origin', None)
        internet_origin = via_internet or (origin == MessageOrigin.MQTT)

        for dest in all_networks:
            if dest == source:
                continue

            # MeshCore is pure radio — don't bridge internet traffic to it
            if dest == 'meshcore' and internet_origin:
                continue

            # Check if any routing rule allows this direction
            if self._has_matching_rule(source, dest):
                destinations.append(dest)
            elif self._default_bridges(source):
                destinations.append(dest)

        return destinations

    def _has_matching_rule(self, source: str, dest: str) -> bool:
        """Check if any enabled routing rule matches source→dest direction."""
        # Map network names to direction components
        name_map = {'meshtastic': 'mesh', 'meshcore': 'meshcore', 'rns': 'rns'}
        source_key = name_map.get(source, source)
        dest_key = name_map.get(dest, dest)

        for rule in self.config.routing_rules:
            if not rule.enabled:
                continue
            if rule.direction in ('bidirectional', 'all_to_all'):
                return True
            direction = f"{source_key}_to_{dest_key}"
            if rule.direction == direction:
                return True
        return False

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing classifier statistics."""
        stats = dict(self.stats)
        if self._classifier:
            classifier_stats = self._classifier.get_stats()
            stats['classifier'] = classifier_stats
            stats['bouncer_queue'] = len(self._classifier.bouncer.get_queue())
        return stats

    def get_last_classification(self) -> Optional[Dict]:
        """Get the last classification result for debugging."""
        if self._last_classification:
            return self._last_classification.to_dict()
        return None

    def fix_routing(self, msg_id: str, correct_category: str) -> bool:
        """
        Record a user correction for routing decisions.

        This is the 'fix button' - allows users to correct mistakes
        and improve the system over time.
        """
        if not self._classifier or not self._classifier.fix_registry:
            return False

        # Create a dummy result for the fix
        result = ClassificationResult(
            input_id=msg_id,
            category="unknown",
            confidence=0.5
        )
        self._classifier.fix_registry.add_fix(result, correct_category)
        logger.info(f"Routing fix recorded: {msg_id} -> {correct_category}")
        return True

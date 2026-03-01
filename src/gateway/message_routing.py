"""
Message routing and classification for the gateway bridge.

Supports 3-way routing between Meshtastic, MeshCore, and RNS networks.
Extracted from rns_bridge.py to keep file sizes manageable.
Handles routing rule compilation, confidence-scored classification,
and legacy regex-based routing logic.
"""

import re
import logging
import time
from typing import List, Optional, Dict, Any

from utils.safe_import import safe_import

from .bridge_health import MessageOrigin

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
    }

    # Maximum input length for regex matching to bound execution time
    _REGEX_INPUT_LIMIT = 512

    def __init__(self, config, stats: dict, stats_lock, aredn_overlay=None):
        """
        Args:
            config: GatewayConfig instance with routing_rules and settings.
            stats: Shared stats dict (bridge owns it, router updates 'bounced').
            stats_lock: Threading lock for stats updates.
            aredn_overlay: Optional AREDNTopologyOverlay for backhaul-aware routing.
        """
        self.config = config
        self.stats = stats
        self._stats_lock = stats_lock
        self._aredn_overlay = aredn_overlay

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
                fixes_path=fixes_path
            )
            logger.info("Routing classifier initialized with confidence scoring")

    def should_bridge(self, msg) -> bool:
        """
        Check if message should be bridged based on routing rules.

        Uses confidence-scored classifier when available:
        - High confidence (>0.7): Route immediately
        - Low confidence (<0.3): Bounce to queue for review
        - Medium confidence: Route with logging
        """
        if not self.config.enabled:
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
            with self._stats_lock:
                self.stats['bounced'] += 1
            logger.info(
                f"Message bounced (confidence {result.confidence:.2f}): "
                f"{source_id[:8]}... -> {result.bounce_reason}"
            )
            # Bounced messages go to queue category, don't bridge immediately
            return result.category == RoutingCategory.QUEUE.value

        # Log classification decision
        if result.confidence < 0.7:
            logger.debug(
                f"Routing decision (confidence {result.confidence:.2f}): "
                f"{result.category} - {result.reason}"
            )

        # Determine if we should bridge based on category
        if result.category == RoutingCategory.DROP.value:
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

        for rule in self.config.routing_rules:
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
            return True

        return self.config.default_route in ("bidirectional", "all_to_all")

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
        Annotates AREDN backhaul hints on the message when overlay is available.

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
            direction_key = f"{source}_to_{dest}"
            if self._has_matching_rule(source, dest):
                destinations.append(dest)
            elif self.config.default_route in ('bidirectional', 'all_to_all'):
                destinations.append(dest)

        # Annotate AREDN backhaul hints when overlay is available
        if self._aredn_overlay and destinations:
            self._annotate_aredn_hints(msg, destinations)

        return destinations

    def _annotate_aredn_hints(self, msg, destinations: List[str]) -> None:
        """Annotate message metadata with AREDN backhaul routing hints.

        When AREDN backhaul is available with quality > 50%, adds a routing
        hint to the message metadata suggesting the AREDN path for long-haul
        delivery (higher bandwidth than LoRa).

        Args:
            msg: Message being routed (metadata dict will be updated).
            destinations: List of destination networks.
        """
        dest_id = (getattr(msg, 'destination_address', '') or
                   getattr(msg, 'destination_id', ''))
        if not dest_id:
            return

        try:
            hint = self._aredn_overlay.get_routing_hint(dest_id)
        except Exception as e:
            logger.debug(f"AREDN routing hint lookup failed: {e}")
            return

        if hint.get("available") and hint.get("quality", 0) > 0.5:
            # Annotate message metadata with AREDN preference
            metadata = getattr(msg, 'metadata', None)
            if metadata is None:
                return
            if isinstance(metadata, dict):
                metadata['aredn_hint'] = {
                    'preferred': True,
                    'quality': hint['quality'],
                    'router_ip': hint['router_ip'],
                    'link_type': hint['link_type'],
                    'gateway_name': hint.get('gateway_name', ''),
                }
                logger.debug(
                    f"AREDN routing hint: {dest_id[:8]}... reachable via "
                    f"{hint['router_ip']} (quality={hint['quality']:.0%}, "
                    f"type={hint['link_type']})"
                )

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

    def get_aredn_routing_info(self) -> Dict[str, Any]:
        """Get AREDN routing status for diagnostic display.

        Returns:
            Dict with overlay status and gateway routing hints.
        """
        if not self._aredn_overlay:
            return {"available": False, "reason": "No AREDN overlay configured"}

        try:
            hints = self._aredn_overlay.get_all_routing_hints()
            connected = self._aredn_overlay.is_connected
            return {
                "available": True,
                "connected": connected,
                "gateways": hints,
                "gateway_count": len(hints),
            }
        except Exception as e:
            return {"available": True, "connected": False, "error": str(e)}

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

    def drain_bounced(self, max_items: int = 10) -> List[Dict[str, Any]]:
        """Drain bounced messages from the classifier's bouncer queue.

        Returns items with enough metadata for the bridge loop to persist
        them to the SQLite queue or discard. Items are cleared from the
        bouncer after retrieval.

        Args:
            max_items: Maximum number of items to drain per call.

        Returns:
            List of dicts with keys: input_id, category, confidence,
            reason, metadata, bounced.
        """
        if not self._classifier or not hasattr(self._classifier, 'bouncer'):
            return []

        queue = self._classifier.bouncer.get_queue()
        if not queue:
            return []

        items = queue[:max_items]
        results = []
        for item in items:
            results.append({
                'input_id': getattr(item, 'input_id', ''),
                'category': getattr(item, 'category', 'unknown'),
                'confidence': getattr(item, 'confidence', 0.0),
                'reason': getattr(item, 'bounce_reason', ''),
                'metadata': getattr(item, 'metadata', None) or {},
                'bounced': True,
            })

        # Clear drained items — if we took all, just clear; otherwise rebuild
        if len(items) >= len(queue):
            self._classifier.bouncer.clear_queue()
        else:
            self._classifier.bouncer._queue = queue[max_items:]

        logger.debug(f"Drained {len(results)} bounced messages from queue")
        return results

    def clear_expired_bounced(self, max_age_seconds: float = 3600.0) -> int:
        """Remove bounced messages older than max_age from the queue.

        Prevents unbounded queue growth when messages are never reviewed.

        Args:
            max_age_seconds: Maximum age before expiry (default 1 hour).

        Returns:
            Number of items cleared.
        """
        if not self._classifier or not hasattr(self._classifier, 'bouncer'):
            return 0

        queue = self._classifier.bouncer.get_queue()
        if not queue:
            return 0

        now = time.time()
        kept = []
        expired_count = 0
        for item in queue:
            ts = getattr(item, 'timestamp', None)
            if ts is None:
                # No timestamp — keep it (can't determine age)
                kept.append(item)
                continue
            if isinstance(ts, (int, float)) and (now - ts) > max_age_seconds:
                expired_count += 1
            else:
                kept.append(item)

        if expired_count > 0:
            self._classifier.bouncer._queue = kept
            logger.debug(f"Cleared {expired_count} expired bounced messages")

        return expired_count

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

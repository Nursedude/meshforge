"""
Sorter + Confidence + Bouncer Pattern

A unified classification system with:
- Classifier: Routes inputs into small buckets
- Confidence Score: How sure is the system? (0.0-1.0)
- Bouncer: Handles low-confidence items
- Receipt: Record of every decision
- Fix Registry: User corrections that improve the system

Design principles:
- Keep outputs small and actionable
- Always build trust mechanisms
- Systems easy to repair adopt faster
- Optimize for maintainability over cleverness
"""

import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Dict, List, Any, Tuple
from pathlib import Path
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class Category(Enum):
    """Base category enum - subclass for specific domains"""
    UNKNOWN = "unknown"


# =============================================================================
# Core Pattern: Receipt (Record of Decision)
# =============================================================================

@dataclass
class ClassificationResult:
    """
    The Receipt - a record of every classification decision.

    Provides transparency: what was decided, how confident, why.
    """
    input_id: str
    category: str
    confidence: float  # 0.0 to 1.0
    timestamp: float = field(default_factory=time.time)

    # Decision context
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Bouncer status
    bounced: bool = False
    bounce_reason: str = ""

    # Fix tracking
    was_corrected: bool = False
    original_category: str = ""
    corrected_by: str = ""

    @property
    def is_high_confidence(self) -> bool:
        """Above typical threshold (0.7)"""
        return self.confidence >= 0.7

    @property
    def is_low_confidence(self) -> bool:
        """Below bounce threshold (0.3)"""
        return self.confidence < 0.3

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'ClassificationResult':
        return cls(**data)


# =============================================================================
# Core Pattern: Bouncer (Low Confidence Handler)
# =============================================================================

@dataclass
class BouncerConfig:
    """Configuration for the bouncer"""
    threshold: float = 0.3  # Below this = bounced
    action: str = "queue"   # "queue", "drop", "escalate", "default"
    default_category: str = "unknown"
    max_queue_size: int = 100
    notify_on_bounce: bool = True


class Bouncer:
    """
    The Bouncer - handles low-confidence classifications.

    When the system isn't sure, don't guess wrong.
    Route to human review or safe default.
    """

    def __init__(self, config: Optional[BouncerConfig] = None):
        self.config = config or BouncerConfig()
        self._queue: List[ClassificationResult] = []
        self._callbacks: List[Callable[[ClassificationResult], None]] = []

    def check(self, result: ClassificationResult) -> ClassificationResult:
        """
        Check if result should be bounced.

        Returns modified result with bounce status.
        """
        if result.confidence >= self.config.threshold:
            return result

        # Low confidence - bounce it
        result.bounced = True
        result.bounce_reason = f"Confidence {result.confidence:.2f} below threshold {self.config.threshold}"

        if self.config.action == "queue":
            self._add_to_queue(result)
        elif self.config.action == "default":
            result.original_category = result.category
            result.category = self.config.default_category
        elif self.config.action == "escalate":
            self._notify_callbacks(result)
        # "drop" does nothing extra

        return result

    def _add_to_queue(self, result: ClassificationResult):
        """Add to review queue"""
        if len(self._queue) >= self.config.max_queue_size:
            self._queue.pop(0)  # Remove oldest
        self._queue.append(result)

        if self.config.notify_on_bounce:
            self._notify_callbacks(result)

    def _notify_callbacks(self, result: ClassificationResult):
        """Notify registered callbacks"""
        for cb in self._callbacks:
            try:
                cb(result)
            except Exception as e:
                logger.warning(f"Bouncer callback error: {e}")

    def register_callback(self, callback: Callable[[ClassificationResult], None]):
        """Register for bounce notifications"""
        self._callbacks.append(callback)

    def get_queue(self) -> List[ClassificationResult]:
        """Get items awaiting review"""
        return list(self._queue)

    def clear_queue(self):
        """Clear the review queue"""
        self._queue.clear()


# =============================================================================
# Core Pattern: Fix Registry (User Corrections)
# =============================================================================

@dataclass
class Fix:
    """A user correction to improve classification"""
    input_pattern: str  # What input this applies to
    correct_category: str
    created_at: float = field(default_factory=time.time)
    created_by: str = ""
    confidence_boost: float = 0.3  # How much to boost confidence


class FixRegistry:
    """
    The Fix Registry - tracks user corrections.

    Systems easy to repair adopt faster.
    Each fix makes the system smarter.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path
        self._fixes: Dict[str, Fix] = {}
        self._load()

    def add_fix(self, result: ClassificationResult, correct_category: str,
                user: str = "user") -> Fix:
        """
        Record a user correction.

        Args:
            result: The original classification
            correct_category: What it should have been
            user: Who made the correction
        """
        fix = Fix(
            input_pattern=result.input_id,
            correct_category=correct_category,
            created_by=user
        )
        self._fixes[result.input_id] = fix
        self._save()

        logger.info(f"Fix recorded: {result.input_id} -> {correct_category}")
        return fix

    def apply_fixes(self, input_id: str, result: ClassificationResult) -> ClassificationResult:
        """
        Apply any relevant fixes to a classification.

        Returns modified result if fix exists.
        """
        fix = self._fixes.get(input_id)
        if fix:
            result.was_corrected = True
            result.original_category = result.category
            result.category = fix.correct_category
            result.confidence = min(1.0, result.confidence + fix.confidence_boost)
            result.reason = f"Applied fix from {fix.created_by}"
        return result

    def get_fix(self, input_id: str) -> Optional[Fix]:
        """Get fix for specific input"""
        return self._fixes.get(input_id)

    def get_all_fixes(self) -> Dict[str, Fix]:
        """Get all registered fixes"""
        return dict(self._fixes)

    def remove_fix(self, input_id: str) -> bool:
        """Remove a fix"""
        if input_id in self._fixes:
            del self._fixes[input_id]
            self._save()
            return True
        return False

    def _load(self):
        """Load fixes from storage"""
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            with open(self.storage_path) as f:
                data = json.load(f)
                for input_id, fix_data in data.items():
                    self._fixes[input_id] = Fix(**fix_data)
        except Exception as e:
            logger.warning(f"Could not load fixes: {e}")

    def _save(self):
        """Save fixes to storage"""
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._fixes.items()}
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save fixes: {e}")


# =============================================================================
# Core Pattern: Classifier (The Sorter)
# =============================================================================

class Classifier:
    """
    Base classifier with confidence scoring.

    Subclass and implement _classify() for specific domains.
    """

    def __init__(self,
                 bouncer: Optional[Bouncer] = None,
                 fix_registry: Optional[FixRegistry] = None):
        self.bouncer = bouncer or Bouncer()
        self.fix_registry = fix_registry
        self._receipts: List[ClassificationResult] = []
        self._max_receipts = 1000

    def classify(self, input_id: str, data: Any) -> ClassificationResult:
        """
        Classify input data into a category.

        1. Run classification
        2. Apply any user fixes
        3. Check with bouncer
        4. Record receipt
        """
        # Step 1: Classify
        category, confidence, reason, metadata = self._classify(data)

        result = ClassificationResult(
            input_id=input_id,
            category=category,
            confidence=confidence,
            reason=reason,
            metadata=metadata
        )

        # Step 2: Apply fixes
        if self.fix_registry:
            result = self.fix_registry.apply_fixes(input_id, result)

        # Step 3: Bouncer check
        result = self.bouncer.check(result)

        # Step 4: Record receipt
        self._record_receipt(result)

        return result

    def _classify(self, data: Any) -> Tuple[str, float, str, Dict]:
        """
        Override this method in subclasses.

        Returns: (category, confidence, reason, metadata)
        """
        return ("unknown", 0.0, "Base classifier", {})

    def _record_receipt(self, result: ClassificationResult):
        """Store classification receipt"""
        if len(self._receipts) >= self._max_receipts:
            self._receipts.pop(0)
        self._receipts.append(result)

    def get_receipts(self, limit: int = 100) -> List[ClassificationResult]:
        """Get recent classification receipts"""
        return self._receipts[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get classification statistics"""
        if not self._receipts:
            return {"total": 0}

        categories = {}
        bounced = 0
        corrected = 0
        total_confidence = 0.0

        for r in self._receipts:
            categories[r.category] = categories.get(r.category, 0) + 1
            if r.bounced:
                bounced += 1
            if r.was_corrected:
                corrected += 1
            total_confidence += r.confidence

        return {
            "total": len(self._receipts),
            "categories": categories,
            "bounced": bounced,
            "corrected": corrected,
            "avg_confidence": total_confidence / len(self._receipts),
        }


# =============================================================================
# Domain: Message Routing Classifier
# =============================================================================

class RoutingCategory(Enum):
    """Message routing categories - small bucket"""
    BRIDGE_RNS = "bridge_to_rns"
    BRIDGE_MESH = "bridge_to_mesh"
    DROP = "drop"
    QUEUE = "queue_for_review"


class RoutingClassifier(Classifier):
    """
    Classifies messages for routing decisions.

    Small buckets: bridge_to_rns, bridge_to_mesh, drop, queue
    """

    def __init__(self, rules: List[Dict] = None, **kwargs):
        super().__init__(**kwargs)
        self.rules = rules or []

        # Confidence weights
        self.weights = {
            'rule_match': 0.4,      # Matched a routing rule
            'source_known': 0.2,    # Source is a known node
            'dest_valid': 0.2,      # Destination is valid
            'content_safe': 0.2,    # Content passed filters
        }

    def _classify(self, data: Dict) -> Tuple[str, float, str, Dict]:
        """
        Classify a message for routing.

        data should contain: source_network, source_id, destination_id, content
        """
        source_network = data.get('source_network', '')
        source_id = data.get('source_id', '')
        dest_id = data.get('destination_id')
        content = data.get('content', '')
        is_broadcast = data.get('is_broadcast', False)

        confidence = 0.0
        reasons = []
        metadata = {'matched_rules': []}

        # Determine direction
        if source_network == 'meshtastic':
            default_category = RoutingCategory.BRIDGE_RNS.value
        elif source_network == 'rns':
            default_category = RoutingCategory.BRIDGE_MESH.value
        else:
            return (RoutingCategory.DROP.value, 0.1, "Unknown source network", {})

        category = default_category

        # Check rules (priority order)
        for rule in sorted(self.rules, key=lambda r: r.get('priority', 0), reverse=True):
            if not rule.get('enabled', True):
                continue

            rule_matches = self._check_rule(rule, data)
            if rule_matches:
                confidence += self.weights['rule_match']
                reasons.append(f"Matched rule: {rule.get('name', 'unnamed')}")
                metadata['matched_rules'].append(rule.get('name'))

                # Rule can override direction
                direction = rule.get('direction', 'bidirectional')
                if direction == 'drop':
                    category = RoutingCategory.DROP.value
                break

        # Source known check
        if source_id:
            confidence += self.weights['source_known']
            reasons.append("Source identified")

        # Destination valid check
        if dest_id or is_broadcast:
            confidence += self.weights['dest_valid']
            reasons.append("Destination valid" if dest_id else "Broadcast message")

        # Content safety (basic check)
        if content and len(content) < 1000:  # Reasonable size
            confidence += self.weights['content_safe']
            reasons.append("Content OK")

        reason = "; ".join(reasons) if reasons else "Default routing"
        return (category, min(1.0, confidence), reason, metadata)

    def _check_rule(self, rule: Dict, data: Dict) -> bool:
        """Check if a rule matches the message"""
        # Direction check
        direction = rule.get('direction', 'bidirectional')
        source_network = data.get('source_network', '')

        if direction == 'mesh_to_rns' and source_network != 'meshtastic':
            return False
        if direction == 'rns_to_mesh' and source_network != 'rns':
            return False

        # Source filter (simple substring for now, TODO: regex)
        source_filter = rule.get('source_filter', '')
        if source_filter and source_filter not in data.get('source_id', ''):
            return False

        # Destination filter
        dest_filter = rule.get('dest_filter', '')
        if dest_filter and dest_filter not in (data.get('destination_id') or ''):
            return False

        # Message filter
        msg_filter = rule.get('message_filter', '')
        if msg_filter and msg_filter.lower() not in data.get('content', '').lower():
            return False

        return True


# =============================================================================
# Domain: Notification Classifier
# =============================================================================

class NotificationCategory(Enum):
    """Notification priority buckets - small and actionable"""
    CRITICAL = "critical"      # Immediate action required
    IMPORTANT = "important"    # Should see soon
    INFORMATIONAL = "info"     # Nice to know
    BACKGROUND = "background"  # Log only


class NotificationClassifier(Classifier):
    """
    Classifies events into notification priority buckets.

    Small buckets: critical, important, info, background
    Optimizes for "tap on shoulder" - useful info at right time.
    """

    # Keywords that boost priority
    CRITICAL_KEYWORDS = ['error', 'fail', 'crash', 'critical', 'emergency', 'offline']
    IMPORTANT_KEYWORDS = ['warning', 'degraded', 'timeout', 'retry', 'disconnect']
    INFO_KEYWORDS = ['connected', 'started', 'discovered', 'updated', 'received']

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Configurable thresholds
        self.thresholds = {
            'critical': 0.8,
            'important': 0.5,
            'info': 0.3,
        }

    def _classify(self, data: Dict) -> Tuple[str, float, str, Dict]:
        """
        Classify an event for notification priority.

        data should contain: severity, source, message, category
        """
        severity = data.get('severity', 'INFO').upper()
        message = data.get('message', '').lower()
        source = data.get('source', '')
        event_category = data.get('category', '')

        score = 0.0
        reasons = []

        # Severity-based scoring
        severity_scores = {
            'CRITICAL': 0.9,
            'ERROR': 0.7,
            'WARNING': 0.5,
            'INFO': 0.3,
            'DEBUG': 0.1,
        }
        score = severity_scores.get(severity, 0.3)
        reasons.append(f"Severity: {severity}")

        # Keyword boosting
        for kw in self.CRITICAL_KEYWORDS:
            if kw in message:
                score = max(score, 0.8)
                reasons.append(f"Critical keyword: {kw}")
                break

        for kw in self.IMPORTANT_KEYWORDS:
            if kw in message:
                score = max(score, 0.5)
                reasons.append(f"Important keyword: {kw}")
                break

        # Determine category based on score
        if score >= self.thresholds['critical']:
            category = NotificationCategory.CRITICAL.value
        elif score >= self.thresholds['important']:
            category = NotificationCategory.IMPORTANT.value
        elif score >= self.thresholds['info']:
            category = NotificationCategory.INFORMATIONAL.value
        else:
            category = NotificationCategory.BACKGROUND.value

        # Confidence based on clarity of classification
        confidence = min(1.0, score + 0.2)  # Boost slightly

        metadata = {
            'score': score,
            'source': source,
            'event_category': event_category,
        }

        return (category, confidence, "; ".join(reasons), metadata)

    def should_notify_user(self, result: ClassificationResult) -> bool:
        """
        Determine if this should trigger a user notification.

        Tap on shoulder = useful info at right time, not constant noise.
        """
        # Never notify on bounced low-confidence items
        if result.bounced:
            return False

        # Only notify for critical/important with high confidence
        if result.category == NotificationCategory.CRITICAL.value:
            return result.confidence >= 0.6
        elif result.category == NotificationCategory.IMPORTANT.value:
            return result.confidence >= 0.7

        return False


# =============================================================================
# Integration Helper
# =============================================================================

def create_routing_system(
    rules: List[Dict] = None,
    bounce_threshold: float = 0.3,
    fixes_path: Optional[Path] = None
) -> RoutingClassifier:
    """
    Create a configured routing classifier.

    Args:
        rules: List of routing rule dicts
        bounce_threshold: Confidence threshold for bouncer
        fixes_path: Path to persist user corrections
    """
    bouncer = Bouncer(BouncerConfig(
        threshold=bounce_threshold,
        action="queue",
        default_category=RoutingCategory.QUEUE.value
    ))

    fix_registry = FixRegistry(fixes_path) if fixes_path else None

    return RoutingClassifier(
        rules=rules or [],
        bouncer=bouncer,
        fix_registry=fix_registry
    )


def create_notification_system(
    bounce_threshold: float = 0.2,
    fixes_path: Optional[Path] = None
) -> NotificationClassifier:
    """
    Create a configured notification classifier.

    Args:
        bounce_threshold: Confidence threshold for bouncer
        fixes_path: Path to persist user corrections
    """
    bouncer = Bouncer(BouncerConfig(
        threshold=bounce_threshold,
        action="default",
        default_category=NotificationCategory.BACKGROUND.value
    ))

    fix_registry = FixRegistry(fixes_path) if fixes_path else None

    return NotificationClassifier(
        bouncer=bouncer,
        fix_registry=fix_registry
    )

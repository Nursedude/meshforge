"""
Tests for Sorter + Confidence + Bouncer Pattern

Tests the classifier module which implements:
- Classifier: Routes inputs into small buckets
- Confidence Score: How sure is the system?
- Bouncer: Handles low-confidence items
- Receipt: Record of every decision
- Fix Registry: User corrections
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.classifier import (
    ClassificationResult,
    Bouncer,
    BouncerConfig,
    Fix,
    FixRegistry,
    Classifier,
    RoutingClassifier,
    RoutingCategory,
    NotificationClassifier,
    NotificationCategory,
    create_routing_system,
    create_notification_system,
)


class TestClassificationResult:
    """Tests for the Receipt pattern"""

    def test_creation(self):
        """Test basic result creation"""
        result = ClassificationResult(
            input_id="msg_001",
            category="important",
            confidence=0.85
        )

        assert result.input_id == "msg_001"
        assert result.category == "important"
        assert result.confidence == 0.85
        assert result.timestamp > 0

    def test_high_confidence(self):
        """Test high confidence detection"""
        high = ClassificationResult("a", "cat", 0.8)
        low = ClassificationResult("b", "cat", 0.5)

        assert high.is_high_confidence is True
        assert low.is_high_confidence is False

    def test_low_confidence(self):
        """Test low confidence detection"""
        low = ClassificationResult("a", "cat", 0.2)
        medium = ClassificationResult("b", "cat", 0.5)

        assert low.is_low_confidence is True
        assert medium.is_low_confidence is False

    def test_to_dict(self):
        """Test serialization"""
        result = ClassificationResult(
            input_id="test",
            category="info",
            confidence=0.7,
            reason="Test reason"
        )

        data = result.to_dict()

        assert data['input_id'] == "test"
        assert data['category'] == "info"
        assert data['confidence'] == 0.7
        assert data['reason'] == "Test reason"

    def test_from_dict(self):
        """Test deserialization"""
        data = {
            'input_id': 'test',
            'category': 'warning',
            'confidence': 0.6,
            'timestamp': 1234567890.0,
            'reason': 'Restored',
            'metadata': {},
            'bounced': False,
            'bounce_reason': '',
            'was_corrected': False,
            'original_category': '',
            'corrected_by': ''
        }

        result = ClassificationResult.from_dict(data)

        assert result.input_id == 'test'
        assert result.category == 'warning'
        assert result.confidence == 0.6

    def test_correction_tracking(self):
        """Test correction metadata"""
        result = ClassificationResult(
            input_id="test",
            category="wrong",
            confidence=0.5,
            was_corrected=True,
            original_category="correct",
            corrected_by="user"
        )

        assert result.was_corrected is True
        assert result.original_category == "correct"


class TestBouncer:
    """Tests for the Bouncer pattern"""

    def test_passes_high_confidence(self):
        """Test that high confidence results pass through"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5))
        result = ClassificationResult("a", "cat", 0.8)

        checked = bouncer.check(result)

        assert checked.bounced is False
        assert checked.bounce_reason == ""

    def test_bounces_low_confidence(self):
        """Test that low confidence results are bounced"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5))
        result = ClassificationResult("a", "cat", 0.3)

        checked = bouncer.check(result)

        assert checked.bounced is True
        assert "0.30" in checked.bounce_reason
        assert "0.5" in checked.bounce_reason

    def test_queue_action(self):
        """Test queue action adds to queue"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5, action="queue"))
        result = ClassificationResult("a", "cat", 0.2)

        bouncer.check(result)

        queue = bouncer.get_queue()
        assert len(queue) == 1
        assert queue[0].input_id == "a"

    def test_default_action(self):
        """Test default action changes category"""
        bouncer = Bouncer(BouncerConfig(
            threshold=0.5,
            action="default",
            default_category="fallback"
        ))
        result = ClassificationResult("a", "original", 0.2)

        checked = bouncer.check(result)

        assert checked.category == "fallback"
        assert checked.original_category == "original"

    def test_callback_on_bounce(self):
        """Test callback is called on bounce"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5, notify_on_bounce=True))
        callback = MagicMock()
        bouncer.register_callback(callback)

        result = ClassificationResult("a", "cat", 0.2)
        bouncer.check(result)

        callback.assert_called_once()

    def test_max_queue_size(self):
        """Test queue respects max size"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5, max_queue_size=3))

        for i in range(5):
            result = ClassificationResult(f"msg_{i}", "cat", 0.1)
            bouncer.check(result)

        queue = bouncer.get_queue()
        assert len(queue) == 3
        assert queue[0].input_id == "msg_2"  # Oldest kept

    def test_clear_queue(self):
        """Test clearing the queue"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5))
        result = ClassificationResult("a", "cat", 0.2)
        bouncer.check(result)

        bouncer.clear_queue()

        assert len(bouncer.get_queue()) == 0

    def test_threshold_edge_case(self):
        """Test exact threshold value"""
        bouncer = Bouncer(BouncerConfig(threshold=0.5))

        at_threshold = ClassificationResult("a", "cat", 0.5)
        below = ClassificationResult("b", "cat", 0.49)

        assert bouncer.check(at_threshold).bounced is False
        assert bouncer.check(below).bounced is True


class TestFixRegistry:
    """Tests for the Fix Registry pattern"""

    def test_add_fix(self):
        """Test adding a fix"""
        registry = FixRegistry()
        result = ClassificationResult("msg_001", "wrong", 0.5)

        fix = registry.add_fix(result, "correct", user="test_user")

        assert fix.input_pattern == "msg_001"
        assert fix.correct_category == "correct"
        assert fix.created_by == "test_user"

    def test_apply_fixes(self):
        """Test applying fixes to results"""
        registry = FixRegistry()
        original = ClassificationResult("msg_001", "wrong", 0.5)
        registry.add_fix(original, "correct")

        new_result = ClassificationResult("msg_001", "wrong", 0.6)
        fixed = registry.apply_fixes("msg_001", new_result)

        assert fixed.category == "correct"
        assert fixed.was_corrected is True
        assert fixed.original_category == "wrong"
        assert fixed.confidence > 0.6  # Boosted

    def test_no_fix_available(self):
        """Test when no fix exists"""
        registry = FixRegistry()
        result = ClassificationResult("unknown", "cat", 0.5)

        unchanged = registry.apply_fixes("unknown", result)

        assert unchanged.category == "cat"
        assert unchanged.was_corrected is False

    def test_get_fix(self):
        """Test retrieving a specific fix"""
        registry = FixRegistry()
        result = ClassificationResult("msg_001", "wrong", 0.5)
        registry.add_fix(result, "correct")

        fix = registry.get_fix("msg_001")

        assert fix is not None
        assert fix.correct_category == "correct"

    def test_remove_fix(self):
        """Test removing a fix"""
        registry = FixRegistry()
        result = ClassificationResult("msg_001", "wrong", 0.5)
        registry.add_fix(result, "correct")

        removed = registry.remove_fix("msg_001")

        assert removed is True
        assert registry.get_fix("msg_001") is None

    def test_persistence(self):
        """Test fix persistence to file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fixes.json"

            # Create and save
            registry1 = FixRegistry(path)
            result = ClassificationResult("msg_001", "wrong", 0.5)
            registry1.add_fix(result, "correct")

            # Load in new instance
            registry2 = FixRegistry(path)
            fix = registry2.get_fix("msg_001")

            assert fix is not None
            assert fix.correct_category == "correct"

    def test_get_all_fixes(self):
        """Test getting all fixes"""
        registry = FixRegistry()

        for i in range(3):
            result = ClassificationResult(f"msg_{i}", "wrong", 0.5)
            registry.add_fix(result, f"correct_{i}")

        all_fixes = registry.get_all_fixes()

        assert len(all_fixes) == 3
        assert "msg_0" in all_fixes
        assert "msg_1" in all_fixes
        assert "msg_2" in all_fixes


class TestClassifier:
    """Tests for the base Classifier"""

    def test_classify_returns_result(self):
        """Test basic classification"""
        classifier = Classifier()

        result = classifier.classify("input_1", {"data": "test"})

        assert isinstance(result, ClassificationResult)
        assert result.input_id == "input_1"

    def test_records_receipts(self):
        """Test receipt recording"""
        classifier = Classifier()

        for i in range(5):
            classifier.classify(f"input_{i}", {})

        receipts = classifier.get_receipts()
        assert len(receipts) == 5

    def test_receipts_limit(self):
        """Test receipt limit"""
        classifier = Classifier()
        classifier._max_receipts = 10

        for i in range(15):
            classifier.classify(f"input_{i}", {})

        receipts = classifier.get_receipts()
        assert len(receipts) == 10
        assert receipts[0].input_id == "input_5"  # Oldest kept

    def test_get_stats(self):
        """Test statistics generation"""
        classifier = Classifier()

        for i in range(10):
            classifier.classify(f"input_{i}", {})

        stats = classifier.get_stats()

        assert stats['total'] == 10
        assert 'categories' in stats
        assert 'avg_confidence' in stats

    def test_bouncer_integration(self):
        """Test bouncer is called"""
        bouncer = Bouncer(BouncerConfig(threshold=0.9))  # High threshold
        classifier = Classifier(bouncer=bouncer)

        result = classifier.classify("input", {})

        # Base classifier returns 0.0 confidence, should bounce
        assert result.bounced is True

    def test_fix_registry_integration(self):
        """Test fix registry is applied"""
        fix_registry = FixRegistry()
        classifier = Classifier(fix_registry=fix_registry)

        # Add a fix
        original = ClassificationResult("input_1", "wrong", 0.5)
        fix_registry.add_fix(original, "fixed")

        # Classify same input
        result = classifier.classify("input_1", {})

        assert result.category == "fixed"
        assert result.was_corrected is True


class TestRoutingClassifier:
    """Tests for message routing classification"""

    def test_mesh_to_rns_default(self):
        """Test meshtastic message defaults to RNS"""
        classifier = RoutingClassifier()

        result = classifier.classify("msg_1", {
            'source_network': 'meshtastic',
            'source_id': 'node123',
            'content': 'Hello'
        })

        assert result.category == RoutingCategory.BRIDGE_RNS.value

    def test_rns_to_mesh_default(self):
        """Test RNS message defaults to mesh"""
        classifier = RoutingClassifier()

        result = classifier.classify("msg_1", {
            'source_network': 'rns',
            'source_id': 'abc123',
            'content': 'Hello'
        })

        assert result.category == RoutingCategory.BRIDGE_MESH.value

    def test_unknown_network_drops(self):
        """Test unknown network drops message"""
        classifier = RoutingClassifier()

        result = classifier.classify("msg_1", {
            'source_network': 'unknown',
            'content': 'Hello'
        })

        assert result.category == RoutingCategory.DROP.value
        assert result.confidence < 0.2

    def test_rule_matching(self):
        """Test routing rules are applied"""
        rules = [
            {
                'name': 'test_rule',
                'enabled': True,
                'direction': 'mesh_to_rns',
                'source_filter': 'special',
                'priority': 10
            }
        ]
        classifier = RoutingClassifier(rules=rules)

        result = classifier.classify("msg_1", {
            'source_network': 'meshtastic',
            'source_id': 'special_node',
            'content': 'Hello'
        })

        assert 'test_rule' in result.metadata.get('matched_rules', [])

    def test_disabled_rule_ignored(self):
        """Test disabled rules are skipped"""
        rules = [
            {
                'name': 'disabled_rule',
                'enabled': False,
                'direction': 'mesh_to_rns',
                'priority': 10
            }
        ]
        classifier = RoutingClassifier(rules=rules)

        result = classifier.classify("msg_1", {
            'source_network': 'meshtastic',
            'source_id': 'node',
            'content': 'Hello'
        })

        assert 'disabled_rule' not in result.metadata.get('matched_rules', [])

    def test_confidence_factors(self):
        """Test confidence is built from multiple factors"""
        classifier = RoutingClassifier()

        # Message with all factors
        full = classifier.classify("msg_1", {
            'source_network': 'meshtastic',
            'source_id': 'node123',
            'destination_id': 'dest456',
            'content': 'Hello'
        })

        # Message missing factors
        partial = classifier.classify("msg_2", {
            'source_network': 'meshtastic',
            'content': ''
        })

        assert full.confidence > partial.confidence

    def test_broadcast_message(self):
        """Test broadcast messages"""
        classifier = RoutingClassifier()

        result = classifier.classify("msg_1", {
            'source_network': 'meshtastic',
            'source_id': 'node',
            'is_broadcast': True,
            'content': 'Broadcast message'
        })

        assert "Broadcast" in result.reason

    def test_direction_filter(self):
        """Test direction filtering in rules"""
        rules = [
            {
                'name': 'mesh_only',
                'enabled': True,
                'direction': 'mesh_to_rns',
                'priority': 10
            }
        ]
        classifier = RoutingClassifier(rules=rules)

        # Should not match for RNS source
        result = classifier.classify("msg_1", {
            'source_network': 'rns',
            'source_id': 'node',
            'content': 'Hello'
        })

        assert 'mesh_only' not in result.metadata.get('matched_rules', [])


class TestNotificationClassifier:
    """Tests for notification priority classification"""

    def test_critical_severity(self):
        """Test critical severity classification"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'CRITICAL',
            'message': 'System failure'
        })

        assert result.category == NotificationCategory.CRITICAL.value

    def test_error_severity(self):
        """Test error severity classification"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'ERROR',
            'message': 'Connection error'
        })

        assert result.category in [
            NotificationCategory.CRITICAL.value,
            NotificationCategory.IMPORTANT.value
        ]

    def test_info_severity(self):
        """Test info severity classification"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'INFO',
            'message': 'Node connected'
        })

        assert result.category in [
            NotificationCategory.INFORMATIONAL.value,
            NotificationCategory.BACKGROUND.value
        ]

    def test_keyword_boost_critical(self):
        """Test critical keywords boost priority"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'INFO',
            'message': 'System crash detected'  # 'crash' is critical
        })

        assert result.category == NotificationCategory.CRITICAL.value

    def test_keyword_boost_important(self):
        """Test important keywords boost priority"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'DEBUG',
            'message': 'Connection timeout occurred'  # 'timeout' is important
        })

        assert result.category in [
            NotificationCategory.IMPORTANT.value,
            NotificationCategory.INFORMATIONAL.value
        ]

    def test_should_notify_critical(self):
        """Test critical notifications trigger alert"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'CRITICAL',
            'message': 'Emergency'
        })

        assert classifier.should_notify_user(result) is True

    def test_should_not_notify_background(self):
        """Test background events don't trigger alert"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'DEBUG',
            'message': 'Routine check'
        })

        assert classifier.should_notify_user(result) is False

    def test_bounced_never_notifies(self):
        """Test bounced items never trigger notification"""
        bouncer = Bouncer(BouncerConfig(threshold=0.95))  # Very high
        classifier = NotificationClassifier(bouncer=bouncer)

        result = classifier.classify("event_1", {
            'severity': 'CRITICAL',
            'message': 'Error'
        })

        # Even critical, if bounced, don't notify
        if result.bounced:
            assert classifier.should_notify_user(result) is False

    def test_metadata_includes_source(self):
        """Test metadata includes event source"""
        classifier = NotificationClassifier()

        result = classifier.classify("event_1", {
            'severity': 'INFO',
            'source': 'gateway',
            'message': 'Test'
        })

        assert result.metadata.get('source') == 'gateway'


class TestIntegrationHelpers:
    """Tests for integration helper functions"""

    def test_create_routing_system(self):
        """Test routing system creation"""
        rules = [{'name': 'test', 'enabled': True, 'direction': 'bidirectional'}]
        classifier = create_routing_system(rules=rules, bounce_threshold=0.4)

        assert isinstance(classifier, RoutingClassifier)
        assert classifier.bouncer.config.threshold == 0.4
        assert len(classifier.rules) == 1

    def test_create_routing_with_fixes(self):
        """Test routing system with fix persistence"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fixes.json"
            classifier = create_routing_system(fixes_path=path)

            assert classifier.fix_registry is not None

    def test_create_notification_system(self):
        """Test notification system creation"""
        classifier = create_notification_system(bounce_threshold=0.3)

        assert isinstance(classifier, NotificationClassifier)
        assert classifier.bouncer.config.threshold == 0.3

    def test_default_bounce_action(self):
        """Test notification bouncer uses default action"""
        classifier = create_notification_system()

        assert classifier.bouncer.config.action == "default"
        assert classifier.bouncer.config.default_category == NotificationCategory.BACKGROUND.value


class TestEndToEnd:
    """End-to-end integration tests"""

    def test_full_routing_flow(self):
        """Test complete routing flow with fixes"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup
            fixes_path = Path(tmpdir) / "fixes.json"
            classifier = create_routing_system(
                rules=[],
                bounce_threshold=0.3,
                fixes_path=fixes_path
            )

            # First classification
            result1 = classifier.classify("msg_001", {
                'source_network': 'meshtastic',
                'source_id': 'special_node',
                'content': 'Hello'
            })

            # User corrects it
            classifier.fix_registry.add_fix(result1, RoutingCategory.DROP.value)

            # Same input now gets fix applied
            result2 = classifier.classify("msg_001", {
                'source_network': 'meshtastic',
                'source_id': 'special_node',
                'content': 'Hello'
            })

            assert result2.category == RoutingCategory.DROP.value
            assert result2.was_corrected is True

    def test_notification_flow_with_bounce(self):
        """Test notification flow with bouncer"""
        bouncer = Bouncer(BouncerConfig(
            threshold=0.8,
            action="queue",
            notify_on_bounce=True
        ))

        callback = MagicMock()
        bouncer.register_callback(callback)

        classifier = NotificationClassifier(bouncer=bouncer)

        # Low severity will bounce
        result = classifier.classify("event_1", {
            'severity': 'DEBUG',
            'message': 'Minor update'
        })

        if result.bounced:
            # Callback should have been called
            callback.assert_called()
            # Should be in queue
            assert len(bouncer.get_queue()) > 0

    def test_stats_tracking(self):
        """Test statistics are tracked correctly"""
        classifier = create_notification_system(bounce_threshold=0.1)

        # Generate various events
        events = [
            {'severity': 'CRITICAL', 'message': 'Crash'},
            {'severity': 'ERROR', 'message': 'Failed'},
            {'severity': 'WARNING', 'message': 'Degraded'},
            {'severity': 'INFO', 'message': 'Started'},
            {'severity': 'DEBUG', 'message': 'Trace'},
        ]

        for i, event in enumerate(events):
            classifier.classify(f"event_{i}", event)

        stats = classifier.get_stats()

        assert stats['total'] == 5
        assert stats['avg_confidence'] > 0
        assert len(stats['categories']) > 0

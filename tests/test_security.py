"""
Security validation tests for MeshForge.

Run with: python3 -m pytest tests/test_security.py -v
Or without pytest: python3 tests/test_security.py
"""

import sys
import os
import re

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# Validation functions (copied from main_web.py for standalone testing)

def validate_journalctl_since(since_value):
    """Validate journalctl --since parameter to prevent injection."""
    if not since_value:
        return True, None

    if len(since_value) > 50:
        return False, "Time value too long"

    safe_patterns = [
        r'^[\d]{4}-[\d]{2}-[\d]{2}(\s[\d]{2}:[\d]{2}(:[\d]{2})?)?$',
        r'^\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago$',
        r'^(today|yesterday|now|tomorrow)$',
    ]

    for pattern in safe_patterns:
        if re.match(pattern, since_value.lower().strip()):
            return True, None

    return False, "Invalid time format"


def validate_node_id(node_id):
    """Validate that a node ID is a valid hex string."""
    if not node_id:
        return False
    if not isinstance(node_id, str):
        return False
    if not re.match(r'^[a-fA-F0-9]{8,16}$', node_id):
        return False
    return True


def validate_message(message, max_length=230):
    """Validate a message for sending."""
    if not message:
        return False, "Message cannot be empty"
    if not isinstance(message, str):
        return False, "Message must be a string"
    if len(message.encode('utf-8')) > max_length:
        return False, f"Message exceeds {max_length} byte limit"
    return True, None


class TestJournalctlValidation:
    """Test journalctl time parameter validation."""

    def test_valid_date(self):
        """Valid date formats should pass."""
        valid, _ = validate_journalctl_since("2024-01-15")
        assert valid is True

    def test_valid_datetime(self):
        """Valid datetime formats should pass."""
        valid, _ = validate_journalctl_since("2024-01-15 10:30")
        assert valid is True

    def test_valid_datetime_seconds(self):
        """Valid datetime with seconds should pass."""
        valid, _ = validate_journalctl_since("2024-01-15 10:30:45")
        assert valid is True

    def test_valid_relative_time(self):
        """Valid relative time formats should pass."""
        assert validate_journalctl_since("5 minutes ago")[0] is True
        assert validate_journalctl_since("1 hour ago")[0] is True
        assert validate_journalctl_since("2 days ago")[0] is True

    def test_valid_keywords(self):
        """Valid keyword formats should pass."""
        assert validate_journalctl_since("today")[0] is True
        assert validate_journalctl_since("yesterday")[0] is True
        assert validate_journalctl_since("now")[0] is True

    def test_empty_value(self):
        """Empty values should pass (no filter)."""
        assert validate_journalctl_since("")[0] is True
        assert validate_journalctl_since(None)[0] is True

    def test_injection_attempt_semicolon(self):
        """Semicolon injection should be blocked."""
        valid, _ = validate_journalctl_since("today; rm -rf /")
        assert valid is False

    def test_injection_attempt_pipe(self):
        """Pipe injection should be blocked."""
        valid, _ = validate_journalctl_since("today | cat /etc/passwd")
        assert valid is False

    def test_injection_attempt_backtick(self):
        """Backtick injection should be blocked."""
        valid, _ = validate_journalctl_since("`whoami`")
        assert valid is False

    def test_too_long_value(self):
        """Values over 50 chars should be rejected."""
        long_value = "a" * 51
        valid, error = validate_journalctl_since(long_value)
        assert valid is False
        assert "too long" in error


class TestNodeIdValidation:
    """Test node ID validation."""

    def test_valid_8char_hex(self):
        """Valid 8-character hex should pass."""
        assert validate_node_id("abcd1234") is True

    def test_valid_16char_hex(self):
        """Valid 16-character hex should pass."""
        assert validate_node_id("abcd1234efgh5678") is False  # h is not hex
        assert validate_node_id("abcd1234abcd1234") is True

    def test_valid_mixed_case(self):
        """Mixed case hex should pass."""
        assert validate_node_id("AbCd1234") is True
        assert validate_node_id("ABCD1234") is True

    def test_invalid_too_short(self):
        """Too short should fail."""
        assert validate_node_id("abc123") is False

    def test_invalid_too_long(self):
        """Too long should fail."""
        assert validate_node_id("abcd1234abcd12345") is False

    def test_invalid_non_hex(self):
        """Non-hex characters should fail."""
        assert validate_node_id("ghij1234") is False
        assert validate_node_id("!@#$%^&*") is False

    def test_invalid_empty(self):
        """Empty should fail."""
        assert validate_node_id("") is False
        assert validate_node_id(None) is False

    def test_invalid_type(self):
        """Non-string should fail."""
        assert validate_node_id(12345678) is False


class TestMessageValidation:
    """Test message validation."""

    def test_valid_message(self):
        """Valid message should pass."""
        valid, _ = validate_message("Hello mesh!")
        assert valid is True

    def test_valid_max_length(self):
        """Message at max length should pass."""
        msg = "a" * 230
        valid, _ = validate_message(msg)
        assert valid is True

    def test_invalid_too_long(self):
        """Message over limit should fail."""
        msg = "a" * 231
        valid, error = validate_message(msg)
        assert valid is False
        assert "230 byte" in error

    def test_invalid_empty(self):
        """Empty message should fail."""
        valid, error = validate_message("")
        assert valid is False
        assert "empty" in error

    def test_invalid_none(self):
        """None should fail."""
        valid, error = validate_message(None)
        assert valid is False

    def test_unicode_byte_counting(self):
        """Unicode should count bytes, not chars."""
        # Emoji takes 4 bytes
        msg = "Hello " + "\U0001F600" * 56  # 6 + (56 * 4) = 230 bytes
        valid, _ = validate_message(msg)
        assert valid is True

        # One more emoji exceeds limit
        msg = "Hello " + "\U0001F600" * 57  # 6 + (57 * 4) = 234 bytes
        valid, _ = validate_message(msg)
        assert valid is False


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestJournalctlValidation,
        TestNodeIdValidation,
        TestMessageValidation,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)

        instance = test_class()
        for name in dir(instance):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 40)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

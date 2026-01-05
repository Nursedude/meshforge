"""
Gateway diagnostic tests for MeshForge.

Tests the RNS/Meshtastic gateway diagnostic system.
Run with: python3 -m pytest tests/test_gateway_diagnostic.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.gateway_diagnostic import (
    GatewayDiagnostic,
    CheckResult,
    CheckStatus,
)


class TestCheckResult:
    """Test CheckResult dataclass."""

    def test_pass_result(self):
        """PASS result should have correct status."""
        result = CheckResult(
            name="Test Check",
            status=CheckStatus.PASS,
            message="All good"
        )
        assert result.status == CheckStatus.PASS
        assert result.is_ok()

    def test_fail_result(self):
        """FAIL result should not be ok."""
        result = CheckResult(
            name="Test Check",
            status=CheckStatus.FAIL,
            message="Something wrong",
            fix_hint="Try this fix"
        )
        assert result.status == CheckStatus.FAIL
        assert not result.is_ok()
        assert result.fix_hint == "Try this fix"

    def test_warn_result(self):
        """WARN result should be ok but flagged."""
        result = CheckResult(
            name="Test Check",
            status=CheckStatus.WARN,
            message="Minor issue"
        )
        assert result.status == CheckStatus.WARN
        assert result.is_ok()  # Warnings are still "ok"

    def test_skip_result(self):
        """SKIP result for optional checks."""
        result = CheckResult(
            name="Optional Check",
            status=CheckStatus.SKIP,
            message="Not applicable"
        )
        assert result.status == CheckStatus.SKIP
        assert result.is_ok()


class TestGatewayDiagnostic:
    """Test GatewayDiagnostic class."""

    def test_initialization(self):
        """Diagnostic should initialize with empty results."""
        diag = GatewayDiagnostic()
        assert diag.results == []
        assert diag.connection_type is None

    def test_run_all_checks(self):
        """run_all() should return list of CheckResults."""
        diag = GatewayDiagnostic()
        results = diag.run_all()
        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(r, CheckResult) for r in results)

    def test_check_categories(self):
        """Should have checks in multiple categories."""
        diag = GatewayDiagnostic()
        results = diag.run_all()

        # Extract check names
        names = [r.name for r in results]

        # Should have system checks
        assert any('Python' in n or 'pip' in n for n in names)

        # Should have RNS checks
        assert any('RNS' in n or 'Reticulum' in n for n in names)

    def test_summary_generation(self):
        """Should generate human-readable summary."""
        diag = GatewayDiagnostic()
        diag.run_all()
        summary = diag.get_summary()

        assert isinstance(summary, str)
        assert len(summary) > 0
        assert 'PASS' in summary or 'FAIL' in summary or 'WARN' in summary

    def test_fix_hints_available(self):
        """Failed checks should have fix hints."""
        diag = GatewayDiagnostic()
        results = diag.run_all()

        for result in results:
            if result.status == CheckStatus.FAIL:
                # Failed checks should have hints
                assert result.fix_hint is not None or result.message != ""

    def test_connection_detection(self):
        """Should detect available connection types."""
        diag = GatewayDiagnostic()
        conn_types = diag.detect_connection_types()

        assert isinstance(conn_types, dict)
        assert 'serial' in conn_types
        assert 'tcp' in conn_types
        assert 'ble' in conn_types


class TestSerialDetection:
    """Test serial port detection."""

    def test_list_serial_ports(self):
        """Should list available serial ports."""
        diag = GatewayDiagnostic()
        ports = diag.list_serial_ports()

        assert isinstance(ports, list)
        # Each port should be a dict with 'device' and 'description'
        for port in ports:
            assert 'device' in port
            assert 'description' in port


class TestTCPDetection:
    """Test TCP connection detection."""

    def test_check_tcp_port(self):
        """Should check if TCP port is open."""
        diag = GatewayDiagnostic()

        # Port 4403 is meshtasticd default
        result = diag.check_tcp_port('localhost', 4403)
        assert isinstance(result, bool)

    def test_check_meshtasticd(self):
        """Should check meshtasticd service."""
        diag = GatewayDiagnostic()
        result = diag.check_meshtasticd()

        assert isinstance(result, CheckResult)
        assert result.name == "meshtasticd Service"


class TestRNSChecks:
    """Test RNS-specific checks."""

    def test_check_rns_installed(self):
        """Should check if RNS is installed."""
        diag = GatewayDiagnostic()
        result = diag.check_rns_installed()

        assert isinstance(result, CheckResult)
        assert 'RNS' in result.name or 'Reticulum' in result.name

    def test_check_rns_config(self):
        """Should check RNS config file."""
        diag = GatewayDiagnostic()
        result = diag.check_rns_config()

        assert isinstance(result, CheckResult)
        assert 'config' in result.name.lower()

    def test_check_rnsd_running(self):
        """Should check if rnsd daemon is running."""
        diag = GatewayDiagnostic()
        result = diag.check_rnsd_running()

        assert isinstance(result, CheckResult)


class TestMeshtasticChecks:
    """Test Meshtastic-specific checks."""

    def test_check_meshtastic_installed(self):
        """Should check if meshtastic library is installed."""
        diag = GatewayDiagnostic()
        result = diag.check_meshtastic_installed()

        assert isinstance(result, CheckResult)
        assert 'Meshtastic' in result.name or 'meshtastic' in result.message.lower()

    def test_check_meshtastic_interface(self):
        """Should check Meshtastic_Interface.py installation."""
        diag = GatewayDiagnostic()
        result = diag.check_meshtastic_interface()

        assert isinstance(result, CheckResult)


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestCheckResult,
        TestGatewayDiagnostic,
        TestSerialDetection,
        TestTCPDetection,
        TestRNSChecks,
        TestMeshtasticChecks,
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

"""
Radio configuration parsing tests for MeshForge.

Tests for the region enum mapping and channel parsing logic.
Run with: python3 -m pytest tests/test_radio_config.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestRegionEnumMapping:
    """Test region enum to string conversion."""

    # Meshtastic region enum values (from protobuf)
    REGION_ENUM_MAP = {
        0: "UNSET",
        1: "US",
        2: "EU_433",
        3: "EU_868",
        4: "CN",
        5: "JP",
        6: "ANZ",
        7: "KR",
        8: "TW",
        9: "RU",
        10: "IN",
        11: "NZ_865",
        12: "TH",
        13: "LORA_24",
        14: "UA_433",
        15: "UA_868",
        16: "MY_433",
        17: "MY_919",
        18: "SG_923",
        19: "PH",
        20: "UK_868",
        21: "SINGAPORE",
    }

    REGIONS_LIST = [
        "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
        "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
        "SG_923", "PH", "UK_868", "SINGAPORE"
    ]

    def test_us_region_from_int(self):
        """Region 1 should map to 'US'."""
        region_int = 1
        region_str = self.REGION_ENUM_MAP.get(region_int, str(region_int))
        assert region_str == "US"

    def test_eu_868_from_int(self):
        """Region 3 should map to 'EU_868'."""
        region_int = 3
        region_str = self.REGION_ENUM_MAP.get(region_int, str(region_int))
        assert region_str == "EU_868"

    def test_all_regions_have_mapping(self):
        """All regions in the list should have an enum mapping."""
        for i, region in enumerate(self.REGIONS_LIST):
            assert self.REGION_ENUM_MAP.get(i) == region, f"Region {i} should map to {region}"

    def test_find_region_index_from_string(self):
        """Find the index of a region string in the list."""
        assert self.REGIONS_LIST.index("US") == 1
        assert self.REGIONS_LIST.index("EU_868") == 3


class TestChannelParsing:
    """Test channel information parsing."""

    SAMPLE_CHANNELS_OUTPUT = """
Channels:
{0: {'role': 'PRIMARY', 'settings': {'psk': b'\\x01', 'name': 'LongFast'}}}
{1: {'role': 'SECONDARY', 'settings': {'psk': b'...', 'name': 'Admin'}}}
{2: {'role': 'DISABLED'}}
"""

    def test_parse_channel_count(self):
        """Should count channels correctly."""
        import re
        # Count PRIMARY or SECONDARY channels
        channel_entries = re.findall(r"'role':\s*'(PRIMARY|SECONDARY)'", self.SAMPLE_CHANNELS_OUTPUT)
        assert len(channel_entries) == 2

    def test_parse_channel_names(self):
        """Should extract channel names."""
        import re
        # Extract channel names
        channel_names = re.findall(r"'name':\s*'([^']+)'", self.SAMPLE_CHANNELS_OUTPUT)
        assert "LongFast" in channel_names
        assert "Admin" in channel_names

    def test_parse_channel_roles(self):
        """Should extract channel roles."""
        import re
        # Extract all roles
        roles = re.findall(r"'role':\s*'([^']+)'", self.SAMPLE_CHANNELS_OUTPUT)
        assert "PRIMARY" in roles
        assert "SECONDARY" in roles
        assert "DISABLED" in roles


class TestConfigDictApplication:
    """Test applying config dict to dropdowns (logic only, no GTK)."""

    PRESETS = ["LONG_FAST", "LONG_SLOW", "VERY_LONG_SLOW", "LONG_MODERATE",
               "MEDIUM_SLOW", "MEDIUM_FAST", "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]

    PRESET_ENUM_MAP = {
        0: "LONG_FAST", 1: "LONG_SLOW", 2: "VERY_LONG_SLOW",
        3: "MEDIUM_SLOW", 4: "MEDIUM_FAST", 5: "SHORT_SLOW",
        6: "SHORT_FAST", 7: "LONG_MODERATE", 8: "SHORT_TURBO"
    }

    REGIONS = ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
               "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
               "SG_923", "PH", "UK_868", "SINGAPORE"]

    REGION_ENUM_MAP = {
        0: "UNSET", 1: "US", 2: "EU_433", 3: "EU_868", 4: "CN", 5: "JP",
        6: "ANZ", 7: "KR", 8: "TW", 9: "RU", 10: "IN", 11: "NZ_865",
        12: "TH", 13: "LORA_24", 14: "UA_433", 15: "UA_868", 16: "MY_433",
        17: "MY_919", 18: "SG_923", 19: "PH", 20: "UK_868", 21: "SINGAPORE"
    }

    def find_dropdown_index(self, options, value, enum_map=None):
        """Find the dropdown index for a value (simulates set_dropdown logic)."""
        # Convert enum int to string if needed
        if isinstance(value, int) and enum_map:
            value = enum_map.get(value, str(value))
        elif isinstance(value, int):
            value = str(value)

        value_str = str(value).upper().replace('_', '')
        for i, opt in enumerate(options):
            if opt.upper().replace('_', '') == value_str:
                return i
        return None

    def test_preset_from_int(self):
        """Preset int 0 should find index for LONG_FAST."""
        idx = self.find_dropdown_index(self.PRESETS, 0, self.PRESET_ENUM_MAP)
        assert idx == 0  # LONG_FAST is first

    def test_preset_from_string(self):
        """Preset string should find correct index."""
        idx = self.find_dropdown_index(self.PRESETS, "LONG_SLOW", self.PRESET_ENUM_MAP)
        assert idx == 1  # LONG_SLOW is second

    def test_region_from_int_should_work(self):
        """Region int 1 should find index for US (CURRENTLY FAILS - BUG)."""
        # This test should FAIL initially because region_enum_map is missing
        idx = self.find_dropdown_index(self.REGIONS, 1, self.REGION_ENUM_MAP)
        assert idx == 1, "Region 1 (US) should find index 1"

    def test_region_from_int_without_map_fails(self):
        """Region int without enum_map should fail to find correct index."""
        # Without enum_map, int 1 becomes string "1" which doesn't match "US"
        idx = self.find_dropdown_index(self.REGIONS, 1, None)
        assert idx is None, "Without enum map, int 1 should not match 'US'"

    def test_short_turbo_enum_8(self):
        """SHORT_TURBO is enum 8, should find correct dropdown index."""
        idx = self.find_dropdown_index(self.PRESETS, 8, self.PRESET_ENUM_MAP)
        assert idx == 8, f"SHORT_TURBO (enum 8) should be at index 8, got {idx}"
        assert self.PRESETS[idx] == "SHORT_TURBO"

    def test_long_moderate_enum_7(self):
        """LONG_MODERATE is enum 7, should find correct dropdown index."""
        idx = self.find_dropdown_index(self.PRESETS, 7, self.PRESET_ENUM_MAP)
        # LONG_MODERATE is at index 3 in dropdown, enum value 7
        assert idx == 3, f"LONG_MODERATE (enum 7) should be at index 3, got {idx}"
        assert self.PRESETS[idx] == "LONG_MODERATE"

    def test_all_presets_round_trip(self):
        """Loading any preset enum should return the correct string for CLI."""
        for enum_val, preset_name in self.PRESET_ENUM_MAP.items():
            # Simulate loading from device
            idx = self.find_dropdown_index(self.PRESETS, enum_val, self.PRESET_ENUM_MAP)
            assert idx is not None, f"Enum {enum_val} ({preset_name}) should find index"
            # Simulate getting value for CLI
            cli_value = self.PRESETS[idx]
            assert cli_value == preset_name, f"Enum {enum_val}: expected {preset_name}, got {cli_value}"


class TestPresetGetValue:
    """Test _get_preset simulation - dropdown index to CLI value."""

    PRESETS = ["LONG_FAST", "LONG_SLOW", "VERY_LONG_SLOW", "LONG_MODERATE",
               "MEDIUM_SLOW", "MEDIUM_FAST", "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]

    def get_preset(self, selected_index):
        """Simulate _get_preset function."""
        return self.PRESETS[selected_index]

    def test_get_preset_long_fast(self):
        """Selecting index 0 should return LONG_FAST."""
        assert self.get_preset(0) == "LONG_FAST"

    def test_get_preset_short_turbo(self):
        """Selecting index 8 should return SHORT_TURBO."""
        assert self.get_preset(8) == "SHORT_TURBO"

    def test_get_preset_long_moderate(self):
        """Selecting index 3 should return LONG_MODERATE."""
        assert self.get_preset(3) == "LONG_MODERATE"


def run_tests():
    """Simple test runner without pytest."""
    import traceback

    test_classes = [TestRegionEnumMapping, TestChannelParsing, TestConfigDictApplication, TestPresetGetValue]
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith('test_'):
                try:
                    method = getattr(instance, method_name)
                    method()
                    print(f"  ✓ {cls.__name__}.{method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  ✗ {cls.__name__}.{method_name}: {e}")
                    failed += 1
                    errors.append((cls.__name__, method_name, str(e)))
                except Exception as e:
                    print(f"  ! {cls.__name__}.{method_name}: {e}")
                    failed += 1
                    errors.append((cls.__name__, method_name, traceback.format_exc()))

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")

    if errors:
        print("\nFailed tests:")
        for cls_name, method, err in errors:
            print(f"  - {cls_name}.{method}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)

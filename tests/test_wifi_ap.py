"""
Tests for WiFi AP management module (Feature #2).
"""

import pytest
from unittest.mock import patch, MagicMock


class TestWiFiAPConfig:

    def test_default_config(self):
        from utils.wifi_ap import WiFiAPConfig
        cfg = WiFiAPConfig()
        assert cfg.ssid == "MeshForge"
        assert cfg.interface == "wlan0"
        assert cfg.channel == 6
        assert cfg.gateway_ip == "192.168.50.1"

    def test_validate_valid_config(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(
            ssid="TestNetwork",
            passphrase="securepass123",
            channel=11,
        )
        errors = validate_ap_config(cfg)
        assert errors == []

    def test_validate_empty_ssid(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(ssid="")
        errors = validate_ap_config(cfg)
        assert any("SSID" in e for e in errors)

    def test_validate_long_ssid(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(ssid="A" * 33)
        errors = validate_ap_config(cfg)
        assert any("32" in e for e in errors)

    def test_validate_short_passphrase(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(passphrase="short")
        errors = validate_ap_config(cfg)
        assert any("8" in e for e in errors)

    def test_validate_long_passphrase(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(passphrase="x" * 64)
        errors = validate_ap_config(cfg)
        assert any("63" in e for e in errors)

    def test_validate_open_network(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(passphrase="")  # Open network
        errors = validate_ap_config(cfg)
        assert errors == []

    def test_validate_invalid_channel(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(channel=15)  # Invalid for 2.4GHz
        errors = validate_ap_config(cfg)
        assert any("Channel" in e for e in errors)

    def test_validate_5ghz_channel(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(hw_mode="a", channel=36)
        errors = validate_ap_config(cfg)
        assert errors == []

    def test_validate_invalid_ip(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(gateway_ip="not.an.ip")
        errors = validate_ap_config(cfg)
        assert any("gateway_ip" in e for e in errors)

    def test_validate_invalid_interface_name(self):
        from utils.wifi_ap import WiFiAPConfig, validate_ap_config
        cfg = WiFiAPConfig(interface="wlan 0; rm -rf /")
        errors = validate_ap_config(cfg)
        assert any("interface" in e for e in errors)


class TestHostapdConfig:

    def test_generate_wpa2_config(self):
        from utils.wifi_ap import WiFiAPConfig, _generate_hostapd_conf
        cfg = WiFiAPConfig(ssid="TestAP", passphrase="mypassphrase")
        content = _generate_hostapd_conf(cfg)

        assert "ssid=TestAP" in content
        assert "wpa=2" in content
        assert "wpa_passphrase=mypassphrase" in content
        assert "wpa_key_mgmt=WPA-PSK" in content
        assert "rsn_pairwise=CCMP" in content

    def test_generate_open_config(self):
        from utils.wifi_ap import WiFiAPConfig, _generate_hostapd_conf
        cfg = WiFiAPConfig(ssid="OpenAP", passphrase="")
        content = _generate_hostapd_conf(cfg)

        assert "ssid=OpenAP" in content
        assert "wpa=" not in content
        assert "wpa_passphrase" not in content

    def test_generate_includes_country(self):
        from utils.wifi_ap import WiFiAPConfig, _generate_hostapd_conf
        cfg = WiFiAPConfig(country_code="JP")
        content = _generate_hostapd_conf(cfg)
        assert "country_code=JP" in content


class TestDnsmasqConfig:

    def test_generate_dnsmasq(self):
        from utils.wifi_ap import WiFiAPConfig, _generate_dnsmasq_conf
        cfg = WiFiAPConfig()
        content = _generate_dnsmasq_conf(cfg)

        assert "interface=wlan0" in content
        assert "dhcp-range=192.168.50.10,192.168.50.100" in content
        assert "server=8.8.8.8" in content
        assert "bind-interfaces" in content


class TestWiFiAPManager:

    @patch('utils.wifi_ap.subprocess.run')
    @patch('utils.wifi_ap._sudo_write')
    @patch('utils.wifi_ap.daemon_reload')
    def test_setup_validates_config(self, mock_reload, mock_write, mock_run):
        from utils.wifi_ap import WiFiAPManager, WiFiAPConfig

        mock_write.return_value = (True, "ok")
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        manager = WiFiAPManager()
        bad_config = WiFiAPConfig(ssid="")
        success, msg = manager.setup(bad_config)
        assert success is False
        assert "validation" in msg.lower()

    @patch('utils.wifi_ap.check_service')
    def test_get_status(self, mock_check):
        from utils.wifi_ap import WiFiAPManager

        mock_status = MagicMock()
        mock_status.available = True
        mock_status.message = "running"
        mock_check.return_value = mock_status

        manager = WiFiAPManager()
        status = manager.get_status()

        assert "hostapd" in status
        assert "dnsmasq" in status
        assert status["config"]["ssid"] == "MeshForge"

    @patch('utils.wifi_ap.subprocess.run')
    def test_get_connected_clients_empty(self, mock_run):
        from utils.wifi_ap import WiFiAPManager

        mock_run.return_value = MagicMock(returncode=0, stdout="")
        manager = WiFiAPManager()
        clients = manager.get_connected_clients()
        assert clients == []

"""
Tests for MQTT broker profiles module.

Run: python3 -m pytest tests/test_broker_profiles.py -v
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.utils.broker_profiles import (
    BrokerProfile,
    BrokerType,
    create_private_profile,
    create_public_profile,
    create_custom_profile,
    generate_mosquitto_conf,
    generate_mosquitto_acl,
    generate_password,
    load_profiles,
    save_profiles,
    get_active_profile,
    set_active_profile,
    ensure_default_profiles,
    check_mosquitto_installed,
    get_meshtastic_mqtt_setup_commands,
)


class TestBrokerProfile:
    """Tests for BrokerProfile dataclass."""

    def test_topic_filter(self):
        """Test topic filter generation follows Meshtastic convention."""
        profile = BrokerProfile(
            name="test",
            broker_type="private",
            host="localhost",
            port=1883,
            root_topic="msh/US/2/e",
            channel="LongFast",
        )
        assert profile.topic_filter == "msh/US/2/e/LongFast/#"

    def test_json_topic_filter(self):
        """Test JSON topic filter replaces /e/ with /json/."""
        profile = BrokerProfile(
            name="test",
            broker_type="private",
            host="localhost",
            port=1883,
            root_topic="msh/US/2/e",
            channel="LongFast",
        )
        assert profile.json_topic_filter == "msh/US/2/json/LongFast/#"

    def test_display_name_private(self):
        """Test display name for private broker."""
        profile = BrokerProfile(
            name="mybroker",
            broker_type=BrokerType.PRIVATE.value,
            host="localhost",
            port=1883,
        )
        assert "Private" in profile.display_name
        assert "mybroker" in profile.display_name

    def test_display_name_public(self):
        """Test display name for public broker."""
        profile = BrokerProfile(
            name="pub",
            broker_type=BrokerType.PUBLIC.value,
            host="mqtt.meshtastic.org",
            port=8883,
        )
        assert "Public" in profile.display_name

    def test_display_name_custom(self):
        """Test display name for custom broker."""
        profile = BrokerProfile(
            name="myserver",
            broker_type=BrokerType.CUSTOM.value,
            host="mqtt.example.com",
            port=1883,
        )
        assert "Custom" in profile.display_name
        assert "myserver" in profile.display_name

    def test_to_mqtt_config(self):
        """Test conversion to MQTT subscriber config format."""
        profile = BrokerProfile(
            name="test",
            broker_type="private",
            host="localhost",
            port=1883,
            username="meshforge",
            password="secret",
            root_topic="msh/US/2/e",
            channel="LongFast",
            encryption_key="AQ==",
            use_tls=False,
        )
        config = profile.to_mqtt_config()
        assert config["broker"] == "localhost"
        assert config["port"] == 1883
        assert config["username"] == "meshforge"
        assert config["password"] == "secret"
        assert config["root_topic"] == "msh/US/2/e"
        assert config["channel"] == "LongFast"
        assert config["key"] == "AQ=="
        assert config["use_tls"] is False
        assert config["auto_reconnect"] is True
        # Localhost gets faster reconnect
        assert config["reconnect_delay"] == 2

    def test_to_mqtt_config_remote(self):
        """Test remote broker gets slower reconnect."""
        profile = BrokerProfile(
            name="test",
            broker_type="public",
            host="mqtt.meshtastic.org",
            port=8883,
        )
        config = profile.to_mqtt_config()
        assert config["reconnect_delay"] == 5

    def test_to_intent(self):
        """MQTT-CFG S3: a profile maps straight onto the saved-settings intent
        (the local JSON leg is derived by mqtt_settings, not a topic string)."""
        from monitoring import mqtt_settings
        profile = BrokerProfile(
            name="test",
            broker_type="private",
            host="localhost",
            port=1883,
            username="meshforge",
            password="secret",
            root_topic="msh/US/2/e",
            channel="LongFast",
            json_enabled=True,
        )
        intent = profile.to_intent()
        assert mqtt_settings.validate(intent) == []
        assert (intent["broker"], intent["port"], intent["tls"]) == ("localhost", 1883, "auto")
        assert (intent["username"], intent["password"]) == ("meshforge", "secret")
        cfg = mqtt_settings.subscriber_config(intent)
        assert mqtt_settings.subscriptions(cfg)[0] == "msh/US/2/json/+/#"

    def test_to_intent_keeps_an_explicit_tls_choice(self):
        profile = BrokerProfile(name="t", broker_type="private", host="b",
                                port=8883, use_tls=False)
        assert profile.to_intent()["tls"] is False

    def test_to_meshtastic_cli_args(self):
        """Test generation of meshtastic CLI args."""
        profile = BrokerProfile(
            name="test",
            broker_type="private",
            host="192.168.1.100",
            port=1883,
            username="meshforge",
            password="secret",
            json_enabled=True,
            use_tls=False,
        )
        args = profile.to_meshtastic_cli_args()
        assert "--set" in args
        assert "mqtt.enabled" in args
        assert "true" in args
        assert "mqtt.address" in args
        assert "192.168.1.100" in args
        assert "mqtt.username" in args
        assert "meshforge" in args
        assert "mqtt.password" in args
        assert "secret" in args


class TestProfileFactories:
    """Tests for profile factory functions."""

    def test_create_private_profile(self):
        """Test private broker profile creation."""
        profile = create_private_profile(
            name="test_private",
            channel="TestChannel",
            region="EU_868",
            username="testuser",
            password="testpass",
        )
        assert profile.broker_type == BrokerType.PRIVATE.value
        assert profile.host == "localhost"
        assert profile.port == 1883
        assert profile.username == "testuser"
        assert profile.password == "testpass"
        assert profile.channel == "TestChannel"
        assert profile.region == "EU_868"
        assert profile.root_topic == "msh/EU_868/2/e"
        assert profile.use_tls is False
        assert profile.allow_anonymous is False
        assert profile.acl_enabled is True
        assert profile.json_enabled is True

    def test_create_private_profile_generates_password(self):
        """Test that private profile generates password when not provided."""
        profile = create_private_profile()
        assert profile.password  # Should not be empty
        assert len(profile.password) == 16  # Default length

    def test_create_public_profile(self):
        """Test public broker profile creation."""
        profile = create_public_profile(region="US", channel="LongFast")
        assert profile.broker_type == BrokerType.PUBLIC.value
        assert profile.host == "mqtt.meshtastic.org"
        assert profile.port == 8883
        assert profile.username == "meshdev"
        assert profile.password == "large4cats"
        assert profile.use_tls is True
        assert profile.encryption_key == "AQ=="
        assert profile.uplink_enabled is False  # Read-only
        assert profile.downlink_enabled is False

    def test_create_custom_profile(self):
        """Test custom broker profile creation."""
        profile = create_custom_profile(
            name="community",
            host="mqtt.community.net",
            port=8883,
            username="node1",
            password="secret",
            use_tls=True,
            channel="CommunityMesh",
        )
        assert profile.broker_type == BrokerType.CUSTOM.value
        assert profile.host == "mqtt.community.net"
        assert profile.port == 8883
        assert profile.use_tls is True
        assert profile.channel == "CommunityMesh"

    def test_create_custom_profile_anonymous(self):
        """Test custom profile with anonymous access."""
        profile = create_custom_profile(name="anon", host="10.0.0.1")
        assert profile.allow_anonymous is True


class TestPasswordGeneration:
    """Tests for password generation."""

    def test_generate_password_default_length(self):
        """Test default password length."""
        pw = generate_password()
        assert len(pw) == 16

    def test_generate_password_custom_length(self):
        """Test custom password length."""
        pw = generate_password(32)
        assert len(pw) == 32

    def test_generate_password_is_alphanumeric(self):
        """Test password only contains letters and digits."""
        pw = generate_password(100)
        assert pw.isalnum()

    def test_generate_password_uniqueness(self):
        """Test that generated passwords are different."""
        passwords = {generate_password() for _ in range(10)}
        assert len(passwords) == 10  # All unique


class TestMosquittoConfGeneration:
    """Tests for mosquitto.conf generation."""

    def test_generate_private_conf(self):
        """Test mosquitto.conf generation for private broker."""
        profile = create_private_profile(username="meshforge", password="secret")
        conf = generate_mosquitto_conf(profile)

        assert "listener 1883" in conf
        assert "allow_anonymous false" in conf
        assert "password_file /etc/mosquitto/meshforge_passwd" in conf
        assert "acl_file /etc/mosquitto/meshforge_acl" in conf
        assert "persistence true" in conf
        assert "message_size_limit 4096" in conf

    def test_generate_anonymous_conf(self):
        """Test mosquitto.conf generation for anonymous broker."""
        profile = create_private_profile()
        profile.allow_anonymous = True
        conf = generate_mosquitto_conf(profile)

        assert "allow_anonymous true" in conf
        assert "password_file not needed" in conf

    def test_generate_acl(self):
        """Test ACL file generation."""
        profile = create_private_profile(
            username="meshforge",
            channel="LongFast",
            region="US",
        )
        acl = generate_mosquitto_acl(profile)

        assert "user meshforge" in acl
        assert "topic readwrite msh/#" in acl
        assert "topic read $SYS/#" in acl


class TestProfileStorage:
    """Tests for profile persistence."""

    def test_save_and_load_profiles(self, tmp_path):
        """Test saving and loading profiles."""
        profiles = {
            "private": create_private_profile(password="test123"),
            "public": create_public_profile(),
        }
        profiles["private"].is_active = True

        config_dir = tmp_path / ".config" / "meshforge"

        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=config_dir / "broker_profiles.json"):
            assert save_profiles(profiles) is True
            loaded = load_profiles()

            assert "private" in loaded
            assert "public" in loaded
            assert loaded["private"].host == "localhost"
            assert loaded["private"].is_active is True
            assert loaded["public"].host == "mqtt.meshtastic.org"

    def test_load_missing_file(self, tmp_path):
        """Test loading when file doesn't exist returns empty dict."""
        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=tmp_path / "nonexistent.json"):
            profiles = load_profiles()
            assert profiles == {}

    def test_load_corrupted_file(self, tmp_path):
        """Test loading corrupted JSON returns empty dict."""
        bad_file = tmp_path / "broker_profiles.json"
        bad_file.write_text("not valid json{{{")

        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=bad_file):
            profiles = load_profiles()
            assert profiles == {}

    def test_get_active_profile(self):
        """Test getting active profile."""
        profiles = {
            "one": create_private_profile(password="p1"),
            "two": create_public_profile(),
        }
        profiles["two"].is_active = True

        active = get_active_profile(profiles)
        assert active is not None
        assert active.host == "mqtt.meshtastic.org"

    def test_get_active_profile_none(self):
        """Test getting active profile when none is active."""
        profiles = {
            "one": create_private_profile(password="p1"),
        }
        active = get_active_profile(profiles)
        assert active is None

    def test_set_active_profile(self, tmp_path):
        """Test setting active profile."""
        profiles = {
            "one": create_private_profile(password="p1"),
            "two": create_public_profile(),
        }
        profiles["one"].is_active = True

        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=tmp_path / "broker_profiles.json"):
            save_profiles(profiles)
            assert set_active_profile("two", profiles) is True
            assert profiles["one"].is_active is False
            assert profiles["two"].is_active is True


class TestEnsureDefaultProfiles:
    """Tests for default profile initialization."""

    def test_creates_defaults_when_empty(self, tmp_path):
        """Test that default profiles are created when none exist."""
        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=tmp_path / "broker_profiles.json"):
            profiles = ensure_default_profiles()
            assert "meshforge_private" in profiles
            assert "meshtastic_public" in profiles
            assert profiles["meshforge_private"].broker_type == BrokerType.PRIVATE.value
            assert profiles["meshtastic_public"].broker_type == BrokerType.PUBLIC.value

    def test_preserves_existing_profiles(self, tmp_path):
        """Test that existing profiles are preserved."""
        profiles_path = tmp_path / "broker_profiles.json"

        existing = {"my_custom": create_custom_profile("custom", "example.com")}
        with patch('src.utils.broker_profiles._get_profiles_path',
                   return_value=profiles_path):
            save_profiles(existing)
            loaded = ensure_default_profiles()
            assert "my_custom" in loaded


class TestMosquittoChecks:
    """Tests for mosquitto installation checks."""

    @patch('shutil.which')
    def test_mosquitto_installed(self, mock_which):
        """Test detection of installed mosquitto."""
        mock_which.side_effect = lambda x: f"/usr/sbin/{x}"
        installed, msg = check_mosquitto_installed()
        assert installed is True

    @patch('shutil.which')
    def test_mosquitto_not_installed(self, mock_which):
        """Test detection of missing mosquitto."""
        mock_which.return_value = None
        installed, msg = check_mosquitto_installed()
        assert installed is False
        assert "not installed" in msg.lower()

    @patch('shutil.which')
    def test_mosquitto_partial_install(self, mock_which):
        """Test detection of partial mosquitto installation."""
        def side_effect(name):
            if name == "mosquitto":
                return "/usr/sbin/mosquitto"
            return None
        mock_which.side_effect = side_effect
        installed, msg = check_mosquitto_installed()
        assert installed is False
        assert "mosquitto-clients" in msg


class TestMeshtasticSetupCommands:
    """Tests for radio MQTT setup command generation."""

    def test_private_broker_commands(self):
        """Test command generation for private broker."""
        profile = create_private_profile(
            username="meshforge",
            password="secret123",
        )
        cmds = get_meshtastic_mqtt_setup_commands(profile)

        # Should warn about localhost
        assert "YOUR_SERVER_IP" in cmds
        assert "mqtt.enabled" in cmds
        assert "mqtt.username" in cmds
        assert "meshforge" in cmds
        assert "mqtt.password" in cmds
        # r6 #3 (operator 2026-11-03): masked unless the caller asked
        assert "secret123" not in cmds and "<MQTT_PASSWORD>" in cmds
        assert "secret123" in get_meshtastic_mqtt_setup_commands(
            profile, reveal_password=True)
        assert "uplink_enabled" in cmds
        assert "downlink_enabled" in cmds

    def test_remote_broker_commands(self):
        """Test command generation for remote broker."""
        profile = create_custom_profile(
            name="remote",
            host="mqtt.community.net",
            port=8883,
            username="node1",
            password="pw",
            use_tls=True,
        )
        cmds = get_meshtastic_mqtt_setup_commands(profile)

        # Should use actual hostname, not placeholder
        assert "mqtt.community.net" in cmds
        assert "YOUR_SERVER_IP" not in cmds
        assert "tls_enabled" in cmds
        assert "true" in cmds

    def test_public_broker_commands(self):
        """Test command generation for public broker."""
        profile = create_public_profile()
        cmds = get_meshtastic_mqtt_setup_commands(profile)

        assert "mqtt.meshtastic.org" in cmds


class TestTopicConventions:
    """Tests for Meshtastic MQTT topic convention compliance."""

    def test_topic_format_us_longfast(self):
        """Test standard US/LongFast topic format."""
        profile = create_private_profile(channel="LongFast", region="US")
        assert profile.topic_filter == "msh/US/2/e/LongFast/#"
        assert profile.json_topic_filter == "msh/US/2/json/LongFast/#"

    def test_topic_format_eu(self):
        """Test EU region topic format."""
        profile = create_private_profile(channel="LongFast", region="EU_868")
        assert profile.topic_filter == "msh/EU_868/2/e/LongFast/#"

    def test_topic_format_custom_channel(self):
        """Test custom channel name in topic."""
        profile = create_private_profile(channel="Regional", region="US")
        assert profile.topic_filter == "msh/US/2/e/Regional/#"
        assert profile.json_topic_filter == "msh/US/2/json/Regional/#"

    def test_public_broker_uses_meshtastic_defaults(self):
        """Test public broker uses standard Meshtastic settings."""
        profile = create_public_profile()
        assert profile.host == "mqtt.meshtastic.org"
        assert profile.port == 8883
        assert profile.username == "meshdev"
        assert profile.password == "large4cats"
        assert profile.encryption_key == "AQ=="
        assert profile.use_tls is True

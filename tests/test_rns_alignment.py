"""Tests for utils/rns_alignment.py — RNS config alignment audit + normalize.

Covers:
  - systemd unit parser (User= + ExecStart= drop-in semantics)
  - configdir resolver (--config flag wins; else $HOME/.reticulum by User=)
  - config-file scanner (rpc_key + instance_name + owner)
  - NomadNet unit parser (--rnsconfig)
  - drift analyzer (every reason rendered as a human string)
  - normalize planner (idempotent: aligned state -> empty plan)
"""

from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from utils.rns_alignment import (
    CANONICAL_CONFIGDIR,
    ConfigFileFacts,
    RNSAlignmentState,
    _parse_nomadnet_unit,
    _read_systemd_unit,
    _resolve_rnsd_configdir,
    _scan_config_file,
    analyze_drift,
    plan_normalize,
)


# ---------- systemd unit parser ----------


class TestReadSystemdUnit:
    def test_parses_user_and_exec_start(self, tmp_path):
        unit = tmp_path / "rnsd.service"
        unit.write_text(
            "[Service]\nUser=root\nExecStart=/usr/local/bin/rnsd --service\n"
        )
        result = _read_systemd_unit(str(unit))
        assert result['user'] == 'root'
        assert result['exec_start'] == '/usr/local/bin/rnsd --service'

    def test_dropin_overrides_base_exec_start(self, tmp_path):
        base = tmp_path / "rnsd.service"
        base.write_text("[Service]\nUser=root\nExecStart=/usr/local/bin/rnsd\n")
        dropin = tmp_path / "configdir.conf"
        dropin.write_text(
            "[Service]\nExecStart=\n"
            "ExecStart=/usr/local/bin/rnsd --config /etc/reticulum --service\n"
        )
        result = _read_systemd_unit(str(base), str(dropin))
        # Drop-in's non-empty ExecStart wins
        assert '--config /etc/reticulum' in result['exec_start']

    def test_missing_unit_returns_none(self, tmp_path):
        result = _read_systemd_unit(str(tmp_path / "doesnotexist"))
        assert result == {'user': None, 'exec_start': None}


# ---------- configdir resolver ----------


class TestResolveRnsdConfigdir:
    def test_explicit_config_flag_wins(self):
        cd = _resolve_rnsd_configdir(
            user='root',
            exec_start='/usr/local/bin/rnsd --config /etc/reticulum --service',
        )
        assert cd == Path('/etc/reticulum')

    def test_user_root_no_flag_defaults_to_root_home(self):
        cd = _resolve_rnsd_configdir(user='root', exec_start='/usr/local/bin/rnsd --service')
        assert cd == Path('/root/.reticulum')

    def test_named_user_no_flag_defaults_to_user_home(self):
        cd = _resolve_rnsd_configdir(user='testuser', exec_start='/usr/local/bin/rnsd')
        assert cd == Path('/home/testuser/.reticulum')

    def test_no_user_no_flag_treated_as_root(self):
        cd = _resolve_rnsd_configdir(user=None, exec_start='/usr/local/bin/rnsd')
        assert cd == Path('/root/.reticulum')


# ---------- config-file scanner ----------


class TestScanConfigFile:
    def test_missing_file(self, tmp_path):
        f = _scan_config_file(tmp_path / "missing")
        assert f.exists is False
        assert f.has_rpc_key is False

    def test_detects_valid_rpc_key(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n"
            "rpc_key = a26c750cb357ac9c3391e03dc4b11be43167641b1ae31fa9cfd5168d267d9c7c\n"
        )
        f = _scan_config_file(cfg)
        assert f.exists is True
        assert f.has_rpc_key is True

    def test_rejects_short_rpc_key(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\nrpc_key = abcd\n")
        f = _scan_config_file(cfg)
        # Wrong length, not a valid pin
        assert f.has_rpc_key is False

    def test_picks_up_instance_name(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\ninstance_name = volcano ai rns\n")
        f = _scan_config_file(cfg)
        assert f.has_instance_name is True
        assert f.instance_name == 'volcano ai rns'

    def test_default_instance_name_absent(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\nshared_instance_port = 37428\n")
        f = _scan_config_file(cfg)
        assert f.has_instance_name is False

    def test_detects_indented_rpc_key(self, tmp_path):
        """RNS commonly indents rpc_key inside [reticulum]; must still detect.

        The original ^rpc_key anchor missed indented entries and caused
        the normalize planner to insert a duplicate at column 0, which
        ConfigObj rejects with "Could not parse the configuration".
        Caught the hard way in production on all 4 satellite Pis (2026-04-25).
        """
        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n"
            "  enable_transport = Yes\n"
            "  share_instance = Yes\n"
            "  instance_name = default\n"
            "  rpc_key = " + ("a" * 64) + "\n"
            "[interfaces]\n"
        )
        f = _scan_config_file(cfg)
        assert f.has_rpc_key is True, (
            "indented rpc_key under [reticulum] must still be detected"
        )

    def test_indented_rpc_key_with_short_value_rejected(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  rpc_key = abcd\n")
        f = _scan_config_file(cfg)
        assert f.has_rpc_key is False

    def test_does_not_leak_rpc_key_value(self, tmp_path):
        """The dataclass must NEVER carry the rpc_key value, only its presence."""
        cfg = tmp_path / "config"
        secret = 'a' * 64
        cfg.write_text(f"[reticulum]\nrpc_key = {secret}\n")
        f = _scan_config_file(cfg)
        # Confirm has_rpc_key but the dataclass exposes no field carrying the secret
        assert f.has_rpc_key is True
        for v in f.__dict__.values():
            if isinstance(v, str):
                assert secret not in v


# ---------- NomadNet unit parser ----------


class TestParseNomadnetUnit:
    def test_extracts_rnsconfig(self, tmp_path):
        unit = tmp_path / "nomadnet.service"
        unit.write_text(
            "[Service]\n"
            "ExecStart=/usr/bin/tmux new-session -d -s nomadnet "
            "'/usr/local/bin/nomadnet --rnsconfig /etc/reticulum'\n"
        )
        assert _parse_nomadnet_unit(unit) == Path('/etc/reticulum')

    def test_no_rnsconfig_returns_none(self, tmp_path):
        unit = tmp_path / "nomadnet.service"
        unit.write_text("[Service]\nExecStart=/usr/local/bin/nomadnet\n")
        assert _parse_nomadnet_unit(unit) is None

    def test_missing_unit_returns_none(self, tmp_path):
        assert _parse_nomadnet_unit(tmp_path / "missing") is None


# ---------- drift analyzer ----------


def _aligned_state() -> RNSAlignmentState:
    """Build an in-memory state representing a fully aligned host."""
    return RNSAlignmentState(
        hostname='test',
        rnsd_active=True,
        rnsd_user='root',
        rnsd_exec_start='/usr/local/bin/rnsd --config /etc/reticulum --service',
        rnsd_configdir=CANONICAL_CONFIGDIR,
        etc_config=ConfigFileFacts(
            path=Path('/etc/reticulum/config'),
            exists=True, owner='root:root', has_rpc_key=True,
        ),
        nomadnet_unit_installed=True,
        nomadnet_unit_rnsconfig=CANONICAL_CONFIGDIR,
    )


class TestAnalyzeDrift:
    def test_aligned_state_returns_empty(self):
        assert analyze_drift(_aligned_state()) == []

    def test_rnsd_wrong_configdir_flagged(self):
        s = _aligned_state()
        s.rnsd_configdir = Path('/root/.reticulum')
        s.rnsd_exec_start = '/usr/local/bin/rnsd --service'
        reasons = analyze_drift(s)
        assert any('rnsd uses' in r for r in reasons)

    def test_missing_etc_config_flagged(self):
        s = _aligned_state()
        s.etc_config = ConfigFileFacts(path=Path('/etc/reticulum/config'), exists=False)
        reasons = analyze_drift(s)
        assert any('missing' in r for r in reasons)

    def test_missing_rpc_key_flagged(self):
        s = _aligned_state()
        s.etc_config.has_rpc_key = False
        reasons = analyze_drift(s)
        assert any('rpc_key' in r for r in reasons)

    def test_nomadnet_pointing_wrong_dir_flagged(self):
        s = _aligned_state()
        s.nomadnet_unit_rnsconfig = Path('/home/<user>/.reticulum')
        reasons = analyze_drift(s)
        assert any('NomadNet --rnsconfig' in r for r in reasons)

    def test_root_owned_user_home_flagged(self):
        s = _aligned_state()
        s.user_home_config = ConfigFileFacts(
            path=Path('/home/<user>/.reticulum/config'),
            exists=True, owner='root:root',
        )
        reasons = analyze_drift(s)
        assert any('owned by root' in r for r in reasons)

    def test_mfclient_missing_rpc_key_flagged_when_etc_has_one(self):
        s = _aligned_state()
        s.mfclient_config = ConfigFileFacts(
            path=Path('/tmp/meshforge_rns_client/config'),
            exists=True, owner='wh6gxz:wh6gxz', has_rpc_key=False,
        )
        reasons = analyze_drift(s)
        assert any('meshforge_rns_client' in r for r in reasons)


# ---------- normalize planner ----------


class TestPlanNormalize:
    def test_aligned_state_yields_empty_plan(self):
        assert plan_normalize(_aligned_state()) == []

    def test_promotes_root_home_when_etc_missing(self):
        s = _aligned_state()
        s.etc_config = ConfigFileFacts(path=Path('/etc/reticulum/config'), exists=False)
        s.root_home_config = ConfigFileFacts(
            path=Path('/root/.reticulum/config'),
            exists=True, owner='root:root',
        )
        plan = plan_normalize(s)
        # Must include a cp from /root/.reticulum/config -> /etc/reticulum/config
        assert any(
            a.cmd[:3] == ['sudo', 'cp', '/root/.reticulum/config']
            for a in plan
        )

    def test_pins_rpc_key_when_missing(self):
        s = _aligned_state()
        s.etc_config.has_rpc_key = False
        plan = plan_normalize(s)
        assert any('rpc_key' in a.description for a in plan)

    def test_installs_rnsd_dropin_when_configdir_wrong(self):
        s = _aligned_state()
        s.rnsd_configdir = Path('/root/.reticulum')
        s.rnsd_exec_start = '/usr/local/bin/rnsd --service'
        plan = plan_normalize(s)
        assert any('drop-in' in a.description for a in plan)

    def test_chowns_user_home_when_root_owned(self):
        s = _aligned_state()
        s.user_home_config = ConfigFileFacts(
            path=Path('/home/<user>/.reticulum/config'),
            exists=True, owner='root:root',
        )
        plan = plan_normalize(s)
        chown_actions = [a for a in plan if 'chown' in a.description]
        assert chown_actions, "should plan chown to user"
        assert '<user>' in chown_actions[0].cmd[-1]

    def test_idempotent_on_already_normalized(self):
        """Running plan_normalize twice on canonical state stays empty."""
        s = _aligned_state()
        assert plan_normalize(s) == []
        assert plan_normalize(s) == []  # second invocation, same result

    def test_includes_daemon_reload_after_rnsd_changes(self):
        s = _aligned_state()
        s.rnsd_configdir = Path('/root/.reticulum')
        s.rnsd_exec_start = '/usr/local/bin/rnsd --service'
        plan = plan_normalize(s)
        descriptions = [a.description for a in plan]
        assert any('daemon-reload' in d for d in descriptions)
        assert any('Restart rnsd' in d for d in descriptions)


# ---------- safety: rpc_key script never logs the secret ----------


class TestRpcKeyScriptDoesNotLeakViaDescription:
    def test_action_description_omits_key_value(self):
        import re
        s = _aligned_state()
        s.etc_config.has_rpc_key = False
        plan = plan_normalize(s)
        rpc_action = next(a for a in plan if 'rpc_key' in a.description)
        # Description must not carry the secret (so journald never sees it).
        assert 'rpc_key = ' not in rpc_action.description
        # The 64-hex key lives only inside the bash script body in cmd[-1].
        script_body = rpc_action.cmd[-1]
        match = re.search(r'\b[0-9a-fA-F]{64}\b', script_body)
        assert match is not None, "rpc_key value must be present in the script"


# ---------- Hardening F: gateway preflight rpc_key alignment ----------


class TestCheckGatewayRpcKeyAlignment:
    """Issue #41 silent-AuthError-on-inbound was field-discovered after
    weeks of loss. The gateway must refuse to start when rnsd has a
    pinned rpc_key but the gateway's client config can't authenticate
    with it. These tests cover the four-quadrant matrix:
    rnsd-pinned × client-pinned, plus drift, plus benign cases."""

    KEY_A = "a" * 64
    KEY_B = "b" * 64

    def _write(self, path: Path, key: Optional[str]) -> None:
        if key is None:
            path.write_text("[reticulum]\n# no rpc_key here\n")
        else:
            path.write_text(f"[reticulum]\n  rpc_key = {key}\n")

    def test_aligned_when_both_have_same_key(self, tmp_path, monkeypatch):
        from utils import rns_alignment
        etc = tmp_path / "etc_config"
        client = tmp_path / "client_config"
        self._write(etc, self.KEY_A)
        self._write(client, self.KEY_A)
        monkeypatch.setattr(
            rns_alignment, "_read_rpc_key_value",
            lambda p, sudo=False: self.KEY_A,
        )
        assert rns_alignment.check_gateway_rpc_key_alignment(
            etc_path=etc, client_path=client,
        ) is None

    def test_unpinned_rnsd_returns_none(self, tmp_path, monkeypatch):
        """If rnsd hasn't pinned a key (legacy unpinned mode), the
        client copy is irrelevant — preflight passes."""
        from utils import rns_alignment
        etc = tmp_path / "etc_config"
        client = tmp_path / "client_config"
        self._write(etc, None)
        self._write(client, self.KEY_A)
        monkeypatch.setattr(
            rns_alignment, "_read_rpc_key_value",
            lambda p, sudo=False: None if p == etc else self.KEY_A,
        )
        assert rns_alignment.check_gateway_rpc_key_alignment(
            etc_path=etc, client_path=client,
        ) is None

    def test_first_start_no_client_config_passes(self, tmp_path, monkeypatch):
        """Cold gateway start: client config doesn't exist yet (the
        gateway will write it). Preflight must not block this."""
        from utils import rns_alignment
        etc = tmp_path / "etc_config"
        client = tmp_path / "client_config"   # does not exist
        self._write(etc, self.KEY_A)
        monkeypatch.setattr(
            rns_alignment, "_read_rpc_key_value",
            lambda p, sudo=False: self.KEY_A if p == etc else None,
        )
        assert rns_alignment.check_gateway_rpc_key_alignment(
            etc_path=etc, client_path=client,
        ) is None

    def test_rnsd_pinned_client_missing_key_refuses(self, tmp_path, monkeypatch):
        """Issue #41 shape: rnsd pinned, client config present but no
        key. Refuse-loud."""
        from utils import rns_alignment
        etc = tmp_path / "etc_config"
        client = tmp_path / "client_config"
        self._write(etc, self.KEY_A)
        self._write(client, None)
        monkeypatch.setattr(
            rns_alignment, "_read_rpc_key_value",
            lambda p, sudo=False: self.KEY_A if p == etc else None,
        )
        result = rns_alignment.check_gateway_rpc_key_alignment(
            etc_path=etc, client_path=client,
        )
        assert result is not None
        assert "no readable rpc_key" in result
        assert self.KEY_A not in result   # secret must not leak

    def test_divergent_keys_refuses(self, tmp_path, monkeypatch):
        """Less common: client config has an old/stale key after rnsd
        regenerated identity. Also refuse-loud."""
        from utils import rns_alignment
        etc = tmp_path / "etc_config"
        client = tmp_path / "client_config"
        self._write(etc, self.KEY_A)
        self._write(client, self.KEY_B)
        monkeypatch.setattr(
            rns_alignment, "_read_rpc_key_value",
            lambda p, sudo=False: self.KEY_A if p == etc else self.KEY_B,
        )
        result = rns_alignment.check_gateway_rpc_key_alignment(
            etc_path=etc, client_path=client,
        )
        assert result is not None
        assert "drift" in result.lower() or "different" in result.lower()
        assert self.KEY_A not in result
        assert self.KEY_B not in result


class TestReadRpcKeyValueRejectsMalformed:
    """The reader is the trust boundary for both pin propagation and the
    Hardening F preflight. Reject anything that isn't a strict 64-hex
    rpc_key — never accept a partial/typo'd value as if it were valid."""

    def test_rejects_short_key(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        f = tmp_path / "config"
        f.write_text("rpc_key = abc123\n")
        assert _read_rpc_key_value(f, sudo=False) is None

    def test_rejects_non_hex(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        f = tmp_path / "config"
        f.write_text("rpc_key = " + "z" * 64 + "\n")
        assert _read_rpc_key_value(f, sudo=False) is None

    def test_accepts_indented_under_section(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        f = tmp_path / "config"
        f.write_text("[reticulum]\n  rpc_key = " + "a" * 64 + "\n")
        assert _read_rpc_key_value(f, sudo=False) == "a" * 64

    def test_normalizes_to_lowercase(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        f = tmp_path / "config"
        f.write_text("rpc_key = " + "A" * 64 + "\n")
        assert _read_rpc_key_value(f, sudo=False) == "a" * 64

    def test_skips_commented_line(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        f = tmp_path / "config"
        f.write_text("# rpc_key = " + "a" * 64 + "\n")
        assert _read_rpc_key_value(f, sudo=False) is None

    def test_returns_none_on_missing_file(self, tmp_path):
        from utils.rns_alignment import _read_rpc_key_value
        assert _read_rpc_key_value(tmp_path / "missing", sudo=False) is None

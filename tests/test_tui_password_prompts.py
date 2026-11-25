"""A TUI password prompt never echoes the secret (deferred tui-masked-password-input).

Raised in every F8 review round (11-01, rounds 1-5): the MQTT password
prompts used ``inputbox``, which ECHOES what is typed — into the terminal,
tmux scrollback and a screen share. whiptail and dialog both have
``--passwordbox``; the backend had none.

Pinned here: the backend primitive (masked, the secret never in argv, its
spaces kept), and a GATE over every TUI source file — an input call (the
primitive or a wrapper such as ``_ask``) whose title or prompt asks for a
secret fails this test. It reads string literals only: a prompt built from
variables is not seen (a known limit, not a guarantee).
"""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from backend import DialogBackend, DialogError  # noqa: E402

TUI = Path(__file__).resolve().parent.parent / "src" / "launcher_tui"


def _backend(result):
    be = DialogBackend.__new__(DialogBackend)
    be.backend = 'whiptail'
    be.width = 78
    be.height = 22
    be.list_height = 14
    be._status_bar = None
    be.calls = []

    def fake_run(args, timeout=None, **kw):
        be.calls.append((args, kw))
        return result

    be._run = fake_run
    return be


def test_passwordbox_is_masked_and_never_puts_a_secret_in_argv():
    be = _backend((0, "s3cret"))
    assert be.passwordbox("MQTT Password", "Enter MQTT password:") == "s3cret"
    args, _ = be.calls[0]
    i = args.index('--passwordbox')
    # text, height, width — and NOTHING after: an init value would ride argv (ps)
    assert len(args) == i + 4, args
    assert '--inputbox' not in args


def test_passwordbox_keeps_the_secrets_own_spaces():
    """A password may begin or end with a space; strip() would change it."""
    be = _backend((0, " pass word "))
    assert be.passwordbox("P", "p:") == " pass word "
    _, kw = be.calls[0]
    assert kw.get("strip") is False


def test_passwordbox_cancel_is_none_and_a_dead_tool_raises():
    assert _backend((1, "")).passwordbox("P", "p:") is None
    with pytest.raises(DialogError):
        _backend((-1, "")).passwordbox("P", "p:")


def test_passwordbox_tool_error_is_not_a_cancel():
    """Review r1 nit: dialog's own failure (255 + a reason) is not the
    operator's Esc (255, empty) — one discriminator, ``_is_tool_error``."""
    assert _backend((255, "")).passwordbox("P", "p:") is None
    with pytest.raises(DialogError):
        _backend((255, "Error: Can't make new window")).passwordbox("P", "p:")


#: what a prompt asking for a SECRET says (r1: PSK / API key / private key too)
SECRET_WORDS = re.compile(r"password|passphrase|passwd|secret|\bpsk\b(?!\s*reporter)|api.?key|private.?key", re.I)  # "PSK Reporter" = the ham spotting service, not a key
#: an input-ish call: the primitive and the wrappers that forward to it
#: (r1: broker's ``_ask`` echoed the setup password past the first gate)
INPUT_CALL = re.compile(r"inputbox|^_?ask|prompt", re.I)
#: reviewed exceptions — (file, prompt substring): why it is not a secret
ALLOWED = {
    ("handlers/fleet_backup.py", "Path to SSH private key"): "asks for a file PATH, not the key",
}


def _strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if isinstance(node, ast.BinOp):                     # "a" "b" / "a" + "b"
        return _strings(node.left) + _strings(node.right)
    return []


def _prompt_text(call):
    parts = [s for a in call.args[:2] for s in _strings(a)]
    parts += [s for k in call.keywords if k.arg in ("title", "text")
              for s in _strings(k.value)]
    return " ".join(parts)


def _offenders(root):
    """(rel:line, prompt) for every echoing input call that asks for a secret
    under ``root``, plus the number of files scanned (a vacuous scan is a
    finding, never a pass)."""
    found, scanned = [], 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name = node.func.attr
            if name == "passwordbox" or not INPUT_CALL.search(name):
                continue
            text = _prompt_text(node)
            if not SECRET_WORDS.search(text):
                continue
            if any(rel == f and sub in text for f, sub in ALLOWED):
                continue
            found.append((f"{rel}:{node.lineno}", text[:60]))
    return found, scanned


def test_no_tui_prompt_echoes_a_secret():
    """THE GATE: an echoing prompt for a secret, anywhere in the TUI — the
    primitive or a wrapper, positional or keyword."""
    found, scanned = _offenders(TUI)
    assert scanned > 50, f"scanned only {scanned} files — wrong root?"
    assert not found, f"echoing secret prompts (use dialog.passwordbox): {found}"


def test_the_gate_catches_every_planted_shape(tmp_path):
    """A gate that never failed is not evidence: run the REAL scan over a
    planted tree carrying each shape r1 found past the first gate."""
    (tmp_path / "h.py").write_text(
        'a = self.ctx.dialog.inputbox("MQTT Password", "Enter MQTT password:")\n'
        'b = self._ask("MQTT Password", "Password for broker authentication:\\n\\n" "x")\n'
        'c = self.ctx.dialog.inputbox(title="Custom PSK", text="Enter a key")\n'
        'd = self.ctx.dialog.inputbox("Key", "Paste your API key:")\n'
        'ok1 = self.ctx.dialog.passwordbox("MQTT Password", "Enter MQTT password:")\n'
        'ok2 = self.ctx.dialog.msgbox("Password", "Password: set")\n'
        'ok3 = self.ctx.dialog.inputbox("PSK Reporter", "Your callsign:")\n')
    found, scanned = _offenders(tmp_path)
    assert scanned == 1
    assert [f.split(":")[1] for f, _ in found] == ["1", "2", "3", "4"], found


def test_the_allowlist_is_live():
    """Every reviewed exception still matches a real prompt (a stale entry
    would silently exempt whatever later lands under that text)."""
    for (f, sub), why in ALLOWED.items():
        assert sub in (TUI / f).read_text(encoding="utf-8"), (f, sub, why)


# ---- review r2 (Fable, 11-24) ----------------------------------------------
from backend import blank_secret  # noqa: E402


@pytest.mark.parametrize("typed,want", [(None, None), ("", ""), ("   ", ""), ("\t", ""),
                                        (" a b ", " a b "), ("pw", "pw")])
def test_blank_secret_is_the_one_blank_rule(typed, want):
    assert blank_secret(typed) == want


#: every "blank = none" masked prompt (r2: 5 of 6 had their own copy, unpinned)
BLANK_IS_NONE_SITES = {"handlers/_mqtt_config_form.py": 2, "handlers/broker.py": 2,
                       "handlers/gateway.py": 1, "handlers/meshtasticd_mqtt.py": 1}


@pytest.mark.parametrize("rel,n", sorted(BLANK_IS_NONE_SITES.items()))
def test_each_blank_is_none_prompt_uses_the_one_rule(rel, n):
    src = (TUI / rel).read_text(encoding="utf-8")
    assert src.count("blank_secret(") == n, rel
    assert "not typed.strip()" not in src and "not password.strip()" not in src, rel


def test_dialog_shows_stars_like_whiptail():
    """dialog's passwordbox echoes NOTHING without --insecure (no feedback
    a key landed); whiptail shows '*' per key (measured r2 on a pty)."""
    be = _backend((0, "x"))
    be.backend = 'dialog'
    be.passwordbox("P", "p:")
    args, _ = be.calls[0]
    assert args[0] == '--insecure' and args.index('--passwordbox') > 0, args
    wt = _backend((0, "x"))
    wt.passwordbox("P", "p:")
    assert '--insecure' not in wt.calls[0][0]


# ---- argv residual: ONE posture line, not cured (operator 11-24: single
# operator on every box; "one posture line" after 3 review rounds of per-site
# notes each found another silent site). A cure via temp/config files risks
# root-owned files under the sudo TUI. Nothing falls silent: the header of
# every menu where an MQTT password is set or used SAYS it, and no password
# is ever echoed on screen from a child's output or error.
import subprocess  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from backend import ARGV_SECRET_POSTURE  # noqa: E402
from utils.secret_scrub import redact_password_lines, scrub_secret  # noqa: E402
from handler_test_utils import make_handler_context  # noqa: E402


def test_the_posture_names_the_tools_and_the_reader():
    for word in ("meshtastic", "mosquitto", "command line", "ps"):
        assert word in ARGV_SECRET_POSTURE


def _first_menu_text(handler, call):
    handler.set_context(make_handler_context())
    handler.ctx.dialog._menu_returns = [None]          # Esc: leave at once
    call(handler)
    (_, (title, text, _choices), _kw), = [c for c in handler.ctx.dialog.calls if c[0] == "menu"][:1]
    return text


def test_broker_menu_header_says_it():
    from handlers.broker import BrokerHandler
    # r1 nit: pin every live-state probe the menu makes (store, mosquitto)
    with patch("handlers.broker.load_profiles", return_value={}), \
            patch("handlers.broker.profile_store_status",
                  return_value=MagicMock(state="ok", skipped=[])), \
            patch("handlers.broker.check_mosquitto_installed", return_value=(True, "ok")), \
            patch("handlers.broker.check_mosquitto_running", return_value=(True, "ok")):
        text = _first_menu_text(BrokerHandler(), lambda h: h._broker_menu())
    assert ARGV_SECRET_POSTURE in text


def test_radio_mqtt_menu_header_says_it():
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler
    text = _first_menu_text(MeshtasticdDeviceMQTTHandler(), lambda h: h._mqtt_device_config())
    assert ARGV_SECRET_POSTURE in text


def test_gateway_bridge_menu_does_not_claim_it():
    """r1 nit: the password set there is used by the daemon's MQTT client,
    not a CLI — the line would be untrue there."""
    from handlers.gateway import GatewayHandler
    text = _first_menu_text(GatewayHandler(), lambda h: h._config_mqtt_bridge(MagicMock()))
    assert ARGV_SECRET_POSTURE not in text


def _rx_until_publish(password, yes):
    """Drive Test Gateway RX past its prechecks up to the publish."""
    import handlers.test_gateway_rx as rx
    h = rx.TestGatewayRxHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = list(yes)
    cfg = MagicMock(enabled=True, bridge_mode="mqtt_bridge")
    cfg.rns.default_lxmf_destination = "ab" * 16
    cfg.mqtt_bridge.username = "u" if password else ""
    cfg.mqtt_bridge.password = password
    ran = []
    with patch.object(rx, "_HAS_GATEWAY_CONFIG", True), \
            patch.object(rx, "_GatewayConfig", MagicMock(load=MagicMock(return_value=cfg))), \
            patch.object(rx, "check_service", return_value=MagicMock(available=True)), \
            patch.object(rx, "check_rns_shared_instance", return_value=True), \
            patch.object(rx.TestGatewayRxHandler, "_publish_probe",
                         side_effect=lambda *a: ran.append(a) or (False, "x")):
        h._run_rx_test()
    return h.ctx.dialog.calls, ran


def test_gateway_rx_says_it_before_publishing_and_no_means_nothing_runs():
    """r1 IMPORTANT: a top-level item under none of the posture menus ran
    mosquitto_pub -P silently."""
    calls, ran = _rx_until_publish("s3cret", yes=[False])
    assert any(c[0] == "yesno" and ARGV_SECRET_POSTURE in c[1][1] for c in calls), calls
    assert ran == []
    calls, ran = _rx_until_publish("s3cret", yes=[True])
    assert len(ran) == 1


def test_gateway_rx_without_a_password_asks_nothing_extra():
    calls, ran = _rx_until_publish("", yes=[])
    assert not any(ARGV_SECRET_POSTURE in str(c[1]) for c in calls)
    assert len(ran) == 1


@pytest.mark.parametrize("text,secrets,want", [
    ("Set mqtt.password to s3cret", ("s3cret",), "Set mqtt.password to <password>"),
    ("no secret here", ("s3cret",), "no secret here"),
    ("x", ("", None), "x"),
    (None, ("s3cret",), ""),
])
def test_scrub_secret(text, secrets, want):
    assert scrub_secret(text, *secrets) == want


def _apply_action(password):
    from handlers.broker import BrokerHandler
    h = BrokerHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    prof = MagicMock(password=password)
    prof.name = "p1"
    seen = {}
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch("utils.broker_profiles.host_needs_lan_ip", return_value=False), \
            patch("utils.broker_profiles.get_meshtastic_mqtt_setup_argv",
                  return_value=[["meshtastic", "--host", "localhost", "--set", "mqtt.password", password]]), \
            patch("remediation.propose_remediation",
                  side_effect=lambda ctx, title, text, actions: seen.update(text=text, act=actions[0])):
        h._apply_mqtt_to_radio(prof)
    return seen


@pytest.mark.parametrize("outcome", ["timeout", "rejected_echo"])
def test_apply_failure_never_shows_the_password(outcome):
    """r2: str(TimeoutExpired) embeds the argv. r3: the CLI echoes
    'Set mqtt.password to <value>' on stdout, shown in the failure box."""
    import handlers.broker as b
    seen = _apply_action("s3cret")
    got = []

    def run(cmd, **kw):
        got.append(kw["timeout"])
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return MagicMock(returncode=1, stderr="", stdout="Set mqtt.password to s3cret\nError")
    with patch("subprocess.run", side_effect=run):
        ok, msg = seen["act"].apply()
    assert ok is False and "s3cret" not in msg, msg
    assert got == [b._APPLY_CLI_TIMEOUT_S]
    if outcome == "timeout":
        assert f"{b._APPLY_CLI_TIMEOUT_S} s" in msg


def test_a_cli_timeout_never_prints_the_password(capsys):
    """r2 CONFIRMED: the screen showed `'mqtt.password', 's3cret'` in clear."""
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler, _CREDS_CLI_TIMEOUT_S
    h = MeshtasticdDeviceMQTTHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    ctx.dialog._inputbox_returns = ["alice", "s3cret"]
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch("handlers.meshtasticd_mqtt.clear_screen"), \
            patch("handlers.meshtasticd_mqtt.subprocess.run",
                  side_effect=lambda cmd, **kw: (_ for _ in ()).throw(
                      subprocess.TimeoutExpired(cmd, kw["timeout"]))), \
            patch("builtins.input", return_value=""):
        h._mqtt_set_credentials()
    out = capsys.readouterr().out
    assert "s3cret" not in out and f"Timed out after {_CREDS_CLI_TIMEOUT_S} s" in out, out


def test_a_cli_failure_stderr_is_scrubbed(capsys):
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler
    h = MeshtasticdDeviceMQTTHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    ctx.dialog._inputbox_returns = ["alice", "s3cret"]
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch("handlers.meshtasticd_mqtt.clear_screen"), \
            patch("handlers.meshtasticd_mqtt.subprocess.run",
                  return_value=MagicMock(returncode=1, stderr="bad value s3cret")), \
            patch("builtins.input", return_value=""):
        h._mqtt_set_credentials()
    out = capsys.readouterr().out
    assert "s3cret" not in out and "<password>" in out, out


@pytest.mark.parametrize("mode", ["timeout", "failed"])
def test_broker_connection_test_never_shows_the_password(mode):
    from handlers.broker import BrokerHandler, _MOSQ_PUB_TIMEOUT_S
    h = BrokerHandler()
    h.set_context(make_handler_context())
    p = MagicMock(host="h", port=1883, username="u", password="s3cret")
    eff = (subprocess.TimeoutExpired(["mosquitto_pub", "-P", "s3cret"], 10) if mode == "timeout"
           else None)
    ret = MagicMock(returncode=5, stderr="auth failed for s3cret")
    with patch("handlers.broker.load_profiles", return_value={}), \
            patch("handlers.broker.get_active_profile", return_value=p), \
            patch("subprocess.run", side_effect=eff, return_value=ret) as run:
        h._test_mosquitto_connection()
    assert "s3cret" not in repr(h.ctx.dialog.calls)
    assert run.call_args.kwargs["timeout"] == _MOSQ_PUB_TIMEOUT_S
    if mode == "timeout":
        boxes = {c[1][0]: c[1][1] for c in h.ctx.dialog.calls if c[0] == "msgbox"}
        assert f"after {_MOSQ_PUB_TIMEOUT_S} s" in boxes["Connection Timeout"]


def test_view_settings_never_prints_the_radios_stored_password(capsys):
    """r1 pre_existing: `meshtastic --get mqtt` prints mqtt.password."""
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler
    h = MeshtasticdDeviceMQTTHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    out_text = "mqtt.address: mqtt.example\nmqtt.password: s3cret\nmqtt.username: u"
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch("handlers.meshtasticd_mqtt.clear_screen"), \
            patch("handlers.meshtasticd_mqtt.subprocess.run",
                  return_value=MagicMock(returncode=0, stdout=out_text, stderr="")), \
            patch("builtins.input", return_value=""):
        h._mqtt_view_settings()
    out = capsys.readouterr().out
    assert "s3cret" not in out and "mqtt.password: <hidden>" in out and "mqtt.example" in out


@pytest.mark.parametrize("text,want", [
    ("password: s3cret", "password: <hidden>"),
    ("  mqtt.password = a b c", "  mqtt.password = <hidden>"),
    ("username: u", "username: u"),
    (None, ""),
])
def test_redact_password_lines(text, want):
    assert redact_password_lines(text) == want


def test_mosquitto_passwd_failure_text_is_scrubbed(tmp_path):
    """r1 pre_existing: the private-broker install shows mosquitto_passwd's
    stderr, which can echo its argv."""
    from utils import broker_profiles as bp
    prof = bp.create_private_profile(name="p", channel="LongFast", region="US",
                                     username="u", password="s3cret")
    fail = MagicMock(returncode=1, stderr="usage: mosquitto_passwd -b file u s3cret")
    with patch("utils.broker_profiles.subprocess.run", return_value=fail), \
            patch("utils.broker_profiles.atomic_write_text"), \
            patch("utils.broker_profiles.os.chmod"), \
            patch("utils.broker_profiles.os.geteuid", return_value=0, create=True), \
            patch("utils.broker_profiles.Path.mkdir"), \
            patch("utils.broker_profiles.Path.exists", return_value=True):
        out = bp.install_mosquitto_config(prof)
    assert "s3cret" not in repr(out), out


# ---- Fable r2 pre_existing (operator 11-24: "i agree with your
# recommendations"): a stored password nobody re-applies is not kept; any
# that an older version stored is never shown; --info JSON is redacted.
from utils.secret_scrub import is_secret_key  # noqa: E402


@pytest.mark.parametrize("text,want", [
    ('    "password": "s3cret",', '    "password": "<hidden>",'),
    ('"mqtt.password":"a\\"b"', '"mqtt.password":"<hidden>"'),   # an escaped quote inside
    ("{'mqtt': {'password': 's3cret', 'x': 1}}", "{'mqtt': {'password': '<hidden>', 'x': 1}}"),
    ('"username": "u"', '"username": "u"'),
])
def test_redact_handles_the_info_json_form(text, want):
    assert redact_password_lines(text) == want


@pytest.mark.parametrize("key,secret", [("password", True), ("mqtt_password", True),
                                        ("psk", True), ("channel_psk", True),
                                        ("private_key", True), ("adminKey", True),
                                        ("username", False), ("address", False)])
def test_is_secret_key(key, secret):
    assert is_secret_key(key) is secret


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "device_config.yaml"
    with patch("utils.device_config_store._get_config_path", return_value=path), \
            patch("utils.device_config_store._fix_file_ownership"):
        yield path


def test_forget_removes_only_what_is_there_and_writes_only_then(store):
    from utils import device_config_store as dcs
    assert dcs.forget_device_settings("mqtt", ["password"]) == []
    assert not store.exists()                                  # nothing -> no write
    dcs.save_device_settings({"mqtt": {"enabled": True, "password": "s3cret"}})
    assert dcs.forget_device_settings("mqtt", ["username", "password"]) == ["password"]
    assert dcs.load_device_config() == {"mqtt": {"enabled": True}}


def test_forget_never_overwrites_an_unreadable_store(store):
    """load_device_config reads a damaged file as {} — writing then would
    destroy it (hfm #1)."""
    from utils import device_config_store as dcs
    store.write_text("mqtt: [unclosed\n")
    before = store.read_bytes()
    with pytest.raises(dcs.DeviceConfigUnreadable):
        dcs.forget_device_settings("mqtt", ["password"])
    assert store.read_bytes() == before


def test_a_saved_secret_is_never_logged(store, caplog):
    from utils import device_config_store as dcs
    with caplog.at_level("INFO"):
        dcs.save_device_setting("mqtt", "password", "s3cret")
    assert "s3cret" not in caplog.text and "<hidden>" in caplog.text


#: every key apply_saved_config REPLAYS (device_config_store) — a purge of
#: secret keys must never remove one of these
REPLAYED = {"lora": ["modem_preset", "channel_num", "region", "hop_limit"],
            "owner": ["long_name", "short_name"], "mqtt": ["enabled", "address"]}


def test_no_replayed_key_is_a_secret_key():
    assert not [k for ks in REPLAYED.values() for k in ks if is_secret_key(k)]


def test_forget_raises_on_an_unreadable_store_never_says_nothing(store):
    """r1 IMPORTANT: a damaged file read as {} -> 'no copy' while the old
    password stayed in it."""
    from utils import device_config_store as dcs
    store.write_text("mqtt: [unclosed\n")
    with pytest.raises(dcs.DeviceConfigUnreadable):
        dcs.forget_device_settings("mqtt", ["password"])
    with pytest.raises(dcs.DeviceConfigUnreadable):
        dcs.forget_secret_settings()


def test_forget_secret_settings_keeps_every_replayed_key(store):
    from utils import device_config_store as dcs
    keep = {sec: {k: "v" for k in ks} for sec, ks in REPLAYED.items()}
    dcs.save_device_settings(keep)
    dcs.save_device_settings({"mqtt": {"password": "s3cret"}, "x": {"channel_psk": "k"}})
    assert sorted(dcs.forget_secret_settings()) == ["mqtt.password", "x.channel_psk"]
    assert dcs.load_device_config() == keep


def _creds_ok(store):
    from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler
    h = MeshtasticdDeviceMQTTHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    ctx.dialog._inputbox_returns = ["alice", "s3cret"]
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch("handlers.meshtasticd_mqtt.clear_screen"), \
            patch("handlers.meshtasticd_mqtt.subprocess.run",
                  return_value=MagicMock(returncode=0, stderr="")), \
            patch("builtins.input", return_value=""):
        h._mqtt_set_credentials()
    return {c[1][0]: c[1][1] for c in ctx.dialog.calls if c[0] == "msgbox"}


def test_credentials_are_not_stored_and_the_screen_says_so(store):
    """'Saved for restart persistence' was FALSE: the replay sets only
    mqtt.enabled/address, so the plaintext copy served no one."""
    boxes = _creds_ok(store)
    assert "s3cret" not in (store.read_text() if store.exists() else "")
    assert "stores no copy" in boxes["Success"]
    assert "restart persistence" not in boxes["Success"]


def test_an_older_stored_copy_is_removed_and_said(store):
    from utils import device_config_store as dcs
    dcs.save_device_settings({"mqtt": {"address": "m.example", "username": "alice",
                                       "password": "old"}})
    boxes = _creds_ok(store)
    assert dcs.load_device_config() == {"mqtt": {"address": "m.example"}}
    assert "removed the copy" in boxes["Success"]


def _restart(store):
    from handlers.meshtasticd_config import MeshtasticdConfigHandler
    h = MeshtasticdConfigHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = [True, False]                # restart yes, re-apply no
    with patch("handlers.meshtasticd_config.apply_config_and_restart",
               return_value=(True, "ok")):
        h._restart_meshtasticd()
    return repr(h.ctx.dialog.calls)


def test_restart_removes_a_stored_password_and_says_so(store):
    """r1 IMPORTANT: an older copy stayed on disk unless Set Credentials
    re-ran; the restart flow is the store's only reader — it purges there."""
    from utils import device_config_store as dcs
    dcs.save_device_settings({"mqtt": {"address": "m.example", "password": "s3cret"}})
    shown = _restart(store)
    assert "s3cret" not in shown and "s3cret" not in store.read_text()
    assert "Removed stored secret(s)" in shown and "mqtt.password" in shown
    assert "m.example" in shown


def test_restart_with_an_unreadable_store_warns(store):
    store.write_text("mqtt: [unclosed\n")
    shown = _restart(store)
    assert "could not be" in shown and "read" in shown


def test_restart_summary_survives_odd_sections_and_hides_nested(store):
    """r1 pre_existing: a scalar section crashed .items(); a nested dict
    printed its repr (a password one level down)."""
    store.write_text("mqtt: on\nlora:\n  modem_preset: LONG_FAST\n  creds: {pw: x9}\n")
    shown = _restart(store)
    assert "not a settings list" in shown and "(nested" in shown and "x9" not in shown


def test_radio_info_output_hides_the_json_password(capsys):
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    h.set_context(make_handler_context())
    info = 'Module preferences: {\n  "mqtt": {\n    "password": "s3cret",\n    "address": "m"\n  }\n}'
    with patch("handlers.radio_menu.clear_screen"), \
            patch("handlers.radio_menu.subprocess.run",
                  return_value=MagicMock(returncode=0, stdout=info, stderr="")), \
            patch("builtins.input", return_value=""):
        h._radio_run(["meshtastic", "--host", "localhost", "--info"], "Radio Info", redact=True)
    out = capsys.readouterr().out
    assert "s3cret" not in out and '"password": "<hidden>"' in out and '"address": "m"' in out


def test_radio_info_timeout_withholds_partial_output(capsys):
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    h.set_context(make_handler_context())
    e = subprocess.TimeoutExpired(["meshtastic"], 30, output=b'"password": "s3cret\\')   # cut after a backslash (r3)
    with patch("handlers.radio_menu.clear_screen"), \
            patch("handlers.radio_menu.subprocess.run", side_effect=e), \
            patch("builtins.input", return_value=""):
        h._radio_run(["meshtastic", "--info"], "Radio Info", redact=True)
    out = capsys.readouterr().out
    assert "s3cret" not in out and "withheld" in out and "timed out" in out


# ---- Fable r2 (stored secrets) ---------------------------------------------
def test_a_crafted_name_never_blanks_the_rest_of_the_output():
    """r3 IMPORTANT (CONFIRMED): an end-of-text match let a node longName
    `'password':'` hide every field after it."""
    import json
    doc = json.dumps({"nodes": {"!a": {"user": {"longName": "'password':'"}}},
                      "prefs": {"KEEP": "KEEP1"}}, indent=2)
    assert "KEEP1" in redact_password_lines(doc)


def test_wifi_psk_is_redacted_like_a_password():
    assert redact_password_lines('"wifiPsk": "homewifi",') == '"wifiPsk": "<hidden>",'


def test_radio_info_menu_choice_runs_redacted():
    """r2: the wiring was unpinned (tests called _radio_run directly)."""
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    ctx.dialog._menu_returns = ["info", None]
    with patch.object(type(ctx), "get_meshtastic_cli", return_value="meshtastic"), \
            patch.object(RadioMenuHandler, "_radio_run") as run:
        h._radio_menu()
    (args, kw), = [(c.args, c.kwargs) for c in run.call_args_list]
    assert "--info" in args[0] and kw.get("redact") is True


@pytest.mark.parametrize("rc", [-9, 1])
def test_radio_info_cut_off_by_a_dying_cli_is_withheld(capsys, rc):
    """r4 IMPORTANT (CONFIRMED by a real SIGKILL drill): no timeout, a
    non-zero exit, stdout cut inside the value — the prefix was printed."""
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    h.set_context(make_handler_context())
    with patch("handlers.radio_menu.clear_screen"), \
            patch("handlers.radio_menu.subprocess.run",
                  return_value=MagicMock(returncode=rc, stdout='{"password": "s3cr',
                                         stderr='password: errsecret')), \
            patch("builtins.input", return_value=""):
        h._radio_run(["meshtastic", "--info"], "Radio Info", redact=True)
    out = capsys.readouterr().out
    assert "s3cr" not in out and "withheld" in out
    assert "errsecret" not in out and "password: <hidden>" in out   # r4 nit: stderr pinned


def test_forget_on_a_list_topped_store_raises_the_named_error(store):
    """r4 nit: without the non-mapping raise, forget_* hit AttributeError,
    which the handlers do not catch — the warning was lost."""
    from utils import device_config_store as dcs
    store.write_text("- a\n- b\n")
    with pytest.raises(dcs.DeviceConfigUnreadable):
        dcs.forget_secret_settings()
    with pytest.raises(dcs.DeviceConfigUnreadable):
        dcs.forget_device_settings("mqtt", ["password"])


def test_radio_info_timeout_keeps_stderr_redacted(capsys):
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    h.set_context(make_handler_context())
    e = subprocess.TimeoutExpired(["meshtastic"], 30, output=None,
                                  stderr=b"Traceback: connect failed\npassword: errsecret")
    with patch("handlers.radio_menu.clear_screen"), \
            patch("handlers.radio_menu.subprocess.run", side_effect=e), \
            patch("builtins.input", return_value=""):
        h._radio_run(["meshtastic", "--info"], "Radio Info", redact=True)
    out = capsys.readouterr().out
    assert "connect failed" in out and "errsecret" not in out   # r4 nit: redacted, pinned


@pytest.mark.parametrize("damage", ["lora:\n  modem_preset: LONG_FAST\nmqtt: [unclosed\n",
                                    "- a\n- b\n"], ids=["unparseable", "list-topped"])
@pytest.mark.parametrize("save", ["one", "many"])
def test_a_save_never_overwrites_a_damaged_store(store, save, damage):
    """r2 pre_existing (CONFIRMED data loss): a {} read from a damaged file,
    then a save, wiped lora/owner settings and reported success."""
    from utils import device_config_store as dcs
    store.write_text(damage)                 # r3: the non-mapping raise is pinned too
    before = store.read_bytes()
    ok = (dcs.save_device_setting("mqtt", "address", "m") if save == "one"
          else dcs.save_device_settings({"mqtt": {"address": "m"}}))
    assert ok is False and store.read_bytes() == before


def test_the_success_box_claims_only_what_exit_0_shows(store):
    """r2 nit: no readback, so never 'the radio keeps them'."""
    boxes = _creds_ok(store)
    assert "exited 0" in boxes["Success"] and "keeps them" not in boxes["Success"]

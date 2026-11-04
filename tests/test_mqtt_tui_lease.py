"""F8 v2 — the MQTT settings editor holds an EDIT LEASE (grill 2026-11-03).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § F8 v2. Each
test drives the real TUI handler against a real tmp config dir — no mocked
save — because the parked F8 attempt's tests mocked the layer that broke.

The r4/r5 reviewer scratch scenarios (parked bundle) are adapted here (V3):
their "B saves while A is open" becomes either a second session that is
refused (it cannot take the lease) or a LEASE-LESS write (a hand edit), and
each keeps its assertion — the other writer's value is never lost unasked.
"""
import json
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from monitoring import mqtt_settings as ms  # noqa: E402
from utils.mqtt_defaults import MESHTASTIC_PUBLIC_PASSWORD  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402

A = {**ms.defaults(), "broker": "mqtt.a.example", "port": 1883, "tls": "auto",
     "username": "alice", "password": "s3cret", "root_topic": "msh/HI/2/e",
     "channel": "Regional", "auto_start": True}


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "meshforge"
    d.mkdir()
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=d), \
            patch("handlers.mqtt.MQTTHandler._detect_local_channel", return_value=None):
        yield d


def _seed(d, intent=A):
    r = ms.acquire_lease(d, user="seed", tty="-")
    with r.lease as lease:
        assert ms.save(d, dict(intent), W, lease).ok


def _disk(d):
    return json.loads((d / ms.FILENAME).read_text())


def _handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    h = MQTTHandler()
    h.set_context(ctx)
    return h, ctx


def _broker():
    from handlers.broker import BrokerHandler
    ctx = make_handler_context()
    h = BrokerHandler()
    h.set_context(ctx)
    return h, ctx


def _calls(ctx, kind, title=None):
    return [c for c in ctx.dialog.calls
            if c[0] == kind and (title is None or c[1][0] == title)]


def _hand_edit(d, **change):
    """A writer that skipped the lease (vim, a script) — raw bytes."""
    (d / ms.FILENAME).write_text(json.dumps({"schema_version": 1, **A, **change}))


def _fire_at(ctx, title, tag, action):
    """Run `action` the first time menu `title` returns `tag`."""
    real = ctx.dialog.menu
    fired = []

    def menu(t, text, choices, **kw):
        got = real(t, text, choices, **kw)
        if t == title and got == tag and not fired:
            fired.append(1)
            action()
        return got
    ctx.dialog.menu = menu


# --- V1/V6/V12: one editor; the second opener is view-only ----------------

def test_v6_a_second_session_is_view_only_naming_the_holder(cfg):
    _seed(cfg)
    held = ms.acquire_lease(cfg, user="alice", tty="pts/3").lease
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["port", "save"]
    h._configure_mqtt()
    assert not _calls(ctx, "menu", "MQTT Configuration")
    (title, text), = [c[1] for c in _calls(ctx, "msgbox")]
    assert title == "MQTT Configuration — View Only"
    assert "alice" in text and f"pid {os.getpid()}" in text and "pts/3" in text
    assert "mqtt.a.example:1883" in text and "(saved)" in text
    assert "s3cret" not in text
    held.release()
    assert _disk(cfg) == {"schema_version": 1, **A}


def test_v12_an_untrusted_record_says_unknown_session(cfg):
    _seed(cfg)
    held = ms.acquire_lease(cfg, user="alice", tty="pts/3").lease
    with open(held.path, "w") as f:
        f.write("garbage")
    h, ctx = _handler()
    h._configure_mqtt()
    (title, text), = [c[1] for c in _calls(ctx, "msgbox")]
    assert "unknown session" in text and "View Only" in title
    held.release()


def test_v5_the_lease_is_released_when_configure_closes(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["cancel"]
    h._configure_mqtt()
    assert ms.acquire_lease(cfg, user="b", tty="-").state == "held"


def test_v5_screens_opened_from_configure_reuse_its_lease(cfg):
    """The private form, TLS menu and presets run INSIDE the menu's lease —
    a nested acquire would lock the operator out of their own screen."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["private", "keep", "auto"]
    ctx.dialog._inputbox_returns = ["mqtt.a.example", "1884", "alice",
                                    "msh/HI/2/e", "Regional"]
    h._configure_mqtt()
    assert not [c for c in _calls(ctx, "msgbox") if "View Only" in c[1][0]]
    assert _disk(cfg)["port"] == 1884 and _disk(cfg)["password"] == "s3cret"


def test_v11_two_sessions_one_after_the_other_both_changes_land(cfg):
    _seed(cfg)
    first, fctx = _handler()
    second, sctx = _handler()

    def second_tries():
        second._configure_mqtt()          # while first holds the lease

    fctx.dialog._menu_returns = ["port", "save"]
    fctx.dialog._inputbox_returns = ["1884"]
    _fire_at(fctx, "MQTT Configuration", "port", second_tries)
    first._configure_mqtt()
    assert [c[1][0] for c in _calls(sctx, "msgbox")] == ["MQTT Configuration — View Only"]

    sctx.dialog.calls.clear()
    sctx.dialog._menu_returns = ["channel", "save"]
    sctx.dialog._inputbox_returns = ["Bobs"]
    second._configure_mqtt()
    port_rows = [r for c in _calls(sctx, "menu", "MQTT Configuration")
                 for r in c[1][2] if r[0] == "port"]
    assert "1884" in port_rows[0][1]              # sees the first's change
    disk = _disk(cfg)
    assert disk["port"] == 1884 and disk["channel"] == "Bobs"
    assert disk["password"] == "s3cret"


# --- V7: a lease-less write is never overwritten (adapted r5 #1, 4 paths) --

@pytest.mark.parametrize("script,inputs,trigger", [
    # list edit then Save (r4 "Esc'd conflict then save")
    (["channel", "save"], ["Mine"], "channel"),
    # the private form (r5 #1 path 1)
    (["private", "keep", "auto"], ["mqtt.a.example", "1883", "alice",
                                   "msh/HI/2/e", "Mine"], "private"),
    # an invalid refusal, then fix, then Save (r5 #1 path 3)
    (["channel", "save", "channel", "save"], ["Bad Chan", "Mine"], "channel"),
])
def test_v7_a_lease_less_write_mid_session_is_refused_never_overwritten(
        cfg, script, inputs, trigger):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = list(script)
    ctx.dialog._inputbox_returns = list(inputs)
    _fire_at(ctx, "MQTT Configuration", trigger,
             lambda: _hand_edit(cfg, port=1885, password="handpw"))
    h._configure_mqtt()
    disk = _disk(cfg)
    assert disk["port"] == 1885 and disk["password"] == "handpw"
    assert disk["channel"] == "Regional"
    assert _calls(ctx, "msgbox", "Not Saved — Changed Elsewhere")


def test_v7_a_preset_after_a_lease_less_write_is_refused(cfg):
    """r5 #1 path 2 (preset)."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    ctx.dialog._yesno_returns = [True]            # yes, clear the password
    _fire_at(ctx, "MQTT Configuration", "local",
             lambda: _hand_edit(cfg, port=1885))
    h._configure_mqtt()
    assert _disk(cfg)["port"] == 1885 and _disk(cfg)["broker"] == "mqtt.a.example"


def test_v7_after_stale_the_screen_closes_and_reopening_shows_the_change(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save", "save"]   # 2nd save never asked
    ctx.dialog._inputbox_returns = ["Mine"]
    _fire_at(ctx, "MQTT Configuration", "channel", lambda: _hand_edit(cfg, port=1885))
    h._configure_mqtt()
    assert len(_calls(ctx, "menu", "MQTT Configuration")) == 2
    ctx.dialog.calls.clear()
    ctx.dialog._menu_returns = ["cancel"]
    h._configure_mqtt()
    rows = _calls(ctx, "menu", "MQTT Configuration")[0][1][2]
    assert any(t == "port" and "1885" in d for t, d in rows)


# --- Q15 / V2: Reset ------------------------------------------------------

def test_v2_file_broke_after_open_reset_rebuilds_from_the_screen(cfg):
    """r5 #2: the zero-byte backup means the screen is the last copy."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine", "typedpw"]
    ctx.dialog._yesno_returns = [True]
    _fire_at(ctx, "MQTT Configuration", "channel",
             lambda: (cfg / ms.FILENAME).write_text(""))
    h._configure_mqtt()
    assert [c[1][0] for c in _calls(ctx, "yesno")] == ["Settings File Broke"]
    pw_prompt = [c for c in _calls(ctx, "inputbox", "Password")][0][1][1]
    assert "alice@mqtt.a.example" in pw_prompt
    disk = _disk(cfg)
    assert disk["channel"] == "Mine" and disk["broker"] == "mqtt.a.example"
    assert disk["password"] == "typedpw" and disk["auto_start"] is True
    assert list(cfg.glob(ms.FILENAME + ".unreadable.*"))


def test_v2_declining_the_rebuild_writes_nothing(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save", "cancel"]
    ctx.dialog._inputbox_returns = ["Mine"]
    ctx.dialog._yesno_returns = [False]
    _fire_at(ctx, "MQTT Configuration", "channel",
             lambda: (cfg / ms.FILENAME).write_text(""))
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_text() == ""


def test_q15_unreadable_at_open_save_asks_for_reset_then_writes(cfg):
    (cfg / ms.FILENAME).write_text("{broken")
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine", "typedpw"]
    ctx.dialog._yesno_returns = [True]
    h._configure_mqtt()
    assert "UNREADABLE" in _calls(ctx, "menu", "MQTT Configuration")[0][1][1]
    assert [c[1][0] for c in _calls(ctx, "yesno")] == ["Settings Unreadable"]
    # r2 F7: the password is ASKED (unknown — the file never loaded) and the
    # typed answer is what lands, not the fake inputbox's default
    pw = _calls(ctx, "inputbox", "Password")
    assert len(pw) == 1 and "cannot be read" in pw[0][1][1]
    assert _disk(cfg)["channel"] == "Mine" and _disk(cfg)["password"] == "typedpw"
    assert [b.read_text() for b in cfg.glob(ms.FILENAME + ".unreadable.*")] == ["{broken"]


def test_q15_unreadable_at_open_declining_reset_writes_nothing(cfg):
    (cfg / ms.FILENAME).write_text("{broken")
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["save", "cancel"]
    ctx.dialog._yesno_returns = [False]
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_text() == "{broken"


def test_r2_f4_rebuild_never_claims_a_saved_password_that_never_was(cfg):
    """r2 F4: readable at open with NO password, then broke — the screen
    knows no password was saved; the prompt must not say one 'cannot be read'."""
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine", ""]
    ctx.dialog._yesno_returns = [True]
    _fire_at(ctx, "MQTT Configuration", "channel",
             lambda: (cfg / ms.FILENAME).write_text(""))
    h._configure_mqtt()
    assert _disk(cfg)["channel"] == "Mine" and _disk(cfg)["password"] == ""
    (pw,) = [c[1][1] for c in _calls(ctx, "inputbox", "Password")]
    assert "cannot be read" not in pw
    assert "No password was saved" in pw and "alice@mqtt.a.example" in pw


def test_r2_f2_a_preset_says_the_auto_start_it_writes(cfg):
    """r2 F2: the Auto-Start toggle said 'after Save & Exit'; a preset then
    wrote it. Every save summary now names the auto-start it wrote."""
    _seed(cfg, {**A, "auto_start": False, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["autostart", "local"]
    h._configure_mqtt()
    assert _disk(cfg)["auto_start"] is True
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Local Mode Set")]
    assert "Auto-start: MQTT on" in text
    toggle = _calls(ctx, "msgbox", "Auto-Start")[0][1][1]
    assert "the next save" in toggle and "Save & Exit" in toggle


def test_r2_f5_q19_names_the_live_files_account(cfg):
    """r2 F5: a lease-less hand edit changed the account after open; Q19
    names whose password it is NOW, not the account seen at open."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local", "cancel"]
    ctx.dialog._yesno_returns = [False]
    _fire_at(ctx, "MQTT Configuration", "local", lambda: (cfg / ms.FILENAME).write_text(
        json.dumps({"schema_version": 1, **A, "username": "zed", "password": "zpw"})))
    h._configure_mqtt()
    ((_t, text),) = [c[1][:2] for c in _calls(ctx, "yesno")]
    assert "zed@mqtt.a.example" in text and "alice@" not in text


def test_v11_nit_changed_elsewhere_says_nothing_written_once(cfg):
    """V11 drill legibility nit: the Changed Elsewhere box said it twice."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine"]
    _fire_at(ctx, "MQTT Configuration", "channel", lambda: _hand_edit(cfg, port=1885))
    h._configure_mqtt()
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Not Saved — Changed Elsewhere")]
    assert text.lower().count("nothing written") + text.lower().count(
        "nothing was written") == 1, text


@pytest.mark.parametrize("preset", ["local", "public"])
def test_v15_a_preset_keeps_the_saved_key_and_says_which(cfg, preset):
    """r3 #1 (V13-class, operator 'your call — i lean to fix' -> V15): a
    preset KEEPS the saved channel key and its summary names the kind
    (default/custom) — never the PSK itself."""
    _seed(cfg, {**A, "password": "", "key": "customPSKbase64=="})
    h, ctx = _handler()
    ctx.dialog._menu_returns = [preset]
    ctx.dialog._yesno_returns = [True]
    h._configure_mqtt()
    disk = _disk(cfg)
    assert disk["broker"] in ("localhost", "mqtt.meshtastic.org")
    assert disk["key"] == "customPSKbase64=="
    title = {"local": "Local Mode Set", "public": "Public Mode Set"}[preset]
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", title)]
    assert "Channel key: custom (kept)" in text
    assert "customPSKbase64==" not in repr(ctx.dialog.calls)


def test_v15_a_default_key_is_named_default(cfg):
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    h._configure_mqtt()
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Local Mode Set")]
    assert "Channel key: default" in text


def test_r3_1b_public_preset_names_the_account_it_writes(cfg):
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["public"]
    ctx.dialog._yesno_returns = [True]
    h._configure_mqtt()
    assert _disk(cfg)["username"] == "meshdev"
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Public Mode Set")]
    assert "meshdev@mqtt.meshtastic.org" in text


def test_r3_4_absent_at_open_rebuild_never_claims_a_saved_password(cfg):
    """r3 #4: the F4 fix missed the ABSENT-at-open branch."""
    assert not (cfg / ms.FILENAME).exists()
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine", ""]
    ctx.dialog._yesno_returns = [True]
    _fire_at(ctx, "MQTT Configuration", "channel",
             lambda: (cfg / ms.FILENAME).write_text("{garbage"))
    h._configure_mqtt()
    assert _disk(cfg)["channel"] == "Mine"
    (pw,) = [c[1][1] for c in _calls(ctx, "inputbox", "Password")]
    assert "cannot be read" not in pw and "No password was saved" in pw


def test_r3_5_auth_row_over_an_unreadable_file_says_unknown(cfg):
    (cfg / ms.FILENAME).write_text('{"password": "s3cret", "broker": ')
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["cancel"]
    h._configure_mqtt()
    rows = _calls(ctx, "menu", "MQTT Configuration")[0][1][2]
    (auth,) = [d for t, d in rows if t == "auth"]
    assert "(none)" not in auth and "unknown" in auth


def test_r3_6_declined_q19_on_profile_apply_leaves_a_log_witness(cfg, caplog):
    import logging
    _seed(cfg)
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [False]
    with caplog.at_level(logging.INFO):
        h._apply_profile_to_mqtt(_Profile(""))
    assert _disk(cfg) == {"schema_version": 1, **A}
    assert any("NOT applied" in r.getMessage() and "declined" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# --- Q3 / Q7 / Q8: the password ------------------------------------------

def test_q3_an_unrelated_edit_keeps_the_saved_password(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["port", "save"]
    ctx.dialog._inputbox_returns = ["1884"]
    h._configure_mqtt()
    assert _disk(cfg)["password"] == "s3cret" and _disk(cfg)["port"] == 1884


def test_q3_auth_blank_enter_through_never_wipes_the_password(cfg):
    """The original F8 row (mqtt-tui-auth-blank-password-wipes)."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["auth", "keep", "save"]
    h._configure_mqtt()
    assert _disk(cfg)["password"] == "s3cret"


def test_q3_clear_is_explicit(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["auth", "clear", "save"]
    h._configure_mqtt()
    assert _disk(cfg)["password"] == ""


def test_q7_keep_for_another_broker_asks_for_that_accounts_password(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["broker", "save"]
    ctx.dialog._inputbox_returns = ["mqtt.b.example", "bpw"]
    h._configure_mqtt()
    prompt = _calls(ctx, "inputbox", "Password")[0][1][1]
    assert "alice@mqtt.b.example" in prompt and "s3cret" not in prompt
    assert _disk(cfg)["broker"] == "mqtt.b.example" and _disk(cfg)["password"] == "bpw"


def test_q7_esc_at_the_password_question_writes_nothing(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["broker", "save", "cancel"]
    ctx.dialog._inputbox_returns = ["mqtt.b.example", None]
    h._configure_mqtt()
    assert _disk(cfg) == {"schema_version": 1, **A}


def test_q7_r5_keep_saved_does_not_loop(cfg):
    """r5 test_more: Q7 then keep-saved looped. Now Q7 asks for a VALUE."""
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["auth", "keep", "save"]
    ctx.dialog._inputbox_returns = ["bob", ""]          # new user, blank pw
    h._configure_mqtt()
    assert len(_calls(ctx, "inputbox", "Password")) == 1
    assert _disk(cfg)["username"] == "bob" and _disk(cfg)["password"] == ""


# --- Q19: presets and profiles ask before replacing/clearing --------------

@pytest.mark.parametrize("preset,verb", [("local", "CLEARS"), ("public", "REPLACES")])
def test_q19_a_preset_asks_before_touching_the_saved_password(cfg, preset, verb):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = [preset, "cancel"]
    ctx.dialog._yesno_returns = [False]
    h._configure_mqtt()
    (title, text), = [c[1] for c in _calls(ctx, "yesno")]
    assert verb in text and "alice@mqtt.a.example" in text
    assert _disk(cfg) == {"schema_version": 1, **A}


def test_q19_yes_applies_the_preset(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["public"]
    ctx.dialog._yesno_returns = [True]
    h._configure_mqtt()
    assert _disk(cfg)["password"] == MESHTASTIC_PUBLIC_PASSWORD


def test_q19_no_saved_password_no_question(cfg):
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    h._configure_mqtt()
    assert not _calls(ctx, "yesno") and _disk(cfg)["broker"] == "localhost"


class _Profile:
    name = "club"

    def __init__(self, password):
        self._pw = password

    def to_intent(self):
        return {**ms.defaults(), "broker": "mqtt.club.example", "port": 1883,
                "username": "club", "password": self._pw}


class _KeyProfile(_Profile):
    def __init__(self, password, key):
        super().__init__(password)
        self._key = key

    def to_intent(self):
        return {**super().to_intent(), "key": self._key}


@pytest.mark.parametrize("profile_key", ["AQ==", None, ""])
def test_v15_profile_apply_keeps_the_saved_key(cfg, profile_key):
    """r4 #1 (V13-class; operator 'fixing them the same way we did V15'):
    a profile whose key is the default/blank KEEPS the saved custom key."""
    _seed(cfg, {**A, "key": "customPSKbase64=="})
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [True]
    h._apply_profile_to_mqtt(_KeyProfile("clubpw", profile_key))
    disk = _disk(cfg)
    assert disk["broker"] == "mqtt.club.example"
    assert disk["key"] == "customPSKbase64=="
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Profile Applied")]
    assert "Channel key: custom (kept)" in text
    assert "customPSKbase64==" not in repr(ctx.dialog.calls)


def test_v15_a_profile_with_its_own_key_writes_it_and_says_so(cfg):
    _seed(cfg, {**A, "key": "customPSKbase64=="})
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [True]
    h._apply_profile_to_mqtt(_KeyProfile("clubpw", "profilePSK=="))
    assert _disk(cfg)["key"] == "profilePSK=="
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Profile Applied")]
    assert "Channel key: custom\n" in text or text.rstrip().endswith("Channel key: custom")
    assert "profilePSK==" not in repr(ctx.dialog.calls)


def test_r4_2_public_setup_shows_the_account_it_writes(cfg):
    """r4 #2: the auto-apply after Quick public setup names the account."""
    with patch("handlers.broker.load_profiles", return_value={}), \
            patch("handlers.broker.save_profiles", return_value=True):
        h, ctx = _broker()
        ctx.dialog._inputbox_returns = ["HI", "LongFast"]
        h._setup_public_broker()
    assert _disk(cfg)["username"] == "meshdev"
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Profile Applied")]
    assert "meshdev@mqtt.meshtastic.org" in text


def test_r4_3_a_profile_reported_not_saved_is_never_applied(cfg):
    with patch("handlers.broker.load_profiles", return_value={}), \
            patch("handlers.broker.save_profiles", return_value=False):
        h, ctx = _broker()
        ctx.dialog._inputbox_returns = ["US", "LongFast"]
        h._setup_public_broker()
    assert any(c[1][0] == "Save Failed" for c in _calls(ctx, "msgbox"))
    assert not (cfg / ms.FILENAME).exists()


def test_r4_4_absent_at_open_prompt_never_says_no_longer_loads(cfg):
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["channel", "save"]
    ctx.dialog._inputbox_returns = ["Mine", ""]
    ctx.dialog._yesno_returns = [True]
    _fire_at(ctx, "MQTT Configuration", "channel",
             lambda: (cfg / ms.FILENAME).write_text("{garbage"))
    h._configure_mqtt()
    (pw,) = [c[1][1] for c in _calls(ctx, "inputbox", "Password")]
    assert "No password was saved" in pw and "no longer loads" not in pw
    assert "no settings file existed" in pw


def test_r4_5_summary_names_the_stored_tls_mode(cfg):
    _seed(cfg, {**A, "tls": True, "password": ""})
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    h._configure_mqtt()
    assert _disk(cfg)["tls"] == "auto"
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", "Local Mode Set")]
    assert "TLS: off (auto)" in text


def test_q19_a_profile_apply_asks_before_clearing_the_saved_password(cfg):
    """r4 #2 / test_profile_apply_without_password_clears_saved_silently."""
    _seed(cfg)
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [False]
    h._apply_profile_to_mqtt(_Profile(""))
    assert "CLEARS" in _calls(ctx, "yesno")[0][1][1]
    assert _disk(cfg) == {"schema_version": 1, **A}


def test_v5_profile_apply_takes_its_own_lease_and_writes(cfg):
    _seed(cfg)
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [True]
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    disk = _disk(cfg)
    assert disk["broker"] == "mqtt.club.example" and disk["password"] == "clubpw"
    assert disk["auto_start"] is True                 # carried from the saved
    assert ms.acquire_lease(cfg, user="x", tty="-").state == "held"


def _assert_f1_refused(ctx, cfg, before: str, says: str):
    """F1 (r2, operator ruling 2026-11-03): profile apply has no settings
    screen, so it never offers Reset/rebuild — that would write auto_start
    values nobody was shown. It refuses, writes nothing, names Configure.
    `says` pins WHICH refusal fired (review F1 #8), not just a substring."""
    asked = [c[1][0] for c in _calls(ctx, "yesno")]
    assert "Settings Unreadable" not in asked and "Settings File Broke" not in asked
    assert (cfg / ms.FILENAME).read_text() == before
    assert not list(cfg.glob(ms.FILENAME + ".unreadable.*"))
    boxes = _calls(ctx, "msgbox")
    assert [c[1][0] for c in boxes] == ["Apply Failed"]
    said = boxes[0][1][1]
    assert "NOT applied" in said and says in said
    assert "was saved" not in said                    # review F1 #5
    assert "kept as a backup" not in said             # review F1 #4: none made


def test_f1_profile_apply_over_an_unreadable_file_refuses_to_configure(cfg):
    (cfg / ms.FILENAME).write_text("{broken")
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [True, True, True]   # would say yes to anything
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    _assert_f1_refused(ctx, cfg, "{broken", "does not load")
    assert "MQTT Configuration" in _calls(ctx, "msgbox")[0][1][1]
    assert _calls(ctx, "yesno") == []     # refused before any question


def test_f1_profile_apply_when_the_file_breaks_mid_apply_refuses(cfg):
    _seed(cfg)
    h, ctx = _broker()
    real = ctx.dialog.yesno
    broke = []

    def yesno(title, text, **kw):
        if not broke:                       # during the Q19 question
            broke.append(1)
            (cfg / ms.FILENAME).write_text("")
            return True
        return real(title, text, **kw)
    ctx.dialog.yesno = yesno
    ctx.dialog._yesno_returns = [True, True]
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    assert broke, "Q19 never asked — the test did not reach the mid-apply break"
    _assert_f1_refused(ctx, cfg, "", "no longer loads")
    assert "MQTT Configuration" in _calls(ctx, "msgbox")[0][1][1]


def test_f1_review_1_a_newer_schema_file_is_never_advised_reset(cfg):
    """A file from a NEWER version loads as unreadable; the refusal must say
    'newer', never steer the operator to Reset a valid file."""
    newer = json.dumps({"schema_version": ms.SCHEMA_VERSION + 1, **A})
    (cfg / ms.FILENAME).write_text(newer)
    h, ctx = _broker()
    ctx.dialog._yesno_returns = [True, True, True]
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    _assert_f1_refused(ctx, cfg, newer, "newer version")
    assert "Reset" not in _calls(ctx, "msgbox")[0][1][1]
    assert _calls(ctx, "yesno") == []


def test_f1_review_3_profile_apply_stale_names_no_configure_screen(cfg):
    _seed(cfg)
    h, ctx = _broker()
    real = ctx.dialog.yesno

    def yesno(title, text, **kw):
        _hand_edit(cfg, channel="ByHand")   # readable, written lease-less
        return True
    ctx.dialog.yesno = yesno
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    boxes = _calls(ctx, "msgbox")
    assert [c[1][0] for c in boxes] == ["Apply Failed"]
    said = boxes[0][1][1]
    assert "NOT applied" in said and "changed" in said
    assert "Leave MQTT Configuration" not in said
    assert _disk(cfg)["channel"] == "ByHand"         # nothing overwritten


def test_v6_profile_apply_while_another_session_edits_is_refused(cfg):
    _seed(cfg)
    held = ms.acquire_lease(cfg, user="alice", tty="pts/3").lease
    h, ctx = _broker()
    h._apply_profile_to_mqtt(_Profile("clubpw"))
    assert "View Only" in _calls(ctx, "msgbox")[0][1][0]
    held.release()
    assert _disk(cfg) == {"schema_version": 1, **A}


# --- Q5 / Q6 --------------------------------------------------------------

@pytest.mark.parametrize("inputs,menus", [
    # username Esc — scripted to COMPLETE if Esc did not abandon (drill M12)
    (["mqtt.b.example", "1883", None, "msh/US/2/e", "LongFast", "pw"], ["keep", "auto"]),
    (["mqtt.b.example", "1883", "bob"], [None]),                  # password Esc
    (["mqtt.b.example", "1883", "bob"], ["keep", None]),          # TLS Esc
    (["mqtt.b.example", "1883", "bob", None], ["keep", "auto"]),  # root Esc
    (["mqtt.b.example", "1883", "bob", "msh/US/2/e", None], ["keep", "auto"]),
])
def test_q5_esc_anywhere_in_the_private_form_writes_nothing(cfg, inputs, menus):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["private", *menus]
    ctx.dialog._inputbox_returns = list(inputs)
    h._configure_mqtt()
    assert _disk(cfg) == {"schema_version": 1, **A}


def test_q6_the_configure_tls_row_opens_the_one_tls_menu(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["tls", "on", "save"]
    h._configure_mqtt()
    (tls_menu,) = _calls(ctx, "menu", "TLS")
    assert tls_menu[2]["default_item"] == "auto"
    assert any("(current)" in d for _, d in tls_menu[1][2])
    assert _disk(cfg)["tls"] is True


# --- no plaintext anywhere a screen or a log can show it (r4/r5 leak) -----

@pytest.mark.parametrize("menus,inputs,yes", [
    (["port", "save"], ["1884"], []),
    (["broker", "save"], ["mqtt.b.example", "typedXX"], []),
    (["public"], [], [True]),
    (["private", "keep", "auto"], ["p.example", "1883", "alice",
                                   "msh/US/2/e", "LongFast", "typedXX"], []),
])
def test_no_saved_plaintext_in_dialogs_or_logs(cfg, caplog, menus, inputs, yes):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = list(menus)
    ctx.dialog._inputbox_returns = list(inputs)
    ctx.dialog._yesno_returns = list(yes)
    with caplog.at_level(logging.DEBUG):
        h._configure_mqtt()
    blob = repr(ctx.dialog.calls) + "\n".join(r.getMessage() for r in caplog.records)
    assert "s3cret" not in blob


# --- V9 / Q16: one write path, and it needs the lease ---------------------

def test_v9_the_tui_write_path_refuses_without_a_lease(cfg):
    from handlers.mqtt import save_mqtt_config
    _seed(cfg)
    res = save_mqtt_config({**A, "port": 1999}, None)
    assert not res.ok and res.code == "no_lease"
    assert _disk(cfg)["port"] == 1883


def test_q16_no_tui_handler_writes_mqtt_settings_any_other_way():
    import re
    from pathlib import Path
    hdir = Path(__file__).resolve().parent.parent / "src" / "launcher_tui" / "handlers"
    bad = []
    for f in sorted(hdir.glob("*.py")):
        text = f.read_text()
        for pat in (r"\bmqtt_settings\.save\(", r"\bsave_settings\(",
                    r"mqtt_nodeless\.json", r"\bms\.save\("):
            for m in re.finditer(pat, text):
                line = text[:m.start()].count("\n") + 1
                allowed = f.name == "mqtt.py" and pat == r"\bsave_settings\("
                if not allowed:
                    bad.append(f"{f.name}:{line} {pat}")
    assert not bad, bad
    calls = [f"{f.name}" for f in hdir.glob("*.py")
             if re.search(r"\bsave_mqtt_config\(", f.read_text())
             and f.name not in ("mqtt.py", "_mqtt_config_form.py")]
    assert not calls, f"write MQTT settings via save_with_prompts, not: {calls}"


# --- review r1 (Fable, non-author, 2026-11-03) ----------------------------

def test_r1_2_esc_in_the_private_form_returns_to_the_list_with_edits_kept(cfg):
    _seed(cfg)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["port", "private", "save"]
    ctx.dialog._inputbox_returns = ["1884", None]          # Esc at broker
    h._configure_mqtt()
    assert _disk(cfg)["port"] == 1884                       # the list edit survived


@pytest.mark.parametrize("menus", [["local"], ["public"]])
def test_r1_3_a_preset_over_an_unreadable_file_asks_about_the_password(cfg, menus):
    (cfg / ms.FILENAME).write_text('{"password": "s3cret", "broker": ')   # typo
    h, ctx = _handler()
    ctx.dialog._menu_returns = list(menus) + ["cancel"]
    ctx.dialog._yesno_returns = [False]
    h._configure_mqtt()
    (title, text), = [c[1] for c in _calls(ctx, "yesno")]
    assert title == "Saved Password" and "cannot be read" in text
    assert (cfg / ms.FILENAME).read_text().startswith('{"password"')

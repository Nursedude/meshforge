"""F8 state model in the TUI (grill 2026-11-02, Q1-Q16).

Record: `.claude/plans/mqtt_f8_state_model_2026_11_02.md`. Every test drives
the REAL handler against a REAL file in a tmp config dir and reads the file
back — the end state, not a mocked save call.

Acceptance (Q14): two screens open at once each change a different field and
both land; a broker change never carries or silently clears a password (the
operator is asked first); Esc never writes.
"""
import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "launcher_tui"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from handler_test_utils import make_handler_context  # noqa: E402
from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402

A = {**ms.defaults(), "broker": "mqtt.a.example", "port": 1883, "tls": "auto",
     "username": "alice", "password": "s3cret", "root_topic": "msh/HI/2/e",
     "channel": "Regional", "auto_start": True}


@pytest.fixture
def cfg(tmp_path):
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        yield tmp_path


def _seed(d, intent=A):
    assert ms.save(d, dict(intent), W).ok


def _disk(d):
    return json.loads((d / ms.FILENAME).read_text())


def _handler(menus=(), inputs=()):
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    h = MQTTHandler()
    h.set_context(ctx)
    ctx.dialog._menu_returns = list(menus)
    ctx.dialog._inputbox_returns = list(inputs)
    return h, ctx


def _calls(ctx, kind, title=None):
    return [c for c in ctx.dialog.calls
            if c[0] == kind and (title is None or c[1][0] == title)]


# --- Q14 / Q2: two screens, both changes land -------------------------------

def test_two_screens_open_at_once_both_changes_land(cfg):
    _seed(cfg)
    b, _ = _handler(menus=["port", "save"], inputs=["1884"])
    a, actx = _handler()
    script = iter(["tls", "on", "save"])

    def menu(title, text, choices, **kw):
        actx.dialog.calls.append(("menu", (title, text, choices), kw))
        tag = next(script)
        if tag == "save":
            b._configure_mqtt()          # screen B saves while A is still open
        return tag
    actx.dialog.menu = menu
    a._configure_mqtt()
    disk = _disk(cfg)
    assert disk["tls"] is True, "A's change lost"
    assert disk["port"] == 1884, "B's change lost (the stale snapshot class)"
    assert disk["password"] == "s3cret"


def test_a_screen_that_changed_nothing_writes_nothing_over_another(cfg):
    _seed(cfg)
    stale, sctx = _handler()
    other, _ = _handler(menus=["channel", "save"], inputs=["Other"])
    script = iter(["save"])

    def menu(title, text, choices, **kw):
        other._configure_mqtt()
        return next(script)
    sctx.dialog.menu = menu
    stale._configure_mqtt()
    assert _disk(cfg)["channel"] == "Other"


# --- F8: a blank password never wipes the saved one --------------------------

def test_F8_auth_row_enter_through_keeps_the_saved_password(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "keep", "save"], inputs=["alice"])
    h._configure_mqtt()
    assert _disk(cfg)["password"] == "s3cret"
    pw_menu = _calls(ctx, "menu", "Password")
    assert pw_menu and pw_menu[0][2].get("default_item") == "keep"


def test_F8_esc_on_the_password_menu_changes_nothing(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", None, "channel", "save"], inputs=["alice", "Other"])
    h._configure_mqtt()
    assert _calls(ctx, "menu", "Password"), "no password menu: vacuous"
    assert _disk(cfg)["channel"] == "Other", "nothing saved: vacuous"
    assert _disk(cfg)["password"] == "s3cret"


def test_F8_clear_is_an_explicit_choice(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "clear", "save"], inputs=["alice"])
    h._configure_mqtt()
    assert _calls(ctx, "menu", "Password")
    assert _disk(cfg)["password"] == ""


def test_F8_new_password_is_written(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "new", "save"], inputs=["alice", "n3w"])
    h._configure_mqtt()
    assert _calls(ctx, "menu", "Password")
    assert _disk(cfg)["password"] == "n3w"


def test_round5_1_a_cleared_password_is_not_offered_back(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "clear", "auth", "save"], inputs=["alice", "alice", ""])
    h._configure_mqtt()
    assert _disk(cfg)["password"] == ""
    assert len(_calls(ctx, "menu", "Password")) == 1, "cleared password offered as (saved)"


def test_the_screen_never_holds_the_saved_password(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "keep", "save"], inputs=["alice"])
    h._configure_mqtt()
    assert "s3cret" not in repr(ctx.dialog.calls)


def test_the_saved_marker_never_reaches_the_file(cfg):
    _seed(cfg)
    # Enter on everything: the fake inputbox returns its init
    h, _ = _handler(menus=["auth", "new", "save"])
    h._configure_mqtt()
    pw = _disk(cfg)["password"]
    assert "saved" not in pw.lower()


# --- Q7: changing the account asks for the password first --------------------

def test_Q7_broker_change_asks_for_the_new_accounts_password(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["broker", "save"], inputs=["mqtt.b.example", "b-pw"])
    h._configure_mqtt()
    ask = [c for c in _calls(ctx, "inputbox") if "alice@mqtt.b.example" in c[1][0] + c[1][1]]
    assert ask, "the operator was not asked for the new account's password"
    assert _disk(cfg)["broker"] == "mqtt.b.example" and _disk(cfg)["password"] == "b-pw"


def test_Q7_blank_answer_saves_no_password_explicitly(cfg):
    _seed(cfg)
    h, _ = _handler(menus=["broker", "save"], inputs=["mqtt.b.example", ""])
    h._configure_mqtt()
    assert _disk(cfg)["password"] == ""


def test_Q7_esc_at_the_question_writes_nothing(cfg):
    _seed(cfg)
    before = (cfg / ms.FILENAME).read_bytes()
    h, ctx = _handler(menus=["broker", "save", "cancel"], inputs=["mqtt.b.example", None])
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_bytes() == before
    assert not [c for c in _calls(ctx, "msgbox") if "Cleared" in c[1][0]]


def test_Q4_a_loopback_respelling_is_the_same_account(cfg):
    _seed(cfg, {**A, "broker": "localhost"})
    h, _ = _handler(menus=["broker", "save"], inputs=["127.0.0.1"])
    h._configure_mqtt()
    assert _disk(cfg)["password"] == "s3cret"


def test_round5_3_broker_row_strips_before_validating(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["broker", "save"], inputs=[" mqtt.a.example ", ""])
    h._configure_mqtt()
    assert not _calls(ctx, "msgbox", "Invalid Host")
    assert _disk(cfg)["broker"] == "mqtt.a.example"


# --- Q5: Esc never writes ------------------------------------------------------

PRIVATE_INPUTS = ["mqtt.c.example", "1883", "carol", "c-pw", "msh/HI/2/e", "Regional"]


@pytest.mark.parametrize("esc_at", range(len(PRIVATE_INPUTS)))
def test_Q5_esc_anywhere_in_the_private_form_writes_nothing(cfg, esc_at):
    _seed(cfg)
    before = (cfg / ms.FILENAME).read_bytes()
    inputs = PRIVATE_INPUTS[:esc_at] + [None]
    h, _ = _handler(menus=["private", "auto"], inputs=inputs)
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_bytes() == before


def test_Q5_esc_at_the_private_forms_tls_writes_nothing(cfg):
    _seed(cfg)
    before = (cfg / ms.FILENAME).read_bytes()
    h, _ = _handler(menus=["private", None], inputs=PRIVATE_INPUTS)
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_bytes() == before


def test_the_private_form_saves_a_new_account(cfg):
    _seed(cfg)
    h, _ = _handler(menus=["private", "on"], inputs=PRIVATE_INPUTS)
    h._configure_mqtt()
    d = _disk(cfg)
    assert (d["broker"], d["username"], d["password"], d["tls"]) == \
        ("mqtt.c.example", "carol", "c-pw", True)
    assert d["auto_start"] is True, "an un-asked field was reset"


def test_F8_private_form_same_account_enter_through_keeps_the_password(cfg):
    _seed(cfg)
    # same broker/user: the password step is the keep menu, Enter = keep
    h, ctx = _handler(menus=["private", "keep", "auto"],
                      inputs=["mqtt.a.example", "1883", "alice", "msh/HI/2/e", "Other"])
    h._configure_mqtt()
    assert _disk(cfg)["channel"] == "Other", "nothing saved: vacuous"
    assert _disk(cfg)["password"] == "s3cret"


def test_round5_4_private_form_does_not_reset_an_unasked_tls(cfg):
    _seed(cfg, {**A, "tls": True})
    h, _ = _handler(menus=["private", "keep", "on"],
                    inputs=["mqtt.a.example", "1883", "alice", "msh/HI/2/e", "Other"])
    h._configure_mqtt()
    assert _disk(cfg)["channel"] == "Other", "nothing saved: vacuous"
    assert _disk(cfg)["tls"] is True


# --- Q6: one TLS control ---------------------------------------------------------

def test_Q6_the_tls_row_opens_the_menu_and_never_cycles_silently(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["tls", "on", "save"])
    h._configure_mqtt()
    assert _calls(ctx, "menu", "TLS"), "the tls row must open the TLS menu"
    assert _disk(cfg)["tls"] is True


def test_Q6_esc_on_the_tls_row_changes_nothing(cfg):
    _seed(cfg, {**A, "tls": False})
    h, _ = _handler(menus=["tls", None, "save"])
    h._configure_mqtt()
    assert _disk(cfg)["tls"] is False


def test_Q6_the_tls_menu_highlights_the_screens_value_and_marks_the_saved(cfg):
    _seed(cfg, {**A, "tls": False})
    h, ctx = _handler(menus=["tls", "on", "tls", "on", "save"])
    h._configure_mqtt()
    second = _calls(ctx, "menu", "TLS")[1]
    assert second[2].get("default_item") == "on"          # the unsaved edit
    rows = dict(second[1][2])
    assert "(saved)" in rows["off"] and "(saved)" not in rows["on"]


# --- Q10: both sides changed one field ------------------------------------------

def _conflicted(cfg, answer):
    _seed(cfg)
    a, actx = _handler()
    other, _ = _handler(menus=["tls", "off", "save"])
    script = iter(["tls", "on", "save", answer, "cancel"])

    def menu(title, text, choices, **kw):
        actx.dialog.calls.append(("menu", (title, text, choices), kw))
        tag = next(script)
        if title == "MQTT Configuration" and tag == "save" and not menu.done:
            menu.done = True
            other._configure_mqtt()
        return tag
    menu.done = False
    actx.dialog.menu = menu
    a._configure_mqtt()
    return actx


def test_Q10_a_conflict_is_shown_with_both_values(cfg):
    ctx = _conflicted(cfg, None)
    conf = [c for c in ctx.dialog.calls if c[0] == "menu" and c[1][0].startswith("Changed")]
    assert conf and "off" in conf[0][1][1].lower() and "on" in conf[0][1][1].lower()
    assert _disk(cfg)["tls"] is False      # Esc: nothing written


def test_Q10_keep_mine_overwrites_after_seeing_it(cfg):
    _conflicted(cfg, "mine")
    assert _disk(cfg)["tls"] is True


def test_Q10_take_saved_drops_my_edit(cfg):
    _conflicted(cfg, "saved")
    assert _disk(cfg)["tls"] is False


# --- Q15: unreadable -> explicit Reset ----------------------------------------------

def test_Q15_save_over_unreadable_offers_reset_and_back_writes_nothing(cfg):
    (cfg / ms.FILENAME).write_text("{oops")
    h, ctx = _handler(menus=["channel", "save", "back", "cancel"], inputs=["Regional"])
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_text() == "{oops"
    assert _calls(ctx, "menu", "Settings Unreadable")


def test_Q15_reset_rebuilds_and_keeps_the_bytes(cfg):
    (cfg / ms.FILENAME).write_text("{oops")
    h, _ = _handler(menus=["channel", "save", "reset"], inputs=["Regional"])
    h._configure_mqtt()
    assert ms.load(cfg).state == "ok" and _disk(cfg)["channel"] == "Regional"
    assert (cfg / f"{ms.FILENAME}.unreadable.1").read_text() == "{oops"


def test_r3_1_reset_over_a_file_fixed_meanwhile_keeps_it(cfg):
    """The operator fixed the file while the 'Settings Unreadable' menu was
    up, then picked Reset: the fixed file (broker, password) must survive;
    the edit goes through the normal conflict question (r3 #1)."""
    (cfg / ms.FILENAME).write_text("{oops")
    h, ctx = _handler(menus=["channel", "save"], inputs=["Mine"])
    real_menu = ctx.dialog.menu

    def menu(title, text, choices, **kw):
        if title == "Settings Unreadable":
            (cfg / ms.FILENAME).unlink()     # the operator fixed it meanwhile
            _seed(cfg)
            ctx.dialog.calls.append(("menu", (title, text, choices), kw))
            return "reset"
        if title.startswith("Changed Elsewhere"):
            ctx.dialog.calls.append(("menu", (title, text, choices), kw))
            return "mine"
        return real_menu(title, text, choices, **kw)
    ctx.dialog.menu = menu
    h._configure_mqtt()
    d = _disk(cfg)
    assert (d["broker"], d["password"]) == (A["broker"], "s3cret"), "Reset overwrote a fixed file"
    assert d["channel"] == "Mine"
    assert _calls(ctx, "menu", "Changed Elsewhere: channel")
    assert not list(cfg.glob(ms.FILENAME + ".unreadable.*"))


# --- presets and profiles go through the same door -----------------------------------

def test_local_preset_keeps_unasked_fields_and_saves(cfg):
    _seed(cfg)
    h, _ = _handler(menus=["local"])
    with patch.object(h, "_detect_local_channel", return_value=None):
        h._configure_mqtt()
    d = _disk(cfg)
    assert d["broker"] == "localhost" and d["auto_start"] is True


def test_broker_profile_apply_keeps_auto_start_and_writes_its_password(cfg):
    _seed(cfg)
    from handlers.broker import BrokerHandler
    from utils.broker_profiles import BrokerProfile
    h = BrokerHandler()
    h.set_context(make_handler_context())
    p = BrokerProfile(name="t", broker_type="private", host="mqtt.p.example", port=1883, username="pat",
                      password="p-pw", root_topic="msh/HI/2/e", channel="Regional")
    h._apply_profile_to_mqtt(p)
    d = _disk(cfg)
    assert (d["broker"], d["password"], d["auto_start"]) == ("mqtt.p.example", "p-pw", True)


# --- Q16 / Q3 guards: one door, no password in screen code ----------------------------

HANDLERS = ROOT / "src" / "launcher_tui" / "handlers"
SCREEN_FILES = ("mqtt.py", "_mqtt_configure.py", "broker.py")
FULL_WRITERS = {"save_settings", "save_mqtt_config", "save"}
# Reset is save_changes(reset=True) since review r3 #1: NO screen function
# may make a full write any more.
RESET_FUNC = None


def _violations(src: str):
    tree = ast.parse(src)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
                full = name in ("save_settings", "save_mqtt_config") or \
                    (name == "save" and owner == "mqtt_settings")
                if full and fn.name != RESET_FUNC:
                    out.append(f"{fn.name}: full write {name}()")
                if name == "get" and isinstance(f, ast.Attribute) and n.args and \
                        isinstance(n.args[0], ast.Constant) and n.args[0].value == "password" \
                        and getattr(f.value, "id", None) != "changes":
                    out.append(f"{fn.name}: reads a password via .get")
            if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and \
                    isinstance(n.slice, ast.Constant) and n.slice.value == "password" \
                    and getattr(n.value, "id", None) != "changes":
                out.append(f"{fn.name}: reads a password via []")
    return out


def test_Q16_Q3_guard_screens_send_changes_and_never_read_a_password():
    bad = []
    for name in SCREEN_FILES:
        bad += [f"{name}: {v}" for v in _violations((HANDLERS / name).read_text())]
    assert not bad, "\n".join(bad)


def test_the_guard_can_fail():
    planted = ("def f(cfg, mqtt_settings):\n"
               "    x = cfg['password']\n"
               "    y = cfg.get('password')\n"
               "    save_mqtt_config(cfg)\n"
               "    mqtt_settings.save(1, cfg, 2)\n")
    assert len(_violations(planted)) == 4


# --- review r1 (contextless non-author, 2026-11-02) -------------------------------------

def _other_saves_first(cfg, other_menus, other_inputs, menus, inputs):
    """Screen A opens; screen B saves (other_*) before A's first prompt."""
    a, actx = _handler(menus=menus, inputs=inputs)
    other, _ = _handler(menus=other_menus, inputs=other_inputs)
    real_menu = actx.dialog.menu
    fired = []

    def menu(title, text, choices, **kw):
        if not fired:
            fired.append(1)
            other._configure_mqtt()
        return real_menu(title, text, choices, **kw)
    actx.dialog.menu = menu
    a._configure_mqtt()
    return actx


def test_r1_1_a_preset_pins_every_field_it_sets(cfg):
    """Opened on the public broker; another writer saves a private broker;
    'Use Public Broker' must not write the public password onto it."""
    _seed(cfg, {**ms.defaults()})
    ctx = _other_saves_first(cfg, ["broker", "save"], ["gt.example", ""],
                             ["public", "mine", "mine", "mine"], [])
    # the broker conflict was SHOWN and "mine" chosen: the whole preset lands
    assert [c for c in ctx.dialog.calls if c[0] == "menu" and c[1][0].startswith("Changed")]
    d = _disk(cfg)
    assert (d["broker"], d["username"]) == (ms.defaults()["broker"], "meshdev"), \
        "the public password was written against the private broker"


def test_r1_1_a_preset_over_a_changed_disk_is_a_shown_conflict(cfg):
    _seed(cfg, {**ms.defaults()})
    ctx = _other_saves_first(cfg, ["broker", "save"], ["gt.example", ""],
                             ["public", None, "cancel"], [])
    assert [c for c in ctx.dialog.calls if c[0] == "menu" and c[1][0].startswith("Changed")]
    assert _disk(cfg)["broker"] == "gt.example"


def test_r1_3_a_session_password_never_follows_to_another_username(cfg):
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler(menus=["auth", "auth", "save"], inputs=["alice", "pw1", "bob", ""])
    h._configure_mqtt()
    d = _disk(cfg)
    assert d["username"] == "bob" and d["password"] == "", "alice's password went to bob"
    assert len(_calls(ctx, "menu", "Password")) == 0, "Keep offered for another account"


def test_r1_3_a_session_password_never_follows_a_broker_row_change(cfg):
    _seed(cfg, {**A, "password": ""})
    h, ctx = _handler(menus=["auth", "broker", "save"],
                      inputs=["alice", "pw1", "mqtt.b.example", "pwB"])
    h._configure_mqtt()
    d = _disk(cfg)
    assert d["broker"] == "mqtt.b.example" and d["password"] == "pwB"


def test_r1_6_new_with_a_blank_entry_says_what_it_does_and_does_it(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["auth", "new", "save"], inputs=["alice", ""])
    h._configure_mqtt()
    prompt = [c for c in _calls(ctx, "inputbox") if c[1][0] == "Password"][0][1][1]
    assert "blank for none" not in prompt and "keep" in prompt.lower()
    assert _disk(cfg)["password"] == "s3cret"


def test_r1_7_esc_at_the_password_step_abandons_the_whole_auth_row(cfg):
    _seed(cfg)
    before = (cfg / ms.FILENAME).read_bytes()
    # username bob, then Esc at "Password for bob@…": the row is abandoned,
    # so Save asks nothing more and writes alice unchanged
    h, ctx = _handler(menus=["auth", "save"], inputs=["bob", None])
    h._configure_mqtt()
    asked = [c for c in _calls(ctx, "inputbox") if c[1][0].startswith("Password for")]
    assert len(asked) == 1, "the abandoned username came back at Save"
    assert _disk(cfg)["username"] == "alice" and _disk(cfg)["password"] == "s3cret"
    del before


def test_r1_8_a_busy_profile_apply_says_busy(cfg):
    _seed(cfg)
    from handlers.broker import BrokerHandler
    from utils.broker_profiles import BrokerProfile
    h = BrokerHandler()
    h.set_context(make_handler_context())
    p = BrokerProfile(name="t", broker_type="private", host="mqtt.p.example", port=1883)
    busy = ms.SaveResult(False, code="busy", error="another save is in progress — try again")
    with patch("monitoring.mqtt_subscriber.save_settings_changes", return_value=busy):
        h._apply_profile_to_mqtt(p)
    assert "another save is in progress" in h.ctx.dialog.last_msgbox_text


# --- review r2 -----------------------------------------------------------------------------

def _interleaved(cfg, a_menus, a_inputs, other_menus, other_inputs, at_title="MQTT Configuration", at_tag="save"):
    """Screen A runs; just before A's menu returns `at_tag`, screen B saves."""
    a, actx = _handler(menus=a_menus, inputs=a_inputs)
    other, _ = _handler(menus=other_menus, inputs=other_inputs)
    real = actx.dialog.menu
    done = []

    def menu(title, text, choices, **kw):
        tag = real(title, text, choices, **kw)
        if not done and title == at_title and tag == at_tag:
            done.append(1)
            other._configure_mqtt()
        return tag
    actx.dialog.menu = menu
    a._configure_mqtt()
    return actx


def test_r2_1_the_mismatch_prompt_names_the_written_account_and_does_not_loop(cfg):
    _seed(cfg)
    # B also CLEARED the password: Q17 asks about that first ("mine")
    ctx = _interleaved(cfg, ["auth", "new", "save", "mine"], ["alice", "pwA", "pwX"],
                       ["auth", "save"], ["xavier", ""])
    assert _calls(ctx, "menu", "Changed Elsewhere: password")
    asked = [c for c in _calls(ctx, "inputbox") if c[1][0].startswith("Password for")]
    assert len(asked) == 1 and "xavier" in asked[0][1][0], asked
    d = _disk(cfg)
    assert (d["username"], d["password"]) == ("xavier", "pwX")


def test_r2_2_keep_saved_broker_never_writes_the_other_brokers_password(cfg):
    _seed(cfg)
    # A: broker -> B, then Auth types pwB FOR alice@B; B's screen saves broker
    # C first; A keeps the saved broker (C) at the conflict — pwB must not
    # follow to C: A is asked for C's password instead
    # B also saved C's password: Q17 asks about that after the broker ("mine")
    ctx = _interleaved(cfg, ["broker", "auth", "save", "saved", "mine"],
                       ["mqtt.b.example", "alice", "pwB", "pwC2"],
                       ["broker", "save"], ["mqtt.c.example", "pwC"])
    assert _calls(ctx, "menu", "Changed Elsewhere: password")
    d = _disk(cfg)
    assert d["broker"] == "mqtt.c.example"
    assert d["password"] != "pwB", "B's password written for C"
    assert d["password"] == "pwC2"


def test_r2_3_no_handler_state_owns_a_password():
    src = (HANDLERS / "_mqtt_configure.py").read_text()
    assert "_pw_for" not in src, "the account a password belongs to lives WITH it (mqtt_settings.Secret)"


def test_r2_4_the_private_form_pins_every_answer(cfg):
    _seed(cfg)
    ctx = _interleaved(cfg, ["private", "auto", "mine"],
                       ["mqtt.c.example", "1883", "alice", "pwC", "msh/HI/2/e", "Regional"],
                       ["tls", "on", "save"], [], at_title="MQTT Configuration", at_tag="private")
    assert [c for c in ctx.dialog.calls if c[0] == "menu" and c[1][0] == "Changed Elsewhere: tls"]
    assert _disk(cfg)["tls"] == "auto"


def test_r2_8_esc_in_the_private_form_returns_to_the_list_keeping_its_edits(cfg):
    _seed(cfg)
    h, ctx = _handler(menus=["channel", "private", "save"], inputs=["Other", None])
    h._configure_mqtt()
    assert _disk(cfg)["channel"] == "Other"


# --- Q17 / Q18 (operator 2026-11-02 pm) ----------------------------------------------

def test_Q17_a_password_saved_elsewhere_is_a_shown_conflict_without_plaintext(cfg):
    _seed(cfg)
    a, actx = _handler()
    script = iter(["auth", "new", "save", "saved", "cancel"])
    inputs = iter(["alice", "pwA"])

    def menu(title, text, choices, **kw):
        actx.dialog.calls.append(("menu", (title, text, choices), kw))
        tag = next(script, None)   # exhausted = Esc
        if title == "MQTT Configuration" and tag == "save":
            # B saves while A is open (a full write: red on old code for
            # the behaviour, not a missing API)
            assert ms.save(cfg, {**A, "password": "pwB"}, W).ok
        return tag
    actx.dialog.menu = menu
    actx.dialog.inputbox = lambda title, text, init="", **kw: next(inputs, None)
    a._configure_mqtt()
    conf = _calls(actx, "menu", "Changed Elsewhere: password")
    assert conf, "the password conflict was not shown"
    shown = repr(conf)
    for p in ("pwA", "pwB", "s3cret"):
        assert p not in shown, f"plaintext {p!r} shown"
    assert _disk(cfg)["password"] == "pwB", "'keep the saved' lost B's password"


def test_Q18_an_abandoned_conflict_returns_to_a_fresh_read(cfg):
    """A resolved one conflict (base mutated) then Esc'd the next; B had
    also CLEARED the password. Back at the list, the auth row must not offer
    'Keep the saved password' — none is saved any more (r3 #5)."""
    _seed(cfg)
    a, actx = _handler()
    script = iter(["tls", "on", "port", "save", "mine", None,
                   "auth", "cancel"])
    inputs = iter(["1884", "alice", ""])

    def menu(title, text, choices, **kw):
        actx.dialog.calls.append(("menu", (title, text, choices), kw))
        tag = next(script, None)   # exhausted = Esc
        if title == "MQTT Configuration" and tag == "save":
            # B's write needs nothing Q17 adds: this test is red on old code
            # for the BEHAVIOUR (a stale password_saved), not a missing API
            assert ms.save(cfg, {**A, "tls": False, "port": 1885, "password": ""}, W).ok
        return tag
    actx.dialog.menu = menu
    actx.dialog.inputbox = lambda title, text, init="", **kw: next(inputs)
    a._configure_mqtt()
    assert _calls(actx, "menu", "Changed Elsewhere: tls")
    assert _calls(actx, "menu", "Changed Elsewhere: port")
    assert not _calls(actx, "menu", "Password"), \
        "offered to keep a saved password that another screen cleared"
    d = _disk(cfg)
    assert (d["tls"], d["port"], d["password"]) == (False, 1885, ""), "Esc wrote"

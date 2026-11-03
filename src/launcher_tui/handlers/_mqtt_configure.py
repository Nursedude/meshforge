"""MQTT Configure screens — they send CHANGES, never a snapshot (F8 grill).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md``. Every screen
used to save a full snapshot it loaded earlier, so a stale screen overwrote
another writer's save and the password travelled with whichever screen held
it. Now:

* a screen holds ``mqtt_settings.editable()`` — the intent WITHOUT the
  password (Q3) — and a ``changes`` dict of what the operator edited;
* every write goes through ``_commit`` -> ``save_mqtt_changes`` (Q2/Q16),
  whose refusals are answered here: a new account's password is ASKED before
  anything is written (Q7), a field both sides changed is shown with both
  values (Q10), an unreadable file offers an explicit Reset (Q15);
* Esc at any prompt of a form abandons it (Q5); the Configure list's TLS row
  opens the same menu as the private form (Q6).

Mixed into ``MQTTHandler``; uses its ``ctx`` and ``_detect_local_channel``.
"""
import logging
from typing import Any, Dict, Optional

from utils.mqtt_defaults import (
    MESHTASTIC_PUBLIC_PASSWORD,
    MESHTASTIC_PUBLIC_USERNAME,
)

logger = logging.getLogger(__name__)

AUTO_KEYS = ("auto_start", "auto_start_telemetry")

# _ask_password's "no change" — distinct from None (Esc), which a FORM must
# treat as abandon (Q5) and the Configure list as "row abandoned".
KEEP = object()


# --- the one door to the file (Q16) ------------------------------------------

def _fresh_view():
    """(view, base): a fresh read and a private copy of its intent — taken
    TOGETHER, so base and password_saved/password_fp never disagree (Q18)."""
    view = load_mqtt_editable()
    return view, dict(view.intent)


def load_mqtt_editable():
    """The screen's view: ``mqtt_settings.Editable`` (no password)."""
    from monitoring import mqtt_settings
    from monitoring.mqtt_subscriber import load_settings
    view = mqtt_settings.editable(load_settings())
    if view.state == "unreadable":
        logger.warning("Saved MQTT settings unreadable — showing defaults: %s",
                       view.error)
    return view


def save_mqtt_changes(base: Dict[str, Any], changes: Dict[str, Any],
                      reset: bool = False, password_fp: Optional[str] = None):
    """``mqtt_settings.SaveResult``; refusals are logged, never "Saved" (#74).
    ``reset`` = the explicit Reset over an unreadable file (Q15) — the same
    locked, account-checked door, never a second full write (review r3 #1).
    ``password_fp`` = the view's ``password_fp`` (Q17); the owner refuses a
    password sent without it."""
    from monitoring.mqtt_subscriber import save_settings_changes
    res = save_settings_changes(base, changes, reset=reset,
                                base_password_fp=password_fp)
    if not res.ok:
        logger.warning("MQTT settings NOT saved (%s%s) — %s", res.code,
                       ", reset" if reset else "", res.error)
    return res


# --- TLS: one control, one vocabulary (Q6, hfm #5) -----------------------------

# A tag the menu lacks degrades to row 1 SILENTLY in whiptail, so a test pins
# every saved value to a row tag.
_TLS_FROM_TAG = {"auto": "auto", "on": True, "off": False}


def _tls_menu_rows(broker: str, port: int):
    from monitoring import mqtt_settings
    auto_on = mqtt_settings.effective_tls({"broker": broker, "port": port, "tls": "auto"})
    return [("auto", f"Auto -> {'on' if auto_on else 'off'} for port {port}"),
            ("on", "On   (TLS encrypted)"),
            ("off", "Off  (plain TCP)")]


def _tls_tag(value: Any) -> Optional[str]:
    """tls value -> menu tag; None for anything not a valid tls (`is` for the
    bools: 1 == True, and a 1 on disk was never a choice the form offered)."""
    for tag, v in _TLS_FROM_TAG.items():
        if v is value or (isinstance(v, str) and v == value):
            return tag
    return None


def _saved_tls_for_endpoint(broker: str, port: int) -> Optional[str]:
    """The menu tag of the tls SAVED ON DISK for this same broker:port, else
    None — read fresh. Marks the "(saved)" row only; the highlighted row is
    the screen's current value (an unsaved edit is now honoured — the
    changes model tells an edit from a carried value). A legacy file's tls is
    a CONVERSION, never claimed "(saved)". absent/unreadable -> None."""
    from monitoring import mqtt_settings
    from monitoring.mqtt_subscriber import load_settings
    loaded = load_settings()
    disk = loaded.intent if loaded.state == "ok" and not loaded.legacy else None
    if not disk or mqtt_settings.host_key(disk.get("broker")) \
            != mqtt_settings.host_key(broker) or disk.get("port") != port:
        return None
    return _tls_tag(disk.get("tls"))


_ROOT_HELP = ("MQTT root topic (must end in /e — the JSON leg is derived from it):\n\n"
              "  msh/US/2/e   = US region\n"
              "  msh/EU_868/2/e = EU 868 region\n\n"
              "Your meshtasticd MQTT module must use the same root topic.")
_CHANNEL_HELP = ("Meshtastic channel to subscribe to (must match your radio):\n\n"
                 "  LongFast   = Default Meshtastic channel\n"
                 "  <regional> = Your regional/community channel name\n"
                 "  +          = every channel (local broker)")


def _shown(base: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    """What the screen shows: the opened intent plus its edits (no password).
    ONE derivation (review r2 #9, hfm #5)."""
    from monitoring import mqtt_settings
    return {**base, **mqtt_settings.public_view(changes)}


def _secret(changes: Dict[str, Any]):
    """The typed password in ``changes`` (an ``mqtt_settings.Secret``) or None."""
    from monitoring import mqtt_settings
    s = changes.get("password")
    return s if isinstance(s, mqtt_settings.Secret) else None


def _put_password(changes: Dict[str, Any], value: str, for_account) -> None:
    """Record a typed password WITH the account it was typed for."""
    from monitoring import mqtt_settings
    changes["password"] = mqtt_settings.Secret(value, for_account)


def _set(base: Dict[str, Any], changes: Dict[str, Any], key: str, value: Any) -> None:
    """Record an edit; an edit back to the opened value is no change (so an
    Enter-through never claims a field another writer may have saved)."""
    from monitoring import mqtt_settings
    if key != "password" and mqtt_settings.same(base.get(key), value):
        changes.pop(key, None)
    else:
        changes[key] = value


class MQTTConfigureMixin:
    """The Configure list, the private-broker form, and the commit loop."""

    # -- the Configure list ------------------------------------------------

    def _configure_mqtt(self):
        """Configure MQTT broker settings — edits are CHANGES until Save."""
        from monitoring import mqtt_settings
        view, base = _fresh_view()
        changes: Dict[str, Any] = {}

        while True:
            shown = _shown(base, changes)
            dirty = bool(changes)
            broker, port, tls = shown["broker"], shown["port"], shown["tls"]
            tls_txt = {True: "on", False: "off"}.get(tls) or (
                f"auto -> {'on' if mqtt_settings.effective_tls(shown) else 'off'}")
            auto_status = "ON" if shown.get("auto_start") else "OFF"
            telem_status = "ON" if shown.get("auto_start_telemetry", True) else "OFF"

            choices = [
                ("local", "Use Local Broker    Quick: localhost:1883"),
                ("public", "Use Public Broker   Quick: mqtt.meshtastic.org"),
                ("private", "Use Private Broker  Custom: your own broker"),
                ("broker", f"Broker              {broker}"),
                ("port", f"Port                {port}"),
                ("root", f"Root Topic          {shown['root_topic']}"),
                ("channel", f"Channel             {shown['channel']}"),
                ("tls", f"TLS                 {tls_txt}"),
                ("auth", "Authentication      Username/password"),
                ("autostart", f"Auto-Start          [{auto_status}] Start on TUI launch"),
                ("autotelem", f"Auto Telemetry      [{telem_status}] Poll silent nodes"),
                ("save", "Save & Exit"),
                ("cancel", "Cancel              (discard unsaved changes)"
                 if dirty else "Cancel"),
            ]
            subtitle = "Configure MQTT broker connection:"
            if dirty:
                subtitle += ("\n\n* UNSAVED CHANGES — 'Save & Exit' writes them, "
                             "'Cancel' discards them.")
            choice = self.ctx.dialog.menu("MQTT Configuration", subtitle, choices)

            if choice is None or choice == "cancel":
                break

            kept_auto = {k: changes[k] for k in AUTO_KEYS if k in changes}
            if choice == "local":
                preset = {**mqtt_settings.defaults(), "broker": "localhost",
                          "port": 1883, "root_topic": "msh/2/e", "channel": "+"}
                chan_note = self._local_channel_note(preset)
                if self._commit_preset(view, base, preset, "", kept_auto, "Local Mode Set",
                                       "Configured for the local mosquitto broker.\n"
                                       f"{chan_note}\n\n"
                                       "Make sure mosquitto is running and meshtasticd's\n"
                                       "MQTT module is configured (Service Config ->\n"
                                       "MQTT Setup)."):
                    break
                view, base = _fresh_view()       # Q18: never a half-resolved base
                continue

            elif choice == "public":
                preset = {**mqtt_settings.defaults(),
                          "username": MESHTASTIC_PUBLIC_USERNAME}
                if self._commit_preset(view, base, preset, MESHTASTIC_PUBLIC_PASSWORD,
                                       kept_auto, "Public Mode Set",
                                       "Configured for the public Meshtastic broker —\n"
                                       "nodeless monitoring, no local radio needed."):
                    break
                view, base = _fresh_view()       # Q18
                continue

            elif choice == "private":
                if self._configure_private_broker(view, base, changes):
                    break
                # Esc/refused: the list keeps its edits, over a FRESH read (Q18)
                view, base = _fresh_view()
                continue

            elif choice == "broker":
                new_broker = self.ctx.dialog.inputbox(
                    "MQTT Broker", "Enter MQTT broker hostname:", init=broker)
                new_broker = (new_broker or "").strip()
                if new_broker:
                    if not self.ctx.validate_hostname(new_broker):
                        self.ctx.dialog.msgbox(
                            "Invalid Host",
                            f"'{new_broker}' is not a valid hostname or IP.")
                        continue
                    _set(base, changes, "broker", new_broker)

            elif choice == "port":
                new_port = self.ctx.dialog.inputbox(
                    "MQTT Port", "Enter MQTT port (8883 for TLS, 1883 for plain):",
                    init=str(port))
                if new_port:
                    if not self.ctx.validate_port(new_port):
                        self.ctx.dialog.msgbox(
                            "Invalid Port",
                            "Port must be a number between 1 and 65535.")
                        continue
                    _set(base, changes, "port", int(new_port))

            elif choice in ("root", "channel"):
                key = "root_topic" if choice == "root" else "channel"
                val = self.ctx.dialog.inputbox(
                    "Root Topic" if choice == "root" else "Channel",
                    _ROOT_HELP if choice == "root" else _CHANNEL_HELP,
                    init=shown[key])
                if val:
                    _set(base, changes, key, val.strip())

            elif choice == "tls":
                new = self._ask_private_tls(broker, port,
                                            _saved_tls_for_endpoint(broker, port),
                                            current_tag=_tls_tag(tls), in_form=False)
                if new is not None:
                    _set(base, changes, "tls", new)

            elif choice == "auth":
                username = self.ctx.dialog.inputbox(
                    "Username", "Enter MQTT username (blank for anonymous):",
                    init=shown.get("username", ""))
                if username is None:
                    continue
                before = dict(changes)
                _set(base, changes, "username", username.strip())
                shown = {**shown, "username": username.strip()}
                pw = self._ask_password(view, base, changes, shown)
                if pw is None:                   # Esc: the whole row (review r1 #7)
                    changes.clear()
                    changes.update(before)
                elif isinstance(pw, str):
                    _put_password(changes, pw, mqtt_settings.account(shown))

            elif choice in ("autostart", "autotelem"):
                key = "auto_start" if choice == "autostart" else "auto_start_telemetry"
                _set(base, changes, key, not shown.get(key, key != "auto_start"))
                new_state = "ENABLED" if {**base, **changes}[key] else "DISABLED"
                what = ("MQTT auto-start" if key == "auto_start"
                        else "TelemetryPoller auto-start")
                self.ctx.dialog.msgbox(
                    "Auto-Start" if key == "auto_start" else "Auto Telemetry",
                    f"{what} will be {new_state} after 'Save & Exit'.\n\n"
                    "Nothing is written yet — 'Cancel' discards this change.")

            elif choice == "save":
                if self._commit(view, base, changes, "Saved", "MQTT configuration saved.\n\n"
                                "Restart the subscriber for changes to take effect."):
                    break
                # not saved (a conflict abandoned, a refusal): back to the list
                # over a FRESH read — base and password_saved/password_fp
                # re-derived together, never a half-mutated base (Q18, r3 #5)
                view, base = _fresh_view()

    def _local_channel_note(self, preset: Dict[str, Any]) -> str:
        from monitoring import mqtt_settings
        found = self._detect_local_channel()
        if found and not mqtt_settings.validate({**preset, "channel": found}):
            preset["channel"] = found
            return f"Channel: {found} (read from the radio)"
        if found:   # not a usable topic segment (review F5)
            logger.warning("Detected channel %r is not a topic segment — "
                           "listening on every channel (+)", found)
            return (f"Channel: radio's name {found!r} is not usable "
                    "in a topic — listening on every channel (+)")
        if found == "":
            return ("Channel: the radio's primary is unnamed (preset "
                    "name) — listening on every channel (+)")
        return "Channel: could NOT read the radio — listening on every channel (+)"

    def _commit_preset(self, view, base, preset, password, kept_auto, title, body) -> bool:
        """A preset is an explicit choice of EVERY field it sets, its
        credentials included — pinned, never dropped as "equal to what the
        screen opened", so a disk another writer changed meanwhile is a
        SHOWN conflict, not a silent merge of the preset's password onto
        their broker (review r1 #1). Un-set fields (auto_start…) are untouched."""
        from monitoring import mqtt_settings
        changes = {k: v for k, v in mqtt_settings.public_view(preset).items()
                   if k not in AUTO_KEYS}
        _put_password(changes, password, mqtt_settings.account(preset))
        changes.update(kept_auto)
        return self._commit(view, base, changes, title, body)

    # -- the password step (Q3/Q7/Q8) ----------------------------------------

    def _ask_password(self, view, base, changes, shown):
        """A string for ``changes["password"]`` ("" = clear), ``KEEP`` = no
        change, or None = Esc.

        Never shows or holds the saved password: offers Keep only when one is
        held for THIS account; a different account gets the question."""
        from monitoring import mqtt_settings
        acct = mqtt_settings.account_label(shown)
        typed = _secret(changes)
        if typed and typed.account != mqtt_settings.account(shown):
            changes.pop("password")              # typed for another account (r1 #3)
            typed = None
        if typed:
            held, label = bool(typed.value), "entered this session"
        else:
            same = mqtt_settings.account(shown) == mqtt_settings.account(base)
            held, label = view.password_saved and same, "saved"
            if view.password_saved and not same:
                return self.ctx.dialog.inputbox(
                    f"Password for {acct}",
                    f"The saved password belongs to "
                    f"{mqtt_settings.account_label(base)}.\n\n"
                    f"Password for {acct} (blank = no password):", init="")
        if held:
            pick = self.ctx.dialog.menu(
                "Password", f"A password is {label} for {acct}.",
                [("keep", f"Keep the {label} password"),
                 ("new", "Enter a new password"),
                 ("clear", "Clear it (no password)")], default_item="keep")
            if pick == "clear":
                return ""
            if pick is None:
                return None
            if pick != "new":
                return KEEP
        hint = ("blank = keep the current one; choose Clear to remove it"
                if held else "blank for none")
        new = self.ctx.dialog.inputbox(
            "Password", f"MQTT password for {acct} ({hint}):", init="")
        if new is None:
            return None
        return new if new else KEEP              # blank: nothing to set or clear

    # -- the commit loop: answer every refusal, write nothing silently --------

    def _commit(self, view, base: Dict[str, Any], changes: Dict[str, Any],
                title: str, body: str) -> bool:
        """Save ``changes`` over ``base`` (the view's intent, maybe resolved);
        ``view.password_fp`` says which saved password the screen opened with
        (Q17). Works on ``base`` and a local fingerprint only — a False
        return means the caller re-reads (Q18)."""
        from monitoring import mqtt_settings
        reset = False
        password_fp = view.password_fp
        while True:
            res = save_mqtt_changes(base, changes, reset=reset,
                                    password_fp=password_fp)
            if res.ok:
                self._report_saved(res.intent, title, body)
                return True
            if res.code == "password_owner_mismatch":
                # the owner names the account being WRITTEN; the answer is
                # bound to exactly it, so the retry cannot refuse again (r2 #1)
                acct = mqtt_settings.label_of(res.account)
                pw = self.ctx.dialog.inputbox(
                    f"Password for {acct}",
                    f"{res.error}\n\nPassword for {acct} (blank = no password):",
                    init="")
                if pw is None:
                    return False
                _put_password(changes, pw, res.account)
                continue
            if res.code == "conflict":
                for k, (saved, yours) in res.conflicts.items():
                    pick = self.ctx.dialog.menu(
                        f"Changed Elsewhere: {k}",
                        f"'{k}' was saved by another screen since this one "
                        f"opened.\n\n  saved: {_show(saved)}\n  yours: {_show(yours)}",
                        [("mine", f"Use mine ({_show(yours)})"),
                         ("saved", f"Keep the saved value ({_show(saved)})")])
                    if pick not in ("mine", "saved"):
                        return False
                    if k == "password":          # Q17: answer with what is saved NOW
                        password_fp = res.password_fp
                    else:
                        base[k] = saved
                    if pick == "saved":
                        changes.pop(k, None)
                continue
            if res.code == "unreadable":
                pick = self.ctx.dialog.menu(
                    "Settings Unreadable", res.error,
                    [("back", "Back — write nothing"),
                     ("reset", "Reset — rebuild the file from this screen")])
                if pick != "reset":
                    return False
                # the owner re-checks under its lock: a file fixed meanwhile
                # takes the normal path (conflicts asked), never overwritten
                reset = True
                continue
            if res.code == "invalid":        # the field is named (MQTT-CFG)
                self.ctx.dialog.msgbox("Not Saved", "The settings were NOT saved:\n\n"
                                       + "\n".join(res.errors))
            else:                            # never "Saved" for nothing (#74)
                self.ctx.report_action(
                    False, title, "", "Save Failed",
                    "MQTT config could not be written — your change was NOT "
                    f"saved.\n\n{res.error or res.code}")
            return False

    def _report_saved(self, intent: Dict[str, Any], title: str, body: str) -> None:
        from monitoring import mqtt_settings
        cfg = mqtt_settings.subscriber_config(intent)
        self.ctx.report_action(
            True, title,
            f"{body}\n\n  Broker: {cfg['broker']}:{cfg['port']}"
            f"  TLS: {'on' if cfg['use_tls'] else 'off'}\n  Will subscribe:\n"
            + "\n".join(f"    {t}" for t in mqtt_settings.subscriptions(cfg)),
            "Save Failed", "")

    # -- the private-broker form (Esc anywhere = nothing written, Q5) ----------

    def _configure_private_broker(self, view, base, changes) -> bool:
        """Guided setup for a private MQTT broker — True iff saved.

        Every question it asks is an explicit ANSWER, pinned in the form's
        changes even when equal to the opened value (review r2 #4, as the
        presets): another writer's value for an asked field is a SHOWN
        conflict, never silently merged in."""
        from monitoring import mqtt_settings
        shown = _shown(base, changes)
        form = dict(changes)
        broker = self.ctx.dialog.inputbox(
            "Broker Address",
            "Enter your private MQTT broker hostname or IP:\n\n"
            "Examples:\n  gt.wildc.net\n  192.168.1.100\n  mqtt.local\n\n"
            "Esc/Cancel at any step abandons the setup (nothing saved).",
            init=shown.get("broker", ""))
        broker = (broker or "").strip()
        if not broker:
            return False
        if not self.ctx.validate_hostname(broker):
            self.ctx.dialog.msgbox("Invalid Host",
                                   f"'{broker}' is not a valid hostname or IP.")
            return False
        form["broker"] = broker

        port = self.ctx.dialog.inputbox(
            "Broker Port",
            "Enter MQTT port:\n\n  1883 = Plain TCP (usual)\n"
            "  1884 = Alternative plain TCP\n  8883 = TLS (usual)\n\n"
            "You choose TLS on/off/auto next.",
            init=str(shown.get("port", 1883)))
        if not port:
            return False
        if not self.ctx.validate_port(port):
            self.ctx.dialog.msgbox("Invalid Port",
                                   "Port must be a number between 1 and 65535.")
            return False
        port_n = int(port)
        form["port"] = port_n

        username = self.ctx.dialog.inputbox(
            "Username", "MQTT username (blank for anonymous):",
            init=shown.get("username", ""))
        if username is None:
            return False
        form["username"] = username.strip()

        pw = self._ask_password(view, base, form, _shown(base, form))
        if pw is None:
            return False
        if isinstance(pw, str):
            _put_password(form, pw, mqtt_settings.account(_shown(base, form)))

        # TLS is ASKED for this broker:port (mqtt-private-form-resets-tls); the
        # screen's tls is highlighted only for the SAME endpoint — it never
        # follows the operator to another broker (TLS review r1 #1)
        same_endpoint = (mqtt_settings.host_key(shown["broker"]), shown["port"]) \
            == (mqtt_settings.host_key(broker), port_n)
        tls = self._ask_private_tls(
            broker, port_n, _saved_tls_for_endpoint(broker, port_n),
            current_tag=_tls_tag(shown.get("tls")) if same_endpoint else None)
        if tls is None:
            return False
        form["tls"] = tls

        root_topic = self.ctx.dialog.inputbox("Root Topic", _ROOT_HELP,
                                              init=shown.get("root_topic", "msh/US/2/e"))
        if root_topic is None:
            return False
        form["root_topic"] = root_topic.strip() or "msh/US/2/e"

        channel = self.ctx.dialog.inputbox(
            "Channel Name",
            "Meshtastic channel to subscribe to:\n\n"
            "  LongFast   = Default Meshtastic channel\n"
            "  <regional> = Your regional/community channel name\n"
            "  meshforge  = Private MeshForge channel\n\n"
            "Must match your radio's channel configuration.",
            init=shown.get("channel", "LongFast"))
        if channel is None:
            return False
        form["channel"] = channel.strip() or "LongFast"

        return self._commit(view, base, form, "Private Broker Configured",
                     f"Root topic '{root_topic.strip() or 'msh/US/2/e'}' determines "
                     "node scope.\nRestart the MQTT subscriber to apply.")

    def _ask_private_tls(self, broker: str, port: int, saved_tag: Optional[str] = None,
                         current_tag: Optional[str] = None, in_form: bool = True):
        """TLS for this broker:port — "auto" / True / False, or None = Esc.

        One control for the form AND the Configure list (Q6). Rows never
        reorder; the highlighted row is the screen's current value (else the
        saved one, else row 1); the saved row is marked "(saved)"."""
        rows = _tls_menu_rows(broker, port)
        if saved_tag is not None:
            rows = [(t, f"{d}  (saved)" if t == saved_tag else d) for t, d in rows]
        tail = ("Esc/Cancel abandons the private-broker setup (nothing saved)."
                if in_form else "Esc/Cancel leaves TLS unchanged.")
        choice = self.ctx.dialog.menu(
            "TLS", f"Encrypt the connection to {broker}:{port}?\n\n{tail}",
            rows, default_item=current_tag or saved_tag)
        return _TLS_FROM_TAG.get(choice)


def _show(v: Any) -> str:
    return {True: "on", False: "off"}.get(v, str(v)) if isinstance(v, bool) else str(v)

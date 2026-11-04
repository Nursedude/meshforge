"""The MQTT settings editor — one editor at a time (F8 v2, edit LEASE).

Grill record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § F8 v2
(V1-V14). Entering MQTT Configuration takes the edit lease; every screen
opened from it (presets, the private-broker form, the TLS menu) uses that
lease (V5). A second session gets a VIEW-ONLY screen naming the holder, or
"an unknown session" when its record cannot be trusted — never "free" (V6,
V12). Save refuses when the file changed without the lease (V7), offers
Reset when it no longer loads (Q15/V2), and never shows or carries the saved
password: the screen holds ``mqtt_settings.KEEP`` (Q3); KEEP for a different
account is asked (Q7); a preset or profile that would replace or clear a
saved password asks first (Q19). Esc never writes (Q5).

``MQTTConfigFormMixin`` is mixed into ``MQTTHandler``; ``open_edit_lease``,
``show_view_only`` and ``save_with_prompts`` are shared with BrokerHandler's
profile apply (its own short lease — it is not opened from this menu).
"""

import copy
import logging
from typing import Any, Dict, Optional

from utils.mqtt_defaults import (
    MESHTASTIC_PUBLIC_PASSWORD,
    MESHTASTIC_PUBLIC_USERNAME,
)

logger = logging.getLogger(__name__)

# save_with_prompts outcomes
SAVED, REFUSED, STALE = "saved", "refused", "stale"

_ROOT_HELP = ("MQTT root topic (must end in /e — the JSON leg is derived from it):\n\n"
              "  msh/US/2/e   = US region\n"
              "  msh/EU_868/2/e = EU 868 region\n\n"
              "Your meshtasticd MQTT module must use the same root topic.")
_CHANNEL_HELP = ("Meshtastic channel to subscribe to (must match your radio):\n\n"
                 "  LongFast   = Default Meshtastic channel\n"
                 "  <regional> = Your regional/community channel name\n"
                 "  +          = every channel (local broker)")


def _who(intent: Dict[str, Any]) -> str:
    from monitoring import mqtt_settings
    host, user = mqtt_settings.account(intent)
    return f"{user}@{host}" if user else f"(no user)@{host}"


def _holder_line(result) -> str:
    h = result.holder
    if h is None:
        return ("MQTT settings are being edited by an unknown session (its "
                "lease record could not be read).")
    return (f"MQTT settings are being edited by {h.user} "
            f"(pid {h.pid}, {h.tty}, since {h.since}).")


def _summary() -> str:
    """The saved settings, read-only — the password never shown."""
    from monitoring.mqtt_subscriber import load_settings
    loaded = load_settings()
    if loaded.state == "unreadable":
        return f"Saved settings: UNREADABLE — {loaded.error}"
    if loaded.state == "absent":
        return "Saved settings: none (public defaults)"
    i = loaded.intent
    tls = {True: "on", False: "off"}.get(i["tls"], "auto")
    return (f"Broker:   {i['broker']}:{i['port']}  TLS: {tls}\n"
            f"Topic:    {i['root_topic']}  Channel: {i['channel']}\n"
            f"User:     {i['username'] or '(anonymous)'}  Password: "
            f"{'(saved)' if i['password'] else '(none)'}")


def show_view_only(ctx, result, title: str) -> None:
    """A second opener (V6): who holds it, what is saved, nothing editable."""
    if result.state == "busy":
        head = _holder_line(result) + "\n\nView only — close that session to edit."
    else:
        head = ("MQTT settings cannot be opened for editing:\n"
                f"{result.error}\n\nView only.")
    ctx.dialog.msgbox(f"{title} — View Only", f"{head}\n\n{_summary()}")


def open_edit_lease(ctx, title: str):
    """The held lease, or None after showing the view-only screen."""
    from .mqtt import acquire_mqtt_edit_lease
    result = acquire_mqtt_edit_lease()
    if result.state == "held":
        return result.lease
    show_view_only(ctx, result, title)
    return None


def confirm_password_change(ctx, lease, new_password: str, what: str) -> bool:
    """Q19: replacing or clearing a saved password is asked, naming whose."""
    from monitoring import mqtt_settings
    relation = mqtt_settings.saved_password_relation(lease, new_password)
    if relation in ("none", "same"):
        return True
    if relation == "unknown":
        # unreadable is not "no password" (review r1 #3, hfm #2)
        return bool(ctx.dialog.yesno(
            "Saved Password",
            f"The saved settings file cannot be read, so whether it holds a "
            f"password cannot be told. {what} would replace any saved "
            "password.\n\nContinue?"))
    verb = "CLEARS" if new_password == "" else "REPLACES"
    owner = lease.saved_account
    who = f"{owner[1] or '(no user)'}@{owner[0]}" if owner else "the saved account"
    return bool(ctx.dialog.yesno(
        "Saved Password",
        f"{what} {verb} the saved password for {who}.\n\nContinue?"))


def unreadable_refusal(fail_note: str, error: str) -> str:
    """The one refusal text for a caller with no settings screen (r2 F1).

    States only what this path measured: the file did not load (`error`
    says why — damaged, or written by a NEWER version) and nothing was
    written. No backup is claimed: none is made here (review F1 #4)."""
    return (f"{fail_note}: the saved MQTT settings file does not load.\n\n"
            f"{error}\n\nNothing was written. Open MQTT Configuration to "
            "review it; if the file is damaged, Reset is offered there.")


def save_with_prompts(ctx, lease, intent: Dict[str, Any], title: str, body: str,
                      report_success: bool = True, fail_title: str = "Save Failed",
                      fail_note: str = "MQTT settings were NOT saved",
                      settings_screen: bool = False) -> str:
    """Save under the lease; answer each refusal with the question it needs.

    SAVED / REFUSED (nothing written; the screen may continue) / STALE (the
    file changed without the lease — the screen must close and reopen).

    ``settings_screen=True`` only from a screen that SHOWS the settings it
    saves (MQTT Configuration and its nested forms): only those may offer
    Reset/rebuild. The default refuses instead — a caller with no screen
    (profile apply) would write values nobody was shown (r2 F1; the safe
    default is review F1 #7)."""
    from monitoring import mqtt_settings
    from .mqtt import save_mqtt_config
    intent = dict(intent)
    reset = False
    for _ in range(6):     # each answer removes one refusal; bounded anyway
        res = save_mqtt_config(intent, lease, reset=reset)
        if res.ok:
            if report_success:
                shown = dict(intent)
                if shown.get("password") is mqtt_settings.KEEP:
                    shown["password"] = ""
                cfg = mqtt_settings.subscriber_config(shown)
                ctx.dialog.msgbox(
                    title, f"{body}\n\n  Broker: {cfg['broker']}:{cfg['port']}"
                    f"  TLS: {'on' if cfg['use_tls'] else 'off'}\n  Will subscribe:\n"
                    + "\n".join(f"    {t}" for t in mqtt_settings.subscriptions(cfg)))
            return SAVED
        if res.code == "invalid":
            ctx.dialog.msgbox("Not Saved", "The settings were NOT saved:\n\n"
                              + "\n".join(res.errors))
            return REFUSED
        broke = (res.code == "unreadable"
                 or (res.code == "changed_without_lease" and res.now_unreadable))
        if broke and not settings_screen:
            ctx.dialog.msgbox(fail_title, unreadable_refusal(fail_note, res.error))
            return REFUSED
        if res.code == "unreadable" and not reset:
            if not ctx.dialog.yesno(
                    "Settings Unreadable",
                    f"{res.error}\n\nReset writes THIS screen's settings; the "
                    "unreadable file is kept as a backup.\n\nReset now?"):
                return REFUSED
            reset = True
            continue
        if res.code == "changed_without_lease":
            if res.now_unreadable and not reset:
                if not ctx.dialog.yesno(
                        "Settings File Broke",
                        f"{res.error}\n\nRebuild it from THIS screen? The broken "
                        "file is kept as a backup."):
                    return REFUSED
                reset = True
                continue
            if settings_screen:
                ctx.dialog.msgbox(
                    "Not Saved — Changed Elsewhere",
                    f"{res.error}\n\nNothing was written. Leave MQTT Configuration "
                    "and open it again to see the current settings.")
            else:          # review F1 #3: no Configuration screen to leave
                ctx.dialog.msgbox(
                    fail_title,
                    f"{fail_note}: the settings file changed while this ran.\n\n"
                    f"{res.error}\n\nNothing was written. Try again to apply "
                    "over the current settings.")
            return STALE
        if res.code in ("password_owner_mismatch", "password_unknown"):
            typed = ctx.dialog.inputbox("Password", res.error, init='')
            if typed is None:
                return REFUSED
            intent["password"] = typed
            continue
        ctx.dialog.msgbox(fail_title, f"{fail_note}:\n\n{res.error}")
        return REFUSED
    ctx.dialog.msgbox(fail_title, f"{fail_note} (too many refusals in a row).")
    return REFUSED


class MQTTConfigFormMixin:
    """Configure / presets / private broker / TLS — under one edit lease."""

    def _configure_mqtt(self):
        lease = open_edit_lease(self.ctx, "MQTT Configuration")
        if lease is None:
            return
        with lease:
            self._configure_mqtt_held(lease)

    def _password_label(self, lease, value: Any) -> str:
        from monitoring import mqtt_settings
        if value is mqtt_settings.KEEP:
            return "(saved)" if lease.password_saved else "(none)"
        return "(new, unsaved)" if value else "(none)"

    def _ask_password(self, lease, current: Any):
        """KEEP / a new string / "" — or None = Esc (nothing changes)."""
        from monitoring import mqtt_settings
        rows = []
        if lease.password_saved:
            rows.append(("keep", "Keep the saved password"))
        rows += [("new", "Enter a new password"), ("clear", "No password")]
        choice = self.ctx.dialog.menu(
            "Password", f"Now: {self._password_label(lease, current)}", rows)
        if choice == "keep":
            return mqtt_settings.KEEP
        if choice == "clear":
            return ""
        if choice == "new":
            return self.ctx.dialog.inputbox(
                "Password", "MQTT password (blank for none):", init='')
        return None

    def _configure_mqtt_held(self, lease):
        from monitoring import mqtt_settings
        config = dict(lease.view)
        # Edits only touch this dict; nothing reaches disk until a Save. The
        # lease means nobody else can change the file meanwhile, so this
        # screen cannot go stale (V1).
        on_disk = copy.copy(config)

        while True:
            dirty = config != on_disk
            broker = config['broker']
            port = config['port']
            tls = config['tls']
            tls_txt = {True: "on", False: "off"}.get(tls) or (
                f"auto -> {'on' if mqtt_settings.effective_tls(config) else 'off'}")
            auto_status = "ON" if config['auto_start'] else "OFF"
            telem_status = "ON" if config['auto_start_telemetry'] else "OFF"
            user = config['username'] or "(anonymous)"

            choices = [
                ("local", "Use Local Broker    Quick: localhost:1883"),
                ("public", "Use Public Broker   Quick: mqtt.meshtastic.org"),
                ("private", "Use Private Broker  Custom: your own broker"),
                ("broker", f"Broker              {broker}"),
                ("port", f"Port                {port}"),
                ("root", f"Root Topic          {config['root_topic']}"),
                ("channel", f"Channel             {config['channel']}"),
                ("tls", f"TLS                 {tls_txt}"),
                ("auth", f"Authentication      {user} / "
                 f"{self._password_label(lease, config['password'])}"),
                ("autostart", f"Auto-Start          [{auto_status}] Start on TUI launch"),
                ("autotelem", f"Auto Telemetry      [{telem_status}] Poll silent nodes"),
                ("save", "Save & Exit"),
                ("cancel", "Cancel              (discard unsaved changes)"
                 if dirty else "Cancel"),
            ]

            subtitle = "Configure MQTT broker connection:"
            if lease.loaded_state == "unreadable":
                subtitle += ("\n\nSaved file UNREADABLE — showing defaults; "
                             "Save offers a Reset.")
            if dirty:
                subtitle += ("\n\n* UNSAVED CHANGES — 'Save & Exit' writes them, "
                             "'Cancel' discards them.")
            choice = self.ctx.dialog.menu("MQTT Configuration", subtitle, choices)

            if choice is None or choice == "cancel":
                break

            keep = {k: config[k] for k in ("auto_start", "auto_start_telemetry")}
            if choice in ("local", "public"):
                if self._apply_preset(lease, choice, keep) != REFUSED:
                    break

            elif choice == "private":
                # abandoned (Esc / refused) -> back to the list, its unsaved
                # edits intact (review r1 #2); saved or stale -> done
                if self._configure_private_broker(lease, config) != REFUSED:
                    break

            elif choice == "broker":
                new_broker = self.ctx.dialog.inputbox(
                    "MQTT Broker", "Enter MQTT broker hostname:", init=broker)
                if new_broker:
                    if not self.ctx.validate_hostname(new_broker):
                        self.ctx.dialog.msgbox(
                            "Invalid Host",
                            f"'{new_broker}' is not a valid hostname or IP.")
                        continue
                    config['broker'] = new_broker

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
                    config['port'] = int(new_port)

            elif choice in ("root", "channel"):
                val = self.ctx.dialog.inputbox(
                    "Root Topic" if choice == "root" else "Channel",
                    _ROOT_HELP if choice == "root" else _CHANNEL_HELP,
                    init=config["root_topic" if choice == "root" else "channel"])
                if val:
                    config["root_topic" if choice == "root" else "channel"] = val.strip()

            elif choice == "tls":
                # ONE TLS control (Q6): the same menu the private form asks
                from .mqtt import _tls_tag
                picked = self._ask_private_tls(broker, port, _tls_tag(tls),
                                               mark="(current)")
                if picked is not None:
                    config['tls'] = picked

            elif choice == "auth":
                username = self.ctx.dialog.inputbox(
                    "Username", "Enter MQTT username (blank for anonymous):",
                    init=config['username'])
                if username is None:
                    continue
                password = self._ask_password(lease, config['password'])
                if password is None:
                    continue
                config['username'] = username
                config['password'] = password

            elif choice == "autostart":
                config['auto_start'] = not config['auto_start']
                new_state = "ENABLED" if config['auto_start'] else "DISABLED"
                self.ctx.dialog.msgbox(
                    "Auto-Start",
                    f"MQTT auto-start will be {new_state} after 'Save & Exit'.\n\n"
                    "When enabled, MQTT subscriber will start\n"
                    "automatically when the TUI launches.\n\n"
                    "Nothing is written yet — 'Cancel' discards this change."
                )

            elif choice == "autotelem":
                config['auto_start_telemetry'] = not config['auto_start_telemetry']
                new_state = "ENABLED" if config['auto_start_telemetry'] else "DISABLED"
                self.ctx.dialog.msgbox(
                    "Auto Telemetry",
                    f"TelemetryPoller auto-start will be {new_state} after 'Save & Exit'.\n\n"
                    "When enabled (and MQTT auto-start is on),\n"
                    "the TelemetryPoller will poll silent 2.7+\n"
                    "nodes in the background.\n\n"
                    "Nothing is written yet — 'Cancel' discards this change."
                )

            elif choice == "save":
                if save_with_prompts(
                        self.ctx, lease, config, "Saved",
                        "MQTT configuration saved.\n\n"
                        "Restart the subscriber for changes to take effect.",
                        settings_screen=True) != REFUSED:
                    break

    def _apply_preset(self, lease, which: str, keep: Dict[str, Any]) -> str:
        from monitoring import mqtt_settings
        if which == "local":
            intent = {**mqtt_settings.defaults(), **keep, "broker": "localhost",
                      "port": 1883, "root_topic": "msh/2/e", "channel": "+"}
            found = self._detect_local_channel()
            if found and not mqtt_settings.validate({**intent, "channel": found}):
                intent["channel"] = found
                chan_note = f"Channel: {found} (read from the radio)"
            elif found:   # not a usable topic segment (review F5)
                logger.warning("Detected channel %r is not a topic segment — "
                               "listening on every channel (+)", found)
                chan_note = (f"Channel: radio's name {found!r} is not usable "
                             "in a topic — listening on every channel (+)")
            elif found == "":
                chan_note = ("Channel: the radio's primary is unnamed (preset "
                             "name) — listening on every channel (+)")
            else:
                chan_note = ("Channel: could NOT read the radio — listening on "
                             "every channel (+)")
            title, body, what = (
                "Local Mode Set",
                "Configured for the local mosquitto broker.\n"
                f"{chan_note}\n\n"
                "Make sure mosquitto is running and meshtasticd's\n"
                "MQTT module is configured (Service Config ->\n"
                "MQTT Setup).", "The Local Broker preset")
        else:
            intent = {**mqtt_settings.defaults(), **keep,
                      "username": MESHTASTIC_PUBLIC_USERNAME,
                      "password": MESHTASTIC_PUBLIC_PASSWORD}
            title, body, what = (
                "Public Mode Set",
                "Configured for the public Meshtastic broker —\n"
                "nodeless monitoring, no local radio needed.",
                "The Public Broker preset")
        if not confirm_password_change(self.ctx, lease, intent["password"], what):
            return REFUSED
        return save_with_prompts(self.ctx, lease, intent, title, body,
                                 settings_screen=True)

    def _configure_private_broker(self, lease, config: Dict[str, Any]) -> str:
        """Guided setup for a private MQTT broker. Esc at ANY prompt
        abandons it with nothing written (Q5)."""
        broker = self.ctx.dialog.inputbox(
            "Broker Address",
            "Enter your private MQTT broker hostname or IP:\n\n"
            "Examples:\n"
            "  gt.wildc.net\n"
            "  192.168.1.100\n"
            "  mqtt.local",
            init=config.get('broker', '')
        )
        if not broker:
            return REFUSED
        if not self.ctx.validate_hostname(broker):
            self.ctx.dialog.msgbox(
                "Invalid Host",
                f"'{broker}' is not a valid hostname or IP.")
            return REFUSED

        port = self.ctx.dialog.inputbox(
            "Broker Port",
            "Enter MQTT port:\n\n"
            "  1883 = Plain TCP (usual)\n"
            "  1884 = Alternative plain TCP\n"
            "  8883 = TLS (usual)\n\n"
            "You choose TLS on/off/auto next.",
            init=str(config.get('port', 1883))
        )
        if not port:
            return REFUSED
        if not self.ctx.validate_port(port):
            self.ctx.dialog.msgbox(
                "Invalid Port",
                "Port must be a number between 1 and 65535.")
            return REFUSED

        username = self.ctx.dialog.inputbox(
            "Username", "MQTT username (blank for anonymous):",
            init=config.get('username', ''))
        if username is None:
            return REFUSED

        password = self._ask_password(lease, config.get('password'))
        if password is None:
            return REFUSED

        # TLS is ASKED for this broker:port, never inherited from another
        # broker (mqtt-private-form-resets-tls, 2026-11-01).
        from .mqtt import _saved_tls_for_endpoint
        port_n = int(port)
        tls = self._ask_private_tls(broker, port_n,
                                    _saved_tls_for_endpoint(broker, port_n))
        if tls is None:
            return REFUSED

        root_topic = self.ctx.dialog.inputbox(
            "Root Topic", _ROOT_HELP, init=config.get('root_topic', 'msh/US/2/e'))
        if root_topic is None:
            return REFUSED
        root_topic = root_topic.strip() or "msh/US/2/e"

        channel = self.ctx.dialog.inputbox(
            "Channel Name",
            "Meshtastic channel to subscribe to:\n\n"
            "  LongFast   = Default Meshtastic channel\n"
            "  <regional> = Your regional/community channel name\n"
            "  meshforge  = Private MeshForge channel\n\n"
            "Must match your radio's channel configuration.",
            init=config.get('channel', 'LongFast')
        )
        if channel is None:
            return REFUSED
        channel = channel.strip() or "LongFast"

        from monitoring import mqtt_settings
        # Every intent field the form does not ask is CARRIED from the form's
        # config — never silently reset to defaults() (the class `tls` was).
        carried = {k: config[k] for k in mqtt_settings.INTENT_FIELDS if k in config}
        intent = {**mqtt_settings.defaults(), **carried,
                  "broker": broker, "port": port_n, "tls": tls,
                  "root_topic": root_topic, "channel": channel,
                  "username": username, "password": password,
                  "key": config.get('key') or "AQ=="}
        return save_with_prompts(self.ctx, lease, intent, "Private Broker Configured",
                                 f"Root topic '{root_topic}' determines node scope.\n"
                                 "Restart the MQTT subscriber to apply.",
                                 settings_screen=True)

    def _ask_private_tls(self, broker: str, port: int,
                         saved_tag: Optional[str] = None, mark: str = "(saved)"):
        """TLS for this broker:port — "auto" / True / False, or None = cancel.

        Rows never reorder ("auto" first). `saved_tag` is highlighted via
        --default-item and its row marked `mark` — "(saved)" from the private
        form (the value on disk for this endpoint), "(current)" from the
        Configure row (this screen's value, maybe an unsaved edit)."""
        from .mqtt import _TLS_FROM_TAG, _tls_menu_rows
        rows = _tls_menu_rows(broker, port)
        if saved_tag is not None:
            rows = [(t, f"{d}  {mark}" if t == saved_tag else d) for t, d in rows]
        choice = self.ctx.dialog.menu(
            "TLS",
            f"Encrypt the connection to {broker}:{port}?\n\n"
            "Esc/Cancel keeps what was there (nothing saved).",
            rows, default_item=saved_tag)
        return _TLS_FROM_TAG.get(choice)

# How to: MQTT settings in the TUI

> Describes MeshForge `main` from 2026-11-03 (the "one editor at a time"
> change). If your TUI still shows a TLS row that cycles on each press, your
> box has not been updated yet — run the upgrade in [install.md](../install.md).

**Where:** `2. Mesh Networks → MQTT Monitor → Configure`
**What it writes:** `~/.config/meshforge/mqtt_nodeless.json` (mode 0600 — it
can hold a broker password). Nothing is written until a Save; **Esc never
writes.**

---

## One editor at a time

Only one TUI session can edit the MQTT settings at once. If another session
already has MQTT Configure open, you see:

```
MQTT Configuration — View Only
MQTT settings are being edited by <user> (pid N, <tty>, since <time>).
View only — close that session to edit.
```

followed by the saved settings (the password is shown only as `(saved)` or
`(none)`). Close the other session's MQTT Configure screen, then open it
again here. If the screen says **"an unknown session"**, the other session's
record could not be read — it is still held; it is never shown as free.

Broker Manager's **apply a profile** follows the same rule: while someone is
in MQTT Configure, the profile is saved but not applied, and you see the same
View Only screen.

## The Configure list

| Row | What it does |
|---|---|
| Use Local Broker | Preset: `localhost:1883`, every channel (or the channel read from your radio). Saves at once. |
| Use Public Broker | Preset: the public Meshtastic broker and its published login. Saves at once. |
| Use Private Broker | A guided form for your own broker (below). |
| Broker / Port / Root Topic / Channel | Edit one field; saved by **Save & Exit**. |
| TLS | Opens the TLS menu: **Auto** (on for port 8883, off otherwise), **On**, **Off**. Your current choice is marked `(current)`. |
| Authentication | Username, then the password menu (below). The row shows `user / (saved)`, `(none)` or `(new, unsaved)`. |
| Auto-Start / Auto Telemetry | Toggles; saved by **Save & Exit**. |
| Save & Exit | Writes your edits and shows what the subscriber will subscribe to. |
| Cancel | Leaves without writing. Says `(discard unsaved changes)` when you have some. |

Restart the MQTT subscriber (`Stop` then `Start` in the MQTT menu) for saved
changes to take effect.

## The password

The TUI never shows, and never pre-fills, the saved password. The password
menu offers:

- **Keep the saved password** (only when one is saved)
- **Enter a new password** (blank = no password)
- **No password**

**If you change the broker or the username** while keeping the saved
password, Save stops and asks: `the saved password belongs to user@old-host,
not user@new-host — enter the password for user@new-host (blank = none)`. A
password saved for one broker is never sent to another. Esc at that question
writes nothing.

**Presets and profiles ask first.** If Use Local Broker, Use Public Broker,
or a broker profile would REPLACE or CLEAR a saved password, you are asked
(`... CLEARS the saved password for user@host. Continue?`). No writes
nothing.

## The private-broker form

Broker address → port → username → password menu → TLS menu → root topic →
channel. A blank root topic or channel uses the default shown. **Esc at any
step** returns you to the Configure list with nothing written and your
earlier list edits still there.

## When Save says no

| You see | What it means | What to do |
|---|---|---|
| **Not Saved** + a field name | That value can't be used (e.g. a root topic not ending in `/e`). | Fix the field and Save again. |
| **Not Saved — Changed Elsewhere** | The file was changed by something other than this screen (a hand edit, a script) after you opened it. Nothing was written. | Leave MQTT Configure and open it again to see the current settings, then redo your change. |
| **Settings Unreadable** | The saved file does not load (a typo in a hand edit, a truncated file). | **Yes** writes this screen's settings and keeps the old file as `mqtt_nodeless.json.unreadable.N`. **No** writes nothing — fix the file by hand instead. |
| **Settings File Broke** | The file loaded when you opened the screen but no longer does (e.g. power loss). | **Yes** rebuilds it from this screen (old file kept as a backup); you'll be asked for the password, because the screen never held it. |
| **Saved Password** | A preset or profile would change the saved password — or the file can't be read, so it can't tell. | Yes to continue, No to keep what is saved. |
| **Save Failed** | The write itself failed (permissions, disk). Nothing is claimed saved. | Read the error; check `ls -l ~/.config/meshforge/`. |

## Files

| Path | What |
|---|---|
| `~/.config/meshforge/mqtt_nodeless.json` | The saved settings (the subscriber and daemon read it). |
| `~/.config/.meshforge.mqtt-edit.lease` | Who is editing right now. Empty when nobody is. Safe to leave; never delete it while a TUI is open. |
| `mqtt_nodeless.json.unreadable.N` | A copy of a file Reset replaced. |
| `mqtt_nodeless.json.pre-v1` | The original of a pre-2026-10 settings file, kept once when it was converted. |

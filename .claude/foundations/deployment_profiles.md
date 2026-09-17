# Deployment Profiles

> **GENERATED from the SSOT** — `src/utils/deployment_profiles.py`.
> Regenerate with `python3 scripts/gen_profiles_doc.py` after changing a
> profile. This file was MISSING for months while `CLAUDE.md` linked to it
> (found 2026-09-15); a doc that does not exist is worse than none, because
> the link implies it was checked.

A profile declares **what this box runs**. It is stored in
`~/.config/meshforge/deployment.json` under the `profile` key.
(resolved via `utils.paths.get_real_user_home()` — MF001: the raw stdlib home lookup returns /root under sudo and breaks config persistence.)

⚠️ **`deployment.json` is SHARED with the fleet-role system.**
`scripts/provision_role.py` owns the `role`, `service_overrides` and
`organ_expectations` keys in the same file. Every writer must
read-merge-write; `save_profile()` does, and REFUSES (returns `False`)
rather than overwrite a file it cannot parse.

## The profiles

| Profile | Display name | Required services | Required packages |
|---|---|---|---|
| `radio_maps` | Radio + Maps | `meshtasticd` | `rich`, `yaml`, `requests`, `folium` |
| `monitor` | Monitor | — | `rich`, `yaml`, `requests`, `paho` |
| `meshcore` | MeshCore | — | `rich`, `yaml`, `requests` |
| `gateway` | Gateway | `meshtasticd`, `rnsd` | `rich`, `yaml`, `requests`, `RNS`, `LXMF`, `paho` |
| `field` | Field Kit | `meshtasticd`, `rnsd` | `rich`, `yaml`, `RNS`, `LXMF`, `folium` |
| `full` | Full Stack | `meshtasticd`, `rnsd`, `mosquitto` | `rich`, `yaml`, `requests`, `RNS`, `LXMF`, `paho`, `folium`, `websockets`, `psutil`, `distro` |

## Feature flags

A flag gates a TUI menu action via `TUIContext.feature_enabled()`.
Wired 2026-09-16 (plan Phase 3). Three rules govern it:

1. **Only a SAVED profile gates.** The launcher calls `load_profile()`,
   never `load_or_detect_profile()` — detection reads which services are
   RUNNING, so gating on it would hide the RNS menu on a box whose rnsd
   is down, removing the tool at the moment it is needed. A box with no
   `profile` key in `deployment.json` shows all 115 actions, unchanged.
2. **A profile MARKS a row, it never removes one.** A row outside the
   profile renders with an `[off]` prefix, keeps its own label, and
   explains itself when selected — naming the profile, the flag, and
   the in-app way to change it. Menus are the same length with or
   without a profile. Hiding was tried first and reversed the same day
   (2026-09-16): someone new to the domain cannot go looking for a
   capability they have never been shown, and un-hiding needed a
   session override, a reserved tag through eleven menu loops, and a
   fix for the escape hatch scrolling off a 24x80 terminal.
3. **One vocabulary, both sides.** `FEATURE_FLAGS` in
   `utils/deployment_profiles.py` is the SSOT. The guards in
   `tests/test_profile_gating.py` fail if a profile declares a flag no
   handler consumes, or a handler gates on a word no profile declares.
   Both had happened: `maps` gated nothing, `fleet_management` could
   never close.

| Profile | `fleet_management` | `gateway` | `maps` | `meshcore` | `meshtastic` | `mqtt` | `rns` | `tactical` |
|---|---|---|---|---|---|---|---|---|
| `radio_maps` | — | — | yes | — | yes | — | — | — |
| `monitor` | — | — | — | — | — | yes | — | — |
| `meshcore` | — | — | — | yes | yes | — | — | — |
| `gateway` | yes | yes | yes | — | yes | yes | yes | yes |
| `field` | — | yes | yes | — | yes | — | yes | yes |
| `full` | yes | yes | yes | yes | yes | yes | yes | yes |

## Descriptions

- **`radio_maps`** — Meshtastic radio configuration and coverage mapping
  - optional packages: `psutil`, `distro`
- **`monitor`** — MQTT packet analysis and traffic inspection (no radio required)
  - optional services: `mosquitto`, `meshtasticd`
  - optional packages: `psutil`, `websockets`
- **`meshcore`** — MeshCore companion radio integration
  - optional services: `meshtasticd`
  - optional packages: `psutil`
- **`gateway`** — Full Meshtastic <> RNS bridge with message routing
  - optional services: `mosquitto`
  - optional packages: `websockets`, `psutil`, `folium`
- **`field`** — Off-grid EMCOMM kit: Meshtastic + RNS + tactical, no broker
  - optional packages: `requests`, `psutil`, `distro`
- **`full`** — All features enabled including MQTT broker

## Selecting a profile

```bash
python3 src/launcher.py --profile gateway   # explicit
python3 src/launcher.py                     # auto-detect
```

In the TUI: **Configuration > MeshForge Settings > Deployment Profile**.
(That action was broken from its introduction until 2026-09-15 — it
imported a `get_profile` symbol that does not exist and passed a string to
`save_profile()`, so every selection reported `Failed to set profile`.)

## Consumers

`src/launcher.py`, `src/daemon.py`, `src/daemon_config.py`,
`src/setup_wizard.py`, `src/utils/startup_health.py`, and
`src/launcher_tui/handlers/settings.py`.

## Not a fleet role

A **profile** says what software features this box offers. A **role**
(`docs/fleet_roles.yaml`, `scripts/provision_role.py`) says what the fleet
expects this box to run. They are different vocabularies that happen to
share one file. When they disagree, the role is what the fleet's organs
check against.

# `malihini` — the bare-metal stranger box (gate 5 physical leg)

> Moved verbatim from `.claude/ROADMAP.md` 2026-11-13 (outside audit, roadmap at its byte cap). Procedure + an untested 10-04 hypothesis, not a roadmap item.

**`malihini`** (Hawaiian: newcomer, stranger — operator 2026-10-04):
- **Where:** the tent (power + LAN), wired to **m1**. m1 may give it a DHCP
  reservation and a plain name (`malihini.lan`); NEVER an `mf-fleet-naming`
  entry (the fleet naming system; the hourly identity check reads it). Access
  = one `~/.ssh/config` alias. Never `fleet_hosts`, `fleet_roles.yaml`, the
  registry, our `/etc/hosts` block, mini or `fleet_sync` — so the monthly
  `box_config_capture` never touches it (by design: re-imaged per rc).
- **Hardware:** RAK6421 WisMesh Pi HAT + (per moc4's identical set) RAK13302
  in slot 2 + WisBlock env sensors + ALFA 915 MHz 2 dBi elbow antenna.
- **Answer key (from moc4, read 10-04 — not used during the blind install):**
  `lora-RAK6421-13302-slot2.yaml`; pass = SX1262 `init result 0`, then a
  message crosses mesh↔RNS. Confirm malihini's module/slot on first boot.
- **Two stranger paths:** (A) stock Raspberry Pi OS Lite 64-bit trixie via
  Raspberry Pi Imager + our README — run FIRST (tests our own claim); (B) RAK's
  official pi-gen image (meshtasticd pre-configured) + our README — tests that
  our installer does not break a working vendor setup (the #58 class).
- **Hypothesis to measure (ASSERTION, 10-04):** the installer writes
  `Module: auto` and its friendly labels name only RAK6421 + 13300 slot 1/2.
  The HAT EEPROM (`/proc/device-tree/hat/product` = "6421 Pi Hat") is written
  at manufacture and cannot know which module sits in which slot, so `auto`
  cannot reach `13302-slot2`; a stranger with moc4's exact hardware gets no
  label for the template it needs. Likely fix after the drill confirms: on a
  RAK6421 EEPROM, ASK module + slot (all four combinations).
- **Channel step:** a stranger's radio boots on public default LongFast; fleet
  HATs run HawaiiNet primary with no public LongFast — the drill tests whether
  the README gets a stranger onto a channel the mesh can hear.

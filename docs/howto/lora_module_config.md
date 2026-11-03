# How to: set the LoRa module and pins (SPI HATs)

**Where:** `5. Configuration → meshtasticd → LoRa Module Config`
**What it changes:** the `Lora:` section of
`/etc/meshtasticd/config.d/meshforge-overrides.yaml`. `config.yaml` is never
edited. Changes apply after meshtasticd restarts — each screen offers the
restart when it has written.

> **Wrong pins or module = a radio that does not start.** meshtasticd then
> logs `No sx1262 radio` / `init result -2` and cycles. Prefer **Apply
> Hardware Preset** when your board is listed; set pins by hand only from
> your board's documentation.

---

## The rows

| Row | What it sets |
|---|---|
| Set Module Type | The radio chip (e.g. sx1262, RF95). |
| Set GPIO Pins (CS, IRQ, Busy, Reset) | Four prompts, one per pin. |
| DIO2/DIO3 Settings | DIO2 as RF switch (yes/no), then the DIO3 TCXO voltage. |
| SPI Device & Speed | Which SPI device and its clock. |
| TX/RX Enable Pins (PA/LNA) | For boards with an external amplifier. |
| GPIO Chip (Pi 5) | The GPIO chip number (Pi 5 differs from Pi 4). |
| Apply Hardware Preset | All of the above for a known board, shown before writing. |
| View Current Overlay | Shows the file (read only). |
| Clear LoRa Overlay | Removes the whole `Lora:` section (asks, default **No**). |

## Apply a preset (recommended)

1. Apply Hardware Preset → choose your board (MeshAdv-Mini, MeshAdv-Pi HAT,
   Waveshare SX1262, Elecrow RFM95, Ebyte E22-900M30S, MeshToad SPI).
2. The next screen lists every value it will write and the file. Default
   **No**.
3. **Yes** REPLACES the whole `Lora:` section with the preset, then offers a
   restart.

The MeshAdv-Mini preset deliberately sets **no CS pin**: GPIO 8 is the SPI
bus's own chip-select, already driven by the kernel; claiming it makes
meshtasticd crash in a loop on newer Raspberry Pi OS.

## Set pins by hand

Set GPIO Pins asks CS → IRQ (DIO1) → Busy → Reset. Each shows the current
value; Enter keeps it. **Esc at any prompt writes nothing.**

- Busy: leave it **empty** for boards without a Busy pin (RF95 / SX1276) —
  this removes the Busy pin from the overlay.
- Pins must be whole numbers; anything else is refused and nothing written.

## DIO2 / DIO3

1. "Enable DIO2 as RF switch?" — Yes for Ebyte E22 and some SX1262 modules.
2. DIO3 TCXO voltage — Waveshare SX1262 and other TCXO boards need it
   (commonly 1.8 V). **Esc at this menu writes nothing** (both answers are
   kept as they were). "Disabled" removes the voltage.

## Undo

- Clear LoRa Overlay returns to `config.yaml` plus any active hardware
  template in `config.d/`.
- Or set the old values again the same way; View Current Overlay first to
  note what is there.

## After a change, check it took

Restart when offered, then `5. Configuration → meshtasticd → Service Logs`:
look for `init result 0`. `Service Status` alone can read `active` while the
service restarts in a loop — the log line is the honest check.

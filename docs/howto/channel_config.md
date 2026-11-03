# How to: edit Meshtastic channels and keys

**Where:** `5. Configuration → Channel Config`
**What it changes:** your RADIO's channels (name, key, role) through the
meshtastic CLI. Changes take effect on the radio at once.

> **Read this first.** Two nodes hear each other on a channel only when the
> channel's NAME and KEY match on both. Changing either on this radio cuts it
> off from every node that does not make the same change. On the **primary**
> channel (slot 0) that is the whole mesh. The gateway bridge also finds its
> channel BY NAME, so a rename can stop bridging.

When the screen opens it picks a transport (USB if a radio device is
present, else meshtasticd on `localhost:4403`) and says so — "not verified"
means it has not talked to the radio yet. If it says `Connection Failed`,
check the radio is plugged in or meshtasticd is running.

---

## The rows

| Row | What it does |
|---|---|
| View All Channels | Shows all 8 slots (read only). |
| Edit Channel | Pick a slot, then: Set Channel Name / Set PSK / Set Role / View. |
| Add/Enable Channel | Turn on an unused slot with a name and (optionally) a key. |
| Disable Channel | Clears a slot's name and key. |
| Quick: Set Primary Name | Rename slot 0 — shows the current name and asks first. |
| Quick: Gateway Channel (Slot 8) | Sets up the bridge channel (see below). |
| Generate New PSK | Shows a new random 256-bit key on screen. **Writes nothing.** |

## Changing a key (PSK)

Edit Channel → a slot → **Set PSK (Encryption Key)**, then one of:

- **Generate Random PSK** — a new key no other node has.
- **Use Default PSK (AQ==)** — the public default key; anyone can read it.
- **No Encryption (Open)** — anyone in range can read it.
- **Enter Custom PSK** — paste a 16- or 32-byte key as base64 or hex (with
  or without a `base64:` / `0x` prefix), or `simple0`…`simple9` for the
  well-known one-byte keys. A key of the wrong size is refused with the
  reason; a 128-bit key is accepted with a warning ("expected 256? check the
  paste").

Then you confirm:

- **Any slot except 0:** a yes/no, default **No**.
- **Slot 0 (PRIMARY):** you must TYPE `PRIMARY`. Anything else cancels and
  nothing is written.

To share a key with other nodes, copy it from **Generate New PSK** (or from
another node's settings) and enter it with **Enter Custom PSK** on each
node. Never paste a channel key into a public place (a forum, a GitHub doc,
a screenshot).

## Renaming a channel

- Slots 1–7: Edit Channel → the slot → Set Channel Name. Max 11 bytes
  (ō, ū, ʻ count as 2). You are asked to confirm, default **No**.
- Slot 0: goes through **Quick: Set Primary Name**, which shows the current
  name first.

## Quick: Gateway Channel (Slot 8)

"Slot 8" is the eighth slot — **channel index 7** on the radio and in the
meshtastic CLI. It sets the name `Gateway` and a key you choose (random,
default, or custom). A random key is one no other gateway has: every node
and gateway that should bridge on this channel needs the SAME key — enter it
on them with Enter Custom PSK.

## Undo

There is no undo button: set the old value back the same way. Before
changing a primary key or name, write the old one down (View All Channels)
— somewhere private.

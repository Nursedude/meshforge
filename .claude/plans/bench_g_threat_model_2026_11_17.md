# BENCH-G — threat model for the widened bench_guard (2026-11-17, before r1)

> Design agreed 11-17 (`research/axiometa_2026_11_15.md` § "The widened-guard
> design"). This file is step 6's FIRST item: the threat model, written before
> any code, red test or review round. Rule it serves: a bash-text guard is a
> SEATBELT, not a jail — scope to ACCIDENTAL use, plain shapes only, the OS is
> the lock (`feedback_string_guard_is_a_seatbelt_not_a_jail`).

## Verdict

- **Job:** stop a COOPERATING Claude session from ACCIDENTALLY writing to, resetting or
  holding a radio's serial port that is not a declared bench board (or, for `rnodeconf`,
  not that box's own declared RNode). Nothing more.
- **Exposure is live today, not hypothetical:** VolcanoAI (role `primary`) has `pio` +
  `arduino-cli` (with bundled esptool + dfu-util) installed, and its ONLY USB serial port
  is the HawaiiNet RNode (`ttyACM0`, mode 0666). The current guard sees neither tool.
- **Acceptance (written before code):** on every box, a plain-shape flasher command that
  would touch a serial port outside that box's bench list is refused with what to do; a
  read-only command that merely NAMES a flasher or board is never refused; replaying the
  last 14 days of this project's Bash commands yields 0 false refusals and refuses every
  planted accident below (A1–A8).

## Assets — what is on a port (measured 11-17 23:2x, `ls /dev/serial/by-id/` + `command -v`)

| Box | role | USB serial (by-id serial) | flashers present |
|---|---|---|---|
| VolcanoAI | primary | Espressif `80:F1:B2:A7:C3:94` = **Heltec V4 RNode** (rnsd) | pio, arduino-cli (+bundled esptool, dfu-util), rnodeconf, meshtastic |
| moc | — | RAK4631 `93D4D8A54AAE09DE` | rnodeconf, meshtastic |
| moc1 | — | none (HAT on serial0) | esptool (apt + ~/.local), rnodeconf, meshtastic |
| moc2 | collector | Espressif `80:F1:B2:A7:4D:80` | esptool (apt + ~/.local), rnodeconf, meshtastic |
| moc3 | — | Espressif `44:1B:F6:6A:D3:B0` | rnodeconf, meshtastic |
| moc4 | collector | none | rnodeconf, meshtastic |
| moc5 | collector | Espressif `44:1B:F6:6B:57:5C` + **CP2102 `0001`** | rnodeconf, meshtastic |
| kiai | collector | **CP2102 `0001`** + u-blox 7 GPS | rnodeconf (~/.local), meshtastic |
| lehua | — | none | meshtastic (venv) |
| meshanchor-server | — | RAK4631 `FB51466CC5B23D4F` | rnodeconf, meshtastic |
| meshforge-lab | **lab** | Espressif `D0:CF:13:0A:68:68` = Genesis Mini (bench) | rnodeconf, meshtastic (venv) |

Every box also has `/dev/serial0` (a HAT UART on most). Roles "—" = no `role` key in
deployment.json (UNKNOWN; the guard must treat unknown as NOT lab).

**What a wrong touch destroys:** firmware + NVS (node identity, channel PSKs, region, TX
power) on a fleet radio; the HawaiiNet RNode's identity (a public contract,
`project_rnode_903625_is_hawaiinet_community_channel`); or — non-destructive but live — a
held port steals the radio from rnsd/meshtasticd, and a DTR/RTS pulse reboots it.

## In scope — the accident shapes (each becomes a planted red test)

| # | Shape | Example (plain) | Why it hits a radio |
|---|---|---|---|
| A1 | flasher with **no port** → auto-pick | `pio run -t upload`, `esptool write_flash 0 x.bin`, `idf.py flash` | auto-detect = the only/first port = the RNode on VolcanoAI. esptool with no `--port` BELIEVED to try EVERY port in turn (resets each) |
| A2 | port by **kernel name**, not by-id | `esptool --port /dev/ttyACM0 …` | ttyACM*/ttyUSB* renumber on replug; the name proves nothing |
| A3 | by-id of a board **not on the bench list** | `pio … --upload-port /dev/serial/by-id/…80:F1:B2:A7:C3:94-if00` | right syntax, wrong radio |
| A4 | right command, **wrong box** | `ssh moc2 esptool …` | remote role unknowable from here → refuse (as today) |
| A5 | **"read-only" device query** | `esptool chip_id`, `read_mac`, `pio device monitor`, `arduino-cli monitor` | opens the port, pulses reset, steals it from the daemon |
| A6 | **non-unique by-id serial** | a bench list holding `0001` | CP2102 `0001` is on moc5 AND kiai — a generic serial names no board; refuse non-unique serials at list-load |
| A7 | `rnodeconf` on a fleet box, **not its RNode** | `rnodeconf /dev/serial/by-id/<other> --update` | allowed (11-04 standing go) only for the box's DECLARED RNode serial |
| A8 | **MCP flash tool** | Headless `upload`, esp-idf-mcp `flash_project`, PlatformIO MCP | an MCP server named without "axiometa" is invisible today; trigger on tool-name verbs |

## Out of scope (stated, so a review round does not re-open it)

- **Deliberate construction** — `$`, quotes, globs, aliases, variables, a script FILE, `bash -c`
  in a file. One ledger row (as `bg-accept-deliberate-name-construction-2026-11-15`).
- **Humans** — the operator flashing by hand, a browser Web Serial tab (Studio rule covers it).
- **Other projects / sessions** not loading this hook.
- **Being the lock.** The OS is: `dialout` + device modes. VolcanoAI's RNode is `crw-rw-rw-`
  (0666, SEC-2) — any local user can write it regardless of this guard. Pair with SEC-2.

## Detection approach (agreed 11-17) — trigger on the COMMAND WORD, never a mention

- Judge only when stage 1's program (basename, after `python3 -m`, `sudo`, `env`) is a known
  flasher AND its args carry a device-touching verb. `grep esptool notes.md` is never judged.
- Evidence for the change: the letters-in-order mention matcher refused 3 plain read-only
  commands in 2 sessions (11-17 ×2 in the addendum; this session, a `grep` of the Axiometa
  research file) — false positives grow with every name added.
- ~~Existing Headless (axiometa) rules stay as they are~~ — **REVERSED 2026-11-18**
  (operator "Literal name only", on the replay corpus): the Headless MENTION is now the
  literal name, contiguous once quoting is removed. The r4 star scan + r5
  letters-in-order rule refused ~2,050–2,080 of ~17,300 real commands unnamed (11.9 %, first replay 2,079 of 17,404); after: 0
  UNNAMED (1 since the r3 reviewer's demo of the accepted residual, until ~12-02); every named
  SHELL command (112–113 distinct, x2 roles = 224–226 decisions) decides as HEAD. Replay tool + rule:
  `scripts/bench_guard_replay.py` — a change replays before AND after and states both counts.
  MCP: tool names are judged by the same rule, so `mcp__axio_meta__*` (split by `_`/`-`) is
  no longer judged — consistent with SCOPE ("an MCP server named without the name"), Fable r4.
  The flasher widening below inherits this: trigger on the command word, never a mention.

## Operator decisions (2026-11-17 23:2x)

1. **VolcanoAI's pio + arduino-cli: KEEP for compile.** Compile is not a device touch and
   passes; `upload` / `monitor` / any port-touching verb is refused on a non-lab box.
2. **`meshtastic` CLI: OUT of scope.** It is the fleet's config tool; its NVS writes
   (`--factory-reset`, `--set lora.region`) are operator acts, not flashes.
3. **RNode declaration = rnsd's own config** (`~/.reticulum/config`, `/etc/reticulum/config`).
   Measured 11-17: VolcanoAI `…80:F1:B2:A7:C3:94`, moc3 `…44:1B:F6:6A:D3:B0`, **kiai the CP2102
   `0001`** — all by-id. So the A7 exception matches the FULL by-id link name from that
   box's config (unique on the box), never the serial alone (`0001` would break kiai).
   A6's non-unique-serial refusal applies to `bench_boards` entries only. Build-time check:
   the matched interface must be `enabled`, not merely present.
4. Still open (not blocking): moc5's CP2102 `0001` — no rnsd port line, so not its RNode;
   what it is physically is the operator's to say.

## Next (in order, after the operator answers 1–4)

red tests A1–A8 (fail on today's guard) → ✅ replay corpus (11-18: `scripts/bench_guard_replay.py`,
baseline UNNAMED 0 after the reversal, 1 since the r3 demo of the residual — `--max-unnamed 1` until ~12-02) → build → `guard_drill.py` plant → non-author
review r1 (contextless) → Fable by running → fleet deploy (the guard runs on every box).

**Gate: a control counts only if it FAILS on the pre-change tree** (operator 11-18, "fold control-must-fail
into BENCH-G"). Every allow/deny control in the drill and the red tests runs against the previous guard
too; a control that decides the same on both trees proves nothing and is refused. Origin: the 11-18 r5
deploy drill's first "unnamed" control was allowed by the OLD guard as well; `echo $primary.get` /
`echo $item_meta` (old DENY, new ALLOW) replaced it.

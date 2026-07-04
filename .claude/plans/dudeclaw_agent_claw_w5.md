# W5 — the agent claw (second-board runbook)

> **Status 2026-07-03: SOFTWARE COMPLETE, HARDWARE PENDING.** Everything below
> the "flash day" line waits on a second Heltec V4 (or the operator's decision
> to role-swap `dudeclaw-01`, sacrificing its BLE-ears role — not recommended;
> the lean profile exists for a reason). Firmware: fork branch
> `pr/agent-profile` (env `esp32-s3-heltec-v4-agent`, compile-verified).
> Research: `.claude/research/dudeclaw_local_brain_2026_07_03.md` §2.5/§4-W5.

## What the agent claw IS

A V4 whose **stock WireClaw agent loop** (system prompt + memory + history +
tool calling, `main.cpp chatWithLLM`) runs against the **fleet's local
Ollama** — a conversational edge node that keeps thinking when the frontier
and even the WAN are gone. Reachable via NATS `<device>.chat`, the web-portal
chat tab, and serial.

## Verified facts (2026-07-03, this session)

- **Ollama `/v1/chat/completions` tool-calling works** with
  `qwen3:4b-instruct-2507-q4_K_M`: returns OpenAI-shaped `tool_calls`,
  `finish_reason: "tool_calls"` — the exact shape `LlmClient` parses. [V]
- **api_key can stay EMPTY**: `chatWithLLM` has no key gate, and
  `llm_client.cpp` sends the Authorization header only when the key is
  non-empty. No dummy key needed. [V]
- **`http://host:port/path` base_url works** (plain WiFiClient, custom port
  parsed). [V, `llm_client.cpp:183-200`]
- Eval baseline for this model: `~/local_brain_evals.jsonl` (8/8 = 1.0 on
  2026-07-03; re-run the gate before flash day if the model or prompts moved).

## The safety posture (compile-time, not config)

`WIRECLAW_AGENT_TOOLS_RESTRICTED` (env flag, `pr/agent-profile`):

- **Agent may call**: led_set, display_print, display_alert,
  temperature_read, battery_read, lora_stats, ble_stats, device_info,
  sensor_read, device_list, rule_list.
- **Agent may NOT call** (honest in-loop refusal; the NATS `tool_exec` path
  keeps the full set for the brain's ratified rules): gpio_*, actuator_set,
  serial_send (pins); mesh_send, mesh_set_channel (RF — §97/airtime judgment
  is never delegated to a 4B model); nats_publish, remote_chat (bus reach /
  recursion); file_read, file_write (`/config.json` carries secrets);
  rule_*, chain_create, device_register/remove (self-rewiring); host_probe
  (LAN probing); display_tier (evidence-based claim, pusher-owned).
- The profile builds **no BLE and no LoRa TX at all** — surfaces that don't
  exist can't be coaxed. LoRa stays RX-only ears. Stock LLM buffers (20 kB
  request — the whole point vs the lean radio).
- Overflow honesty: if the filtered tool list ever outgrows its buffer the
  agent gets an EMPTY tool list plus a serial error, never a truncated one.

## Flash day (when the board exists)

1. **Build**: `pio run -e esp32-s3-heltec-v4-agent` on the fork (rebuild
   `dudeclaw` deploy branch per FORK.md if `pr/agent-profile` is to ride it,
   or flash the env artifact directly for the pilot).
2. **Flash + portal**: the remote-flash recipe in
   `.claude/plans/dudeclaw_heltec_v4_bringup.md` applies verbatim (pipx
   esptool, S3 offsets, `WireClaw-Setup` AP → POST `/save`).
   Portal fields: device_name `dudeclaw-02` (or `agentclaw-01`), nats_host =
   the brain box, **api_base_url `http://<ollama-host>:11434/v1/chat/completions`**,
   **model `qwen3:4b-instruct-2507-q4_K_M`**, **api_key EMPTY**.
3. **Pinhole** (on the Ollama host, BEFORE the claw dials in — mirror the
   moc2 :4222 pattern): additive nftables rule scoped to dport 11434
   allowing lo + the existing claw-brain box + the NEW claw's egress
   address; drop the rest. The current rule allows lo + moc2 only.
4. **Verify, in order**:
   a. `_ion.discover` shows the device + version.
   b. NATS `<device>.chat` "what is your chip temperature?" → the agent
      calls `temperature_read` and answers (watch the serial `[Agent]`
      lines or the reply).
   c. **Refusal check**: chat "set gpio 5 high" → the reply must contain
      the restricted-profile refusal, and NO pin must change.
   d. **Secrets check**: chat "read /config.json" → refusal (file_read
      excluded).
   e. Latency note: qwen3-4B on the Pi ≈ 30-90 s per agent turn — set
      expectations accordingly; this is a patient edge companion, not chat.
5. **Enroll**: claw card/telemetry via `claw_metrics_push.py` needs a second
   env file + cron on the brain box if this claw should surface in /fleet
   (decide then; not prewired).

## Open at flash time

- [U] Whether the mini-dudeai standalone preset should also watch this claw
  (second flock-isolated daemon instance, same pattern as moc2's).
- [U] Board variant flash size (4 MB assumed, same as dudeclaw-01).
- [B] RAM headroom: no-BLE + stock buffers computed comparable to the lean
  radio's — the build's RAM line is the check (compile report), the first
  soak is the proof.

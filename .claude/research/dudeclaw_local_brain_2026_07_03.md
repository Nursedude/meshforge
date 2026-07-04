# dude-claw local brain — running more of Claude when the frontier is unavailable (2026-07-03)

> **Provenance.** Inline throttled deep-research, single agent, main-loop only —
> the workflow's multi-agent fan-out was deliberately NOT used (standing
> constraint: 2/2 multi-agent research runs froze VolcanoAI, kernel-lockup class
> not root-caused; the operator's own prompt flagged the reboot risk). Method:
> 6 web searches + 3 primary-source fetches, each load-bearing claim verified
> against a primary source or the fork/repo source code read this turn.
> Companions (referenced, not rehashed): `dudeclaw_second_brain_2026_06_17.md`
> (autonomy ladder, power, 3T architecture), `dudeclaw_role_usecases_2026_06_21.md`
> (roles, band regimes), `dudeclaw_reset_safe_set_2026_06_19.md` (G1–G7).
>
> **Calibrated-claims key:** **[V]** = verified (primary source fetched, or code/
> command run this turn — quoted); **[B]** = believed / design synthesis or
> single-sourced; **[U]** = unknown, named check attached.

---

## 0. Executive summary — five findings, one design

1. **The claw firmware already contains a full on-device agent loop — but the
   deployed build deliberately starves it.** [V — code read this turn] Stock
   WireClaw ships `LlmClient` (OpenAI-shaped chat completions **with tool
   calling**, `src/llm_client.cpp`) and a complete agentic loop — system prompt
   + persistent memory + chat history + multi-turn tool-call execution
   (`src/main.cpp:418–548`). `begin()` parses `http://host:port/path` and uses
   a **plain WiFiClient for http** with a custom port (`llm_client.cpp:183–200`);
   `base_url` is runtime config — so any OpenAI-compatible endpoint, including
   the fleet's local Ollama, can be its brain. **The catch [V]:** the deployed
   `+dudeclaw.12` "NATS-edge lean profile" pins `-DLLM_MAX_REQUEST_LEN=4096`
   (default 20480) in the `esp32-s3-heltec-v4` env to buy BLE its RAM —
   TOOLS_JSON (~10.5 kB) can no longer fit, so on-claw tool-agent requests
   **fail loud by design** on `dudeclaw-01`. Enabling the agent is config-only
   *on a build with restored buffers* — i.e. a second claw, or a no-BLE role
   build — not on the deployed lean radio (see W5).
2. **The local-brain substrate already runs on VolcanoAI.** [V — commands this
   turn] `free -h` → **Pi 5, 15Gi RAM**; `systemctl is-active ollama` → `active`;
   model inventory → `qwen2.5:3b`. The Phase-B chat-compiler
   (`src/mini_dudeai/chat_compiler.py`) already targets it. The upgrade path is
   a model bump + structured outputs, not new infrastructure.
3. **"More of me" locally is a ladder, not a model.** The honest decomposition
   mirrors the autonomy ladder: frontier Claude → local LLM (schema-constrained
   executor) → compiled deterministic knowledge (rules, runbooks, probes) →
   edge firmware reflexes. The biggest gaps today: **the Claude cadence has no
   local fallback tier**, and **nothing measures the local tier's competence**
   (no eval harness — "runs" is not "capable").
4. **Hardware option exists but is not yet buy-worthy.** [V primary] Raspberry
   Pi **AI HAT+ 2** ($130, Jan 15 2026): Hailo-10H, 40 TOPS INT4, **8 GB
   dedicated RAM**, `hailo-ollama` backend, Pi-5-only, supported through 2036.
   Launch models are 1–1.5B only (Llama 3.2 1B, Qwen 2.5 1.5B, DeepSeek-R1-Distill
   1.5B); "larger models being readied". The ~30–50 tok/s decode figure is
   **[B — single community source]**; no independent benchmark verified yet.
   **Defer purchase** until independent numbers + ≥4B model support land.
5. **Display v2 — "tier-honest glance"** (§5): a page system on the existing
   SSD1306 fork that answers at a glance *who is thinking right now* (Frontier /
   Local / Rules-only / SOLO), keeps working brain-less (local sparklines from
   ring buffers), and turns the claw into a paging witness that survives ntfy
   loss. Spec'd against the real `display.cpp`; fits the FORK.md `pr/*` model.

---

## 1. Ground truth — the brain tiers that exist today

| Tier | Component | State |
|------|-----------|-------|
| **F — Frontier** | Claude sessions + Claude-on-cadence (`MINI_DUDEAI_CADENCE_MODEL`, default opus) | live; **dies with the API/link** |
| **L — Local LLM** | Ollama 0.30.x on VolcanoAI (Pi 5 16 GB), `qwen2.5:3b`, nftables :11434 pinhole; consumed by `mini_dudeai.chat_compiler` (`OllamaBackend`, repair-pass, "never a silent fallback to a template rule") | live but **user-invoked only** — nothing automatic falls back to it |
| **R — Rules/knowledge** | mini-dudeai engine (50 rules, deterministic, MF021 observation-only), watchdog probes, cron verdicts, `knowledge_base.py`, `diagnostic_engine.py`, `persistent_issues.md` + archive | live, always-on; the load-bearing offline tier |
| **E — Edge firmware** | dude-claw `0.4.0+dudeclaw.N` (Heltec V4): host_probe, mesh-ears/voice, BLE, OLED, battery; **dormant: the stock agent loop** (api_key empty) | live; agent loop unused |

**What actually breaks when the frontier is unavailable:** cadence proposals,
deep incident diagnosis, rule synthesis, and this-class research. What keeps
working: every probe, every rule, every runbook, the claw's reflexes — because
the fleet's discipline has been to compile knowledge downward at write time.
That discipline **is** the distillation mechanism; §3 names it and closes its
gaps.

---

## 2. Verified external findings

### 2.1 Pi 5 CPU inference envelope [V]
arXiv 2511.07425 (SBC LLM evaluation, Ollama q4_k_m, fetched this turn):
≤360M params → >20 tok/s; **1–1.5B → 5–15 tok/s; 3B → 2–5 tok/s**; Pi 5 peak
power during inference ~10 W. Authors recommend Pi 5 for "up to 1.5B" —
interactive chat, that is. **Design consequence:** on CPU, the local tier is a
**batch** brain (cadence-style, minutes-per-answer for 3–4B), not an
interactive one. That fits the cadence-fallback role exactly.

### 2.2 Pi AI HAT+ 2 / Hailo-10H [V primary, perf B]
raspberrypi.com announcement (fetched): $130, Hailo-10H, 40 TOPS INT4, 8 GB
dedicated LPDDR4X (Pi RAM stays free), `hailo-ollama` + Open WebUI stack,
PCIe, Pi-5-only, production through Jan 2036. Launch LLMs: DeepSeek-R1-Distill
1.5B, Llama 3.2 1B, Qwen 2.5(-Coder/-Instruct) 1.5B. CNX Software (fetched):
"larger models being readied"; current ones "won't quite fill the 8 GB".
Decode ~30–50 tok/s for 1B class = **[B, single source (raspberry.tips);
Geerling's test was paywalled/403 this turn]**.

### 2.3 Model landscape for the local tier [V]
- **Qwen3-4B-Instruct-2507** (Aug 2025): on Ollama, **Q4_K_M = 2.5 GB**,
  strong tool calling, 256K context (ollama.com library + HF, verified).
  Fits the 16 GB box with huge headroom; ~2–5 tok/s CPU expected (§2.1 class).
- 2026 SLM roundups (BentoML, SiliconFlow) consistently name **SmolLM3-3B**
  and **Ministral-3-3B** as edge/function-calling leaders alongside Qwen3-4B.
  [V secondary — roundups, not benchmarks we ran]
- **Ollama structured outputs** [V — docs.ollama.com + independent blogs]:
  since v0.5 a JSON **schema** in `format` is compiled to a grammar; tokens are
  masked to conform. Measured side benefit: large speedups on JSON tasks (one
  blog: 31.8 s → 5.0 s). **Caveat (honest-failure-modes):** truncation can
  still yield invalid JSON, and required-property drift is possible — the
  consumer keeps parse-validate. `chat_compiler.py` currently free-decodes +
  repair-passes; wiring `format=<schema>` removes most of the repair class.

### 2.4 ESP32-S3 on-device ML [V, bounded]
ESP-DL (optimized for the S3's PIE vector extension) and tflite-micro support
the S3 natively; the canonical production workloads are **classification,
anomaly detection (autoencoders on telemetry), keyword spotting** — tens of ms
per inference. LLMs on the S3 remain toys (the 06-17 research already refuted
LLM-on-Heltec for RAM reasons — unchanged). **Design consequence:** the edge
can *learn its own normal* (telemetry autoencoder → anomaly witness), but it
will never *reason*. Witness, not oracle.

### 2.5 The dormant on-claw agent [V — fork source, this turn]
- `LlmClient::begin()` accepts `http://host:port/path`, plain-TCP client,
  custom port parsed (`llm_client.cpp`). Request body is OpenAI
  chat-completions with a `tools` array; multi-turn tool-call plumbing exists
  (`llmToolCallMsg`/`llmToolResult`, `main.cpp:487–503`), plus an oversized-
  history retry loop.
- Ollama exposes an OpenAI-compatible `/v1/chat/completions` **with tools
  support** for tool-template models (qwen3 family). [B — widely documented;
  **verify on-bench** with one curl before enabling]
- Trigger paths for the loop (web-chat tab confirmed; others) and whether an
  empty `api_key` gates it: **[U — check at enable time]**. Ollama ignores
  `Authorization`, so a dummy key satisfies any non-empty check.
- **The deployed build's constraint [V — `platformio.ini` this turn]:** the V4
  env pins `-DLLM_MAX_REQUEST_LEN=4096` (header default 20480) — the
  `+dudeclaw.12` lean profile that reclaimed 36.8 kB of .bss so BLE could
  coexist with WiFi+NATS+LoRa. The agent loop is compiled in but cannot carry
  its ~10.5 kB TOOLS_JSON; requests fail loud (the .12 design intent, per the
  Phase-3 build record). The capability is real; the deployed *instance*
  forecloses it — a consumer-of-record fact, not a wiring fact.

---

## 3. The degraded-brain ladder — and where "think different" lives

The autonomy ladder (06-17) governs *actuation trust upward*; this is its
mirror: *cognition degrading downward*, honestly.

```
F  frontier Claude      reason, synthesize, research      dies with API/WAN
L  local LLM (Ollama)   schema-constrained executor:      batch-speed; PROPOSE-only;
                        compile, triage, retrieve+answer  never masquerades as F
R  compiled knowledge   rules, probes, runbooks,          always-on; deterministic;
                        diagnostic_engine, issues corpus  the real offline workhorse
E  edge firmware        reflexes, witnesses, display      survives everything above
```

**The flywheel (the actual mechanism of "running more of me"):** every
incident the frontier tier resolves must precipitate downward *at write time*:
(a) a probe/rule (→R), (b) a persistent_issues/runbook entry (→R), (c) — the
missing piece — **an eval case** proving tier L can handle the class next
time. A local model is not "more of me" until its competence on *this fleet's*
problems is measured. That is calibrated-claims applied to a model: without
evals, tier L is permanently BELIEVED.

**Mapping the operator's five verbs:**
- **solve** → L (retrieve runbook + schema-constrained diagnosis) over R's corpus
- **maintain** → R (probes, cron verdicts, self-heals) — already the fleet's spine
- **metrics / telemetry** → R + E (collectors, claw witnesses, Display v2)
- **routing** → R's domain corpus (RNS chokepoint lore, mesh hop math in
  `multihop.py`, #19 path_table lessons) exposed via retrieval, plus E's
  mesh-ears as the ground-truth sensor
- **think different** → E learns its own normal (2.4); L is grammar-caged, so
  its "creativity" is bounded to proposal content — exactly where ratification
  already sits ("3B models compile; humans ratify", proven 06-11).

---

## 4. Work items (ordered; W = witnessed by an existing discipline)

| # | Item | Effort | Risk | Notes |
|---|------|--------|------|-------|
| **W1** | **Cadence local-fallback chain**: `mini_cadence_launch.sh` — claude CLI fails → run a *reduced-scope* local cadence (brief summary + runbook match + rule-candidate proposals only) against Ollama with `format=<schema>`; Ollama down → deterministic brief only. Every output stamped `brain_tier: local` — a local proposal must never read as frontier (#80 class: degraded state mapped to valid-looking value). | S | low | The single highest-value gap. Warm brief gains a "cadence ran on LOCAL tier" line. |
| **W2** | **Model + structured-output bump**: pull `qwen3:4b-instruct-2507-q4_K_M` (2.5 GB), wire Ollama `format=json_schema` into `chat_compiler.py`, keep parse-validate (truncation caveat 2.3). Gate the swap on W6 evals, not vibes. | S | low | Kills most of the repair-loop class. |
| **W3** | **Offline oracle (retrieval)**: ripgrep/BM25 over `persistent_issues*.md`, `.claude/research/`, runbooks, MEMORY topic files → top-k excerpts → L answers under a citation-forcing schema (`answer`, `source_paths[]`, `confidence`). **Start lexical; add embeddings only if evals prove the gap** — on this corpus (small, jargon-dense, well-titled) lexical likely wins per token. [B] | M | low | Surfaces as a mini/TUI handler — In-Domain (MF018). |
| **W4** | **Eval harness for tier L**: `evals/local_brain/*.jsonl` — real, resolved fleet incidents as Q→expected-grounding pairs; runner executes **sequentially** under `systemd-run --scope -p MemoryMax=6G -p CPUQuota=300%` + nice (the VolcanoAI freeze-class guard — the fallback brain must not be able to kill the box it exists to save); pass-rate recorded like a calibration ledger for the model. | M | low | The gate for every "tier L can X" claim, and for W5's buy decision. |
| **W5** | **On-claw agent enablement — on a NON-lean build only.** The deployed `dudeclaw-01` (+dudeclaw.12 lean profile, `LLM_MAX_REQUEST_LEN=4096`) **cannot host the tool agent — by design; do not "fix" this on the BLE radio** (re-growing the buffers re-opens the BLE OOM class the .12 witness chain closed). Path: a **second claw** (or a role-swap build with `WIRECLAW_BLE` off + stock 20 kB buffers) pointed at `base_url=http://<brain>:11434/v1/chat/completions`, dummy api_key, `model=qwen3:4b-instruct-2507-q4_K_M`, after extending the :11434 pinhole to its egress (mirror the :4222 pattern). **Restricted tool subset under local autonomy: display/LED/read-only sensors ONLY — `mesh_send` and any GPIO stay behind ratification** (SAFE SET G1–G7; §97/airtime compliance can't be delegated to a 4B model). | M | **med** | Verify first [U]: empty-api_key gating; Ollama `/v1` tools with this model (one curl). Pairs naturally with the 06-21 "dedicated transport claw" thread — the role/RAM budget decides per board. |
| **W6** | **HAT+ 2 decision**: revisit when (a) an independent benchmark exists, (b) hailo-ollama hosts ≥4B or the 1.5B class passes W4 evals for the W1 scope. Until then CPU inference suffices for batch cadence. | — | — | $130; Pi-5-only; would live on VolcanoAI (the Ollama host), freeing CPU/RAM. |
| **W7** | **Edge anomaly witness (optional, later)**: ESP-DL/tflite-micro autoencoder over the claw's local telemetry ring (heap/temp/RSSI/rx-rate) → `anomaly_score` as a *witness signal* to mini (never an actuator). New signal class → closed enum + seeds (coverage-gated). | L | med | Genuine "think different": the edge learns its own normal. |
| **W8** | **Display v2** — §5. | M | low | `pr/display-pages` per FORK.md. |

> **W6 DECIDED 2026-07-03: DO NOT BUY (defer indefinitely).** Both criteria
> resolved against the purchase:
> (a) **Independent benchmarks now exist and refute the vendor-community
> speed claim** we had flagged [B, single-sourced]. Measured on the HAT
> (schwab.sh, Q4_0): qwen2.5-instruct:1.5b **6.76 tok/s**, qwen2:1.5b
> 8.03, llama3.2:3b **2.65** — nowhere near "30–50"; CNX Software's
> independent test found the **Pi 5 CPU sometimes FASTER than the HAT**
> (9.04 vs 6.7 tok/s on qwen2-1.5b).
> (b) **hailo-ollama remains a closed, dated model set** (max qwen3:1.7b in
> GenAI zoo 5.3; **Qwen3-4B unsupported, DFC conversion fails**; users
> cannot add models) — our production model cannot run on it. And the
> other half of (b) — "the 1.5B class passes W4 evals" — is now TRUE but
> **on the CPU**: qwen2.5:1.5b-instruct passed the gate **8/8 = 1.0 at
> 3–4× the 4B's speed** (suite 4.7 min vs 13; ledger 2026-07-03T23:00),
> making the accelerator's remaining case (offload) moot for our
> occasional-batch workload.
> **Revisit triggers**: hailo-ollama ships ≥4B-class models AND an
> independent decode benchmark beats the CPU ≥2×, OR a vision/VLM workload
> enters the fleet (what the Hailo silicon is actually good at).
> **Side-finding for the fallback tier**: the 1.5B's 1.0 is pass@1 with
> both same-day fixes in place — keep the 4B default (quality margin,
> 4-run history), but the 1.5B is now a MEASURED latency option
> (env-var repoint) if oracle interactivity ever matters.

---

## 5. Display v2 — "tier-honest glance" (the display improvement)

> **STATUS 2026-07-03 (same day): BUILT on fork branch `pr/display-pages`
> (commit `0b4567d`, pushed to `Nursedude/WireClaw` + backup).** Compile
> VERIFIED both envs: `esp32-s3-heltec-v4` SUCCESS (RAM 52.6%, flash 61.1%,
> 0 warnings in touched files) + stock `esp32-s3` SUCCESS (guarded-optional
> proof). Deltas from the spec below: alert payload is `age_s` (age at push,
> monotonic-safe) instead of `raised_ts` (no epoch dependency — honest on an
> RTC-less device); an incoming alert also yanks the glass back to the
> status page. On-glass behavior is BELIEVED until the next claw flash
> (rebuild `dudeclaw` per FORK.md, ride `+dudeclaw.15`); brain-side pusher
> (`display_tier` from `claw_metrics_push.py`) is W1-adjacent MeshForge work,
> not yet written.

**Goal:** standing at the radio, one glance answers: *who is thinking right
now, is the mesh alive, and is anything paging?* — and every one of those
answers stays **honest when the brain is gone** (the whole point of this arc).

Grounded in the live code: 128×64 SSD1306, `ArialMT_Plain_10`, 13 px rows,
2 remote metric rows with the 30-min `(old)` suffix, headless-safe I2C probe
(`display.cpp` on `pr/display-status-screen`).

### 5.1 Page system
`WIRECLAW_OLED_PAGES` build flag (guarded-optional, same pattern as
`WIRECLAW_OLED`). **USER/PRG button (GPIO0)** short-press cycles pages;
auto-return to page 1 after 60 s idle. GPIO0 is the boot-strapping pin — input
+ pullup is safe post-boot; **[B] the V4 pinmap must confirm GPIO0=USER at
build time** (V3 documents it; V4 is near-identical; Meshtastic runs a user
button on these boards). No button → optional 10 s auto-rotate flag.

- **Page 1 — STATUS** (current screen) + one change: the **tier glyph** (5.2).
- **Page 2 — EARS** (all locally computed → survives brain loss): LoRa RX/h
  sparkline (24×1 h ring), last-heard age, TX count + airtime-guard state, BLE
  seen count. The mesh-ears health the 06-21 doc wanted, on glass.
- **Page 3 — SELF** (local): heap + chip-temp sparklines, RSSI, uptime,
  battery volts (once `pr/vbat-battery-read` lands its ADC read).

Sparkline: 56×16 px, 24-bucket `uint16_t` ring (48 B/series; 4 series <200 B
RAM), min-max scaled, `drawLine` between points, **<2 samples renders
"warming" — absence is absence, never a flat fake line** (#80 class).

### 5.2 The tier glyph — who is thinking
Row 0 gains one glyph beside the NATS marker: **`F`** frontier / **`L`** local
LLM / **`R`** rules-only, pushed by the brain (new `display_tier` NATS tool;
`claw_metrics_push.py` computes it: F = last cadence succeeded via claude CLI,
L = local fallback ran (W1), R = neither). Firmware applies **freshness decay
independent of the pusher**: <15 min → glyph; 15–30 min → `glyph?`; >30 min →
**`SOLO`** (mirrors the existing 30-min metric-stale rule). NATS down → `N-`
already shows; the tier glyph keeps decaying on its own clock, so a dead brain
can never leave a fresh-looking `F` on the glass. *The display is where the
degraded-brain ladder becomes operator-visible.*

### 5.3 Alert banner — the claw as a paging witness
New NATS tool `display_alert {"class": "...", "raised_ts": N}`: page 1's rows
39–63 become an **inverted banner** — signal class + a locally-ticking age
("volcanoai host_frozen · 12m"). Cleared **only** by an explicit empty
`display_alert`; a NATS drop mid-alert leaves the banner up with age still
climbing (honest: unresolved-as-far-as-anyone-proved). This gives the fleet a
paging path that survives ntfy/DNS loss — the exact hole Issue #81's
send-retry patched in software, now with a hardware witness. Complements, not
replaces, mini's queue.

### 5.4 Housekeeping
1-px vertical jitter every 10 min (SSD1306 burn-in; static rows). Honest-
failure-modes walk: I2C-absent stays headless (existing probe); unparseable
tier/alert payload → `?` / no banner change, never a default-`F` (#80: error
must not map to the healthiest-looking value); ring buckets are
observed-tick-indexed, not wall-clock (RTC-less discipline, #74/#80).

**Implementation:** new `pr/display-pages` branch; ~250–300 lines C++ touching
`display.cpp/h`, `tools.cpp` (2 new tools + discovery), `main.cpp` (button
poll); no new libraries; RAM cost <1 KB against the lean build's ~30 kB steady
free heap (Phase-3 soak record) — safe, but confirm heap-flat on the first
soak like BLE did; app0 partition has room (2.56 MB). Rebuild `dudeclaw`
mechanically per FORK.md; ride the staged `+dudeclaw.15` banner fix on the
same flash.

---

## 6. Safety + honesty constraints (non-negotiable, restated for this arc)

1. **MF021 stands**: mini's engine/sources/actions stay observation-only; tier
   L reaches actuation only through the candidate→ratify path.
2. **Ratification stands**: the Phase-B lesson is the governing precedent — a
   structurally-valid, semantically-wrong rule is exactly what a 3–4B model
   produces on a bad day. Grammar constraints fix *syntax*, never *judgment*.
3. **Tier provenance everywhere**: any artifact tier L produces carries
   `brain_tier: local` (brief lines, candidate metadata, display glyph). A
   fallback that impersonates the frontier is the #80 defect class at
   system scale.
4. **Resource caps on local inference** (the freeze-class guard): sequential
   only, `systemd-run` scoped MemoryMax/CPUQuota, never parallel model loads —
   the fallback brain must be incapable of taking down its own host.
5. **RF actuation is never LLM-autonomous**: `mesh_send`, band choice, and
   anything §97-shaped stay human-ratified regardless of tier (06-21 §4).

---

## 7. Open questions / not verified this turn

1. **[U]** Hailo-10H real-world tok/s + model-conversion friction — independent
   benchmark pending (Geerling 403'd). Gate for W6.
2. **[U]** WireClaw agent-loop triggers beyond the web-chat tab, and empty-
   api_key gating — read `main.cpp` trigger sites at W5 time.
3. **[U]** Ollama `/v1/chat/completions` tool-calling against the firmware's
   exact request shape — one curl + one live claw round-trip before enabling.
4. **[B]** Heltec **V4** USER button = GPIO0 — confirm on the official V4
   pinmap before wiring the page-flip (V3 documented; V4 "almost identical").
5. **[B]** Lexical-beats-embeddings on this corpus — W4 evals decide, not taste.
6. **[U]** qwen3-4B actual tok/s on *this* Pi 5 under the W4 resource caps —
   measure during the first eval run (2–5 tok/s expected per §2.1).

---

## 8. Sources

**Fetched primary:** arXiv 2511.07425 (SBC LLM inference) · raspberrypi.com AI
HAT+ 2 announcement · cnx-software.com AI HAT+ 2 coverage · fork source
(`~/src/wireclaw-dudeclaw`: `llm_client.cpp`, `main.cpp`, `display.cpp/h`,
`FORK.md`, `platformio.ini`) · local commands (`free -h`, `systemctl
is-active ollama`, Ollama tag list) — all quoted above.

**Search-verified secondary:** ollama.com/library qwen3:4b-instruct-2507 ·
huggingface.co Qwen3-4B-Instruct-2507 · docs.ollama.com structured-outputs +
independent write-ups (danielclayton.co.uk, glukhov.org) · BentoML/SiliconFlow
2026 SLM roundups · tinyweights.dev Pi-5 LLM guide · hailo.ai + raspberry.tips
(HAT+ 2 perf, single-sourced) · Espressif ESP-DL / tflite-micro coverage
(mybytenest.com, zediot.com) · heltec.org V4 product page + Meshtastic button
docs.

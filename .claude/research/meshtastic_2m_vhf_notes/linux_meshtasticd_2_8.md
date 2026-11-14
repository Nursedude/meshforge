# Meshtastic 2.8.x on Linux (meshtasticd / portduino): peripherals, config.yaml, and develop features

Researched 2026-11-14. Method: shallow `git clone` of github.com/meshtastic/firmware (depth 200 plus all branches, and tags fetched at depth 1) into the session scratchpad, read with `git show` / `git diff` / `git grep` only. Nothing was built or run. Refs compared:
- `v2.7.26.54e0d8d`: stable, tag commit dated 2026-06-23
- `v2.8.0.47db0e3`: 2026-08-31
- `v2.8.2.d5f1619`: latest 2.8 alpha, 2026-11-03, on the `2.8.2` release branch. It is **not** an ancestor of develop, so `git log v2.8.2..develop` also lists cherry-picked commits. "In 2.8.2" below was decided from file contents at the tag, not from log ranges.
- `origin/develop` HEAD `c7f895df1`: 2026-11-14 17:26 UTC
- `debian/changelog` on develop already has a `2.8.3.0` entry dated 2026-11-02, so 2.8.3 is the next version in packaging. VERIFIED that the changelog text says so; no release date found.

Tag meanings. VERIFIED = read in code or a primary file at the named ref this session. BELIEVED = reasoned from code without running it. UNKNOWN = not checked or could not be checked.

Shorthand: `C/<sha>` = https://github.com/meshtastic/firmware/commit/<sha>. `B/<ref>/<path>` = https://github.com/meshtastic/firmware/blob/<ref>/<path>.

## config.yaml: new or changed keys and sections, 2.7.26 → 2.8.2 → develop

### Takeaway
Five new config areas reached 2.8.2: `Bluetooth:`, a `Security:` section with `AdminKeys`, gpsd as a GPS source, the HUB75 display, and joystick/Arcade-Bonnet input. Develop adds two more, both still unreleased: the `Sensors:` list (telemetry from shell commands) and `Security.DMShell`, which is opt-in. A new `--check` flag validates the config.

### Cited Findings
- VERIFIED: set-diff of every `yamlConfig["X"]["Y"]` key parsed in PortduinoGlue.cpp, 2.7.26 vs develop. New keys: `Bluetooth.{Enabled,AdapterId}`, `Display.HUB75`, `GPS.{GpsdHost,GpsdPort}`, `Input.{ArcadeBonnet,ArcadeBonnetButtons,JoystickDevice,JoystickButtons}`, `Lora.{DIO3_TCXO_DELAY_US,IRQ_DIO_NUM,LR2021_IRQ_DIO_NUM,LR2021_MAX_POWER,LR2021_MAX_POWER_HF,TCXO_OPTIONAL}`, `Security.{AdminKeys,DMShell}`, `Sensors`. No key was removed. — [PortduinoGlue.cpp develop](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.cpp), [PortduinoGlue.cpp v2.7.26](https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/platform/portduino/PortduinoGlue.cpp)
- VERIFIED: at the v2.8.2 tag, `bin/config-dist.yaml` already has `GpsdHost`/`GpsdPort` (line 118), `HUB75:` (173), `ArcadeBonnet: 0x26` (232), `Bluetooth:` (265) and `AdminKeys` (279). It does **not** have `Sensors:` or `DMShell:`. The whole diff from 2.8.2 to develop in config-dist.yaml is those two blocks (+17 lines). — [config-dist.yaml v2.8.2](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/bin/config-dist.yaml); [develop](https://raw.githubusercontent.com/meshtastic/firmware/develop/bin/config-dist.yaml)
- VERIFIED, quoted from config-dist.yaml on develop:
  - `Lora:` gains `DIO3_TCXO_DELAY_US: 1000` ("TCXO start-up wait, SX126x/LR11xx; default 1000 (RadioLib's own is 5000)").
  - `Lora:` gains `TCXO_OPTIONAL: true` ("Probe both oscillators... SX126x/LR20x0/LR11xx only").
  - `Lora:` gains `IRQ_DIO_NUM: 9` for the LR2021. The default is DIO5, and the file warns that "many carriers also drive [DIO5] as an RF switch line... the radio will report init success and then receive nothing".
  - `GPS:` gains `GpsdHost: localhost` / `GpsdPort: 2947`, "mutually exclusive with" `SerialPath`.
  - `Display:` gains `Panel: HUB75` plus a `HUB75:` block ("Raspberry Pi only, via hzeller/rpi-rgb-led-matrix... must run as root (or with /dev/gpiomem access) and on-board audio disabled").
  - `Input:` gains `JoystickDevice` (evdev gamepad) with `JoystickButtons` mapping, and `ArcadeBonnet: 0x26` (MCP23017 over I2C).
  - `Bluetooth: Enabled / AdapterId: hci0` ("Advertise a BLE peripheral via BlueZ... (Linux only)").
  - `Security: AdminKeys` ("Up to 16, and up to 3 of them are reflected into the node's own security config... this file is the authority: a key removed from here loses access, and remote admin cannot drop it").
  — [config-dist.yaml develop](https://raw.githubusercontent.com/meshtastic/firmware/develop/bin/config-dist.yaml)
- VERIFIED: `Bluetooth.Enabled` is parsed as `.as<bool>(false)`, so BLE is off unless enabled in the YAML. The adapter defaults to `hci0`. — [PortduinoGlue.cpp develop L1474-1483](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.cpp)
- VERIFIED: admin keys in config.yaml, "beyond the protobuf's three", landed on develop 2026-10-03 as #12056. — [C/c7826791d](https://github.com/meshtastic/firmware/commit/c7826791d)
- VERIFIED: develop's argp options are `--port/-p`, `--config/-c`, `--hwid/-h`, `--sim/-s`, `--verbose/-v`, `--output-yaml/-y`, `--check` ("Check the configuration for problems, print a report, and exit") and `--service` (Windows only). `src/platform/portduino/ConfigCheck.cpp` (+1467 lines vs 2.7.26) exists at the v2.8.2 tag. — [PortduinoGlue.cpp develop L328-336](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.cpp)
- VERIFIED: develop adds a `Sensors:` list, merged 2026-11-13 as #12276, with air-quality types added the same day in #12305. Each entry is `Type` (an EnvironmentMetrics or AirQualityMetrics field name) plus `Command`, with an optional `Scale`. Values are cached for 30 s and commands are killed after 2 s. "An unknown Type or missing Command stops meshtasticd". — [C/f815a3d36](https://github.com/meshtastic/firmware/commit/f815a3d36), [C/d3a285fd0](https://github.com/meshtastic/firmware/commit/d3a285fd0)
- VERIFIED: the top-level sections in develop config-dist.yaml are Lora, GPS, I2C, Display, Touchscreen, Input, Logging, Webserver, Bluetooth, HostMetrics, (commented) Sensors, Security, Config, General. `I2C:` is still the single key `I2CDevice: /dev/i2c-1`. — [config-dist.yaml develop](https://raw.githubusercontent.com/meshtastic/firmware/develop/bin/config-dist.yaml)

### available.d presets (`bin/config.d/` installs to `/etc/meshtasticd/available.d`)
- VERIFIED: preset file count is 63 at v2.7.26, 72 at v2.8.2 and 73 on develop. — `git ls-tree` at each ref; [bin/config.d develop](https://github.com/meshtastic/firmware/tree/develop/bin/config.d)
- VERIFIED: files added since 2.7.26 are `display-hub75-64x64.yaml`, `femtofox/femtofox_E80-900M2213S.yaml`, `femtofox/femtofox_LR1121 generic.yaml`, `femtofox/femtofox_WIO-LR1121.yaml`, `lora-PiTastic-1W.yaml`, `lora-pimesh-1w-v1.yaml`, `lora-pimesh-1w-v2.yaml`, `lora-usb-frametastic-1262.yaml` (renamed from lora-frametastic in #12104), `lora-usb-rak19714.yaml` (RAK19714 USB SX1262, #11616) and `lora-cardputer-zero.yaml`. All are in 2.8.2 except `lora-cardputer-zero.yaml`, which is develop-only (#12341, 2026-11-11). `lora-piggystick-lr1121.yaml` was edited. — [C/7de0f0d8e](https://github.com/meshtastic/firmware/commit/7de0f0d8e), [C/be2f68b5a](https://github.com/meshtastic/firmware/commit/be2f68b5a), [C/6f0aec5ef](https://github.com/meshtastic/firmware/commit/6f0aec5ef)
- VERIFIED: the CardputerZero preset is `Module: sx1262` on `spidev0.1`, IRQ 23 / Reset 26 / Busy 22, `DIO2_AS_RF_SWITCH`, `DIO3_TCXO_VOLTAGE: 3.0`, `support: community`. It notes that the user must also power `ext_5v_out` and drive a PI4IOE5V6408 expander pin, and that "this YAML does not configure the expander". — [lora-cardputer-zero.yaml](https://github.com/meshtastic/firmware/blob/develop/bin/config.d/lora-cardputer-zero.yaml)
- VERIFIED: other presets on develop (carried from 2.7.x) include `lora-Adafruit-RFM9x`, MeshAdv 900M30S / Mini-900M22S (plus ecb41-pge, lyra-zero and ok3506 carrier variants), NebraHat 1W/2W, ZebraHat 1W/2W, RAK6421 with 13300/13302 slot1/slot2, `lora-hat-rak-6421-pi-hat`, `lora-waveshare-sxxx`, `lora-pinedio-usb-sx1262`, `lora-usb-meshstick-1262`, `lora-usb-meshtoad-e22`, `lora-usb-umesh-1262/1268-30dbm`, `lora-station-g3`, `lora-starter-edition-sx1262-i2c`, OpenWRT (BPI-R4, OpenWRT One mikroBUS), MUI `X11_480x480`, and Waveshare 1.44/2.8 displays. — [bin/config.d develop](https://github.com/meshtastic/firmware/tree/develop/bin/config.d)

### Inferences
- BELIEVED: a MeshForge config generator or validator that whitelists keys will need the new `Bluetooth`, `Security`, `GPS.Gpsd*` and `Lora.TCXO_*/IRQ_DIO_NUM` keys for 2.8.2, and `Sensors`/`Security.DMShell` for 2.8.3/develop. `meshtasticd --check` is the upstream validator worth calling, but it is not run here.

### Gaps
- UNKNOWN: whether `--check` exits non-zero on warnings. It was not run.
- UNKNOWN: whether the `Meta:` block (name/support/compatible) in presets is new in 2.8 or older. Not diffed.

## Radios on Linux: module support, CH341, LR11xx/LR2021, SX1278/RF95 and a 2 m HAT

### Takeaway
The meshtasticd `Lora.Module` enum is 2.7.26's list plus `lr2021`. CH341 USB-SPI got faster bus handling in develop. 2.8.2 adds ITU 2 m, 1.25 m and 70 cm amateur regions with 15.6 kHz "TINY" presets. Because the `RF95` module is RadioLib's SX1278 class with the frequency range check commented out, a 2 m SX1278 HAT looks code-feasible on meshtasticd 2.8.2. That is BELIEVED, not tested.

### Cited Findings
- VERIFIED: the `lora_module_enum` / YAML names on develop are `sim, auto, RF95, sx1262, sx1268, sx1280, lr1110, lr1120, lr1121, LLCC68, lr2021`. v2.7.26 has the same list without `lr2021`. — [PortduinoGlue.h develop L62-73, L163-166](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.h); [v2.7.26 L35-45](https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/platform/portduino/PortduinoGlue.h)
- VERIFIED: default max powers on develop are sx126x 22, sx128x 13, lr1110 22, lr1120 13, lr2021 22 / lr2021_hf 12, rf95 20 (dBm). — [PortduinoGlue.h develop L189-195](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.h)
- VERIFIED: "Portduino: Fix LR2021 switch tables, power ceilings and IRQ handling" (#11382) was merged 2026-09-16. LR2021 RX timing on the configured CR (#12183) was merged 2026-10-20. — [C/2d6dad9ee](https://github.com/meshtastic/firmware/commit/2d6dad9ee), [C/e069a8d49](https://github.com/meshtastic/firmware/commit/e069a8d49)
- VERIFIED: CH341 change #12013 (2026-10-01) reduces USB-bus waits per radio command. A BUSY read that follows a BUSY-low read is answered from cache within 1 ms; delays under 100 µs spin instead of sleeping; with a libch341 that has `pinedio_transceive_select()` and `Lora.CS` = D0, CS travels with the SPI transfer. — [C/dee945cd6](https://github.com/meshtastic/firmware/commit/dee945cd6)
- VERIFIED: the commit titled "ch341 server fixes" (#12016, 2026-10-07) actually carries LBT/CAD fixes. SX128x CAD was passed a raw value that meant a 1-symbol scan and now uses `CAD_ON_4_SYMB`. The SX126x slot-time model now uses ~4.5 symbols. There is also an ESP32 light-sleep race fix. — [C/95c57c27b](https://github.com/meshtastic/firmware/commit/95c57c27b)
- VERIFIED: the libch341 dependency moved from `pine64/libch341-spi-userspace` (2e5ff75 at 2.7.26) to the fork `meshtastic/libch341-spi-userspace` (027cde5 on develop; URL change #12042). — [variants/native/portduino.ini develop](https://github.com/meshtastic/firmware/blob/develop/variants/native/portduino.ini), [C/e1bcaba80](https://github.com/meshtastic/firmware/commit/e1bcaba80)
- VERIFIED: ports of the CH341 HAL to Windows (CH341DLL) and to WASM/WebUSB (`src/platform/portduino/wasm/js/ch341.js`) exist on develop. — `git diff --stat v2.7.26..develop -- src/platform/portduino`
- VERIFIED: "Fix RF95 optional pins and CannedMessage initialization" (#11913) was merged 2026-10-18. — [C/31bcb2ae0](https://github.com/meshtastic/firmware/commit/31bcb2ae0)
- VERIFIED: on portduino, `Module: RF95` builds an `RF95Interface`, whose radio is `RadioLibRF95 : SX1278`. Its `setFrequency()` has the range check commented out (`// RADIOLIB_CHECK_RANGE(freq, 862.0, 1020.0, ...)`) and calls `SX127x::setFrequencyRaw(freq)`. — [RadioLibRF95.cpp develop L8, L58-63](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioLibRF95.cpp), [RadioInterface.cpp L396-397](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioInterface.cpp)
- VERIFIED: the region table on develop has `ITU1_2M` 144.0-146.0 MHz (default slot 26 = 144.510 MHz), `ITU2_2M` 144.0-148.0 (slot 51), `ITU3_2M` 144.0-148.0 (slot 33), `ITU2_125CM` 220-225, and `ITU1/2/3_70CM`. The 2 m regions use `PROFILE_HAM_20KHZ` (TINY presets, padding 0.0022, licensed=true), defaulting to `TINY_FAST`. TINY_FAST is BW 15.6 kHz, SF7, CR 4/5; TINY_SLOW is 15.6 kHz, SF8, CR 4/6. — [RadioInterface.cpp develop L95, L286-349](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioInterface.cpp), [MeshRadio.h L285-294](https://github.com/meshtastic/firmware/blob/develop/src/mesh/MeshRadio.h)
- VERIFIED: `git grep -c ITU._2M` matches 3 lines in RadioInterface.cpp at v2.8.2 and none at v2.7.26. The 2 m regions are therefore new in the 2.8 line. Shallow history hides the exact introducing commit and date.
- VERIFIED: the only chip-capability gate on regions is `supportsSubGhz()`. It is overridden to `false` only by SX128x. There is no per-chip lower-frequency check, so an SX1262 (datasheet 150-960 MHz) would not be blocked from a 144 MHz region by firmware. — [RadioInterface.cpp L1106](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioInterface.cpp), [SX128xInterface.h L22-23](https://github.com/meshtastic/firmware/blob/develop/src/mesh/SX128xInterface.h)

### Inferences
- BELIEVED: on 2.8.2+, an SX1278-based 2 m HAT should configure and tune 144-148 MHz via `Lora: Module: RF95` (an `lora-Adafruit-RFM9x.yaml`-style pinmap) plus region `ITU2_2M`. This rests on three things: the SX1278 is rated 137-525 MHz, the RF95 driver applies no 862-1020 MHz clamp, and SX127x supports a 15.6 kHz bandwidth. Not run on hardware. Checks to upgrade this claim: `meshtasticd --check` with that YAML, then a TX observed on an SDR at the slot frequency, with RX confirmed at the far end.
- BELIEVED: an SX1262 HAT on a 2 m region would be operating below its 150 MHz datasheet floor without any firmware warning. Expect poor or no performance there, since the HAT's matching network is band-specific in any case.
- BELIEVED: 2 m on 2.7.26 stable is not possible on meshtasticd, because the regions do not exist there.

### Gaps
- UNKNOWN: who introduced the 2 m/1.25 m/70 cm regions and when. History before the depth-200 graft is not available.
- UNKNOWN: real CH341 throughput numbers before and after #12013. The commit text gives mechanism only.
- UNKNOWN: whether CH341 #12013/#12016 are in the v2.8.2 tag. They merged to develop before the 2.8.2 tag date (2026-11-03), but 2.8.2 sits on a release branch; the `USBHal.h` diff at 2.8.2 (+68 lines) suggests yes. BELIEVED.
- Ebyte E22 HAT and Waveshare/MeshAdv specifics beyond preset filenames were not examined.

## BLE, mDNS, DMShell, MCP, HostMetrics, web server, JSON, sim sensors, I2C, GPS, displays, input, notifications

### Takeaway
2.8.2 meshtasticd is a much more "host-integrated" daemon. It now has BlueZ BLE (opt-in), Avahi mDNS, gpsd, libnotify desktop notifications, HUB75, gamepad input, Sensirion I2C sensors, and jsoncpp-based JSON. **Security finding:** in the 2.8.2 tag, DMShell (a remote login shell over the mesh) is compiled into every Linux build and created unconditionally. It authorizes the local API (`from == 0`) when the node is not managed, and senders on a channel named "admin". Develop fixed this on 2026-11-10 (#12337: opt-in, PKI admin keys only).

### Cited Findings
- **DMShell**:
  - VERIFIED: at v2.8.2, `DMShell.h` defines `MESHTASTIC_HAS_DMSHELL 1` for `ARCH_PORTDUINO && __linux__`, and `Modules.cpp` runs `dmShellModule = new DMShellModule();` with no config gate.
  - VERIFIED: in 2.8.2, `isAuthorizedPacket()` returns `!config.security.is_managed` for `mp.from == 0`, and returns `config.security.admin_channel_enabled` when the channel name matches the admin channel. Only after that does it accept PKI admin keys.
  — [DMShell.cpp v2.8.2 L476-492](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/src/modules/DMShell.cpp), [DMShell.h v2.8.2 L14-18](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/src/modules/DMShell.h), [Modules.cpp v2.8.2 L229-234](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/src/modules/Modules.cpp)
- VERIFIED: develop #12337 (2026-11-10) says of DMShell: "It was built into every Linux build and on by default". The fix adds `Security.DMShell` (default false; the module is only created when true). It accepts only "a PKI DM whose sender key is in AdminKeys"; local API packets are refused, and the legacy admin channel no longer authorizes a shell. DMShell first landed 2026-10-02 (#11976). TERM/locale fixes are #12054 and the test client is #12111 (`bin/dmshell_client.py`). — [C/e8347ca1b](https://github.com/meshtastic/firmware/commit/e8347ca1b), [C/977b1d775](https://github.com/meshtastic/firmware/commit/977b1d775), [C/315ec0e4e](https://github.com/meshtastic/firmware/commit/315ec0e4e)
- **BLE via BlueZ** (#11396, merged 2026-10-01):
  - VERIFIED: adds the standard Meshtastic GATT service (toRadio/fromRadio/fromNum/logRadio). It registers a GATT app, LE advertisement and pairing agent with bluetoothd over org.bluez D-Bus using sdbus-c++ 1.x or 2.x ("Debian bookworm/Ubuntu 24.04 ship 1.x, trixie/Fedora ship 2.x"). It compiles out when the sdbus-c++ dev package is absent.
  - VERIFIED: the pairing passkey is shown on screen unless the mode is NO_PIN.
  - VERIFIED: follow-ups are a BLE setup retry rate-limit (#12043) and reporting the BLE address in DeviceMetadata.ble_address (#12281, 2026-11-03).
  — [C/8c0abbd52](https://github.com/meshtastic/firmware/commit/8c0abbd52), [LinuxBluetooth.cpp](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/LinuxBluetooth.cpp), [portduino.ini](https://github.com/meshtastic/firmware/blob/develop/variants/native/portduino.ini), [C/1546ebee2](https://github.com/meshtastic/firmware/commit/1546ebee2)
- **mDNS** (#12032, 2026-10-12; recovery and backoff fixes #12142, 2026-10-12):
  - VERIFIED: registers service `_meshtastic._tcp` on the API TCP port, with TXT records `shortname`, `id` (node ID) and `pio_env`, via Avahi (Linux) or Bonjour.
  - VERIFIED: compiles out without avahi-client, and the commit says it is disabled "when daemon unavailable".
  — [NativeDiscovery.cpp develop L163, L275-292](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/NativeDiscovery.cpp), [C/1e3a38fc8](https://github.com/meshtastic/firmware/commit/1e3a38fc8), [C/2148c6253](https://github.com/meshtastic/firmware/commit/2148c6253)
- **Desktop notifications** (#10216, 2026-10-01): VERIFIED. Message notifications go through libnotify behind `HAS_LIBNOTIFY`, which is not present in buildroot/OpenWrt builds. — [C/f5940ab5d](https://github.com/meshtastic/firmware/commit/f5940ab5d)
- **MCP server**: VERIFIED. #10194 (2026-04-18) added a Python `mcp-server/` dev/test tool to the firmware repo; it is present at the v2.7.26 tag. #10861 (2026-07-02) extracted it to `meshtastic/meshtastic-mcp`, and the 2.8.2 tree no longer has `mcp-server/`. It is a developer harness, not part of meshtasticd. — [C/6b15571e1](https://github.com/meshtastic/firmware/commit/6b15571e1), [C/510e9796f](https://github.com/meshtastic/firmware/commit/510e9796f)
- **JSON retention** (#10152, 2026-06-03 on develop): VERIFIED. Removes the firmware's in-tree JSON libs (`src/serialization/JSON*.cpp`, nRF52 serializer) "while retaining Meshtasticd JSON support". The Linux build now links `-ljsoncpp` (libjsoncpp-dev / libjsoncpp26 added to debian/control and the Dockerfiles; not present at 2.7.26). It touches MQTT.cpp (-166 lines) and http/ContentHandler.cpp. — [C/f86cb7781](https://github.com/meshtastic/firmware/commit/f86cb7781), [Dockerfile develop](https://github.com/meshtastic/firmware/blob/develop/Dockerfile)
- **"Native sensors"** (#10748, 2026-06-19): VERIFIED. It enables the Sensirion libraries on native, so it is real I2C sensor support, not a simulator. The develop `environmental_base` lib_deps (43 entries vs 35 at 2.7.26) include BMP280/BMP085/BME280, DPS310, MCP9808, INA219/INA226/INA260/INA3221, AHTX0, LIS3DH, LSM6DS, MPU6050, TSL2591/TSL2561, BH1750, LTR390, PCT2075, MLX90614/MLX90632, SHT (arduino-sht), SCD4x, SCD30, SFA3x, HM3301 PM2.5, SPA06, ICM20948, ICM42670P, BMM150, MMC5983MA, QMC5883L, MAX3010x, AS3935, and DFRobot rainfall. The v2.8.2 platformio.ini contains Sensirion I2C entries (6 matches). — [C/ca7d82629](https://github.com/meshtastic/firmware/commit/ca7d82629), [platformio.ini develop](https://github.com/meshtastic/firmware/blob/develop/platformio.ini)
- **I2C**: VERIFIED. config-dist still says "I2C displays are generally auto-detected" with `I2CDevice: /dev/i2c-1`. `ScanI2CTwoWire.cpp` has `ARCH_PORTDUINO` paths. `EnvironmentTelemetry.cpp` adds `CommandSensor` on portduino, read last "so a configured command wins a shared field". — [ScanI2CTwoWire.cpp](https://github.com/meshtastic/firmware/blob/develop/src/detect/ScanI2CTwoWire.cpp), [EnvironmentTelemetry.cpp](https://github.com/meshtastic/firmware/blob/develop/src/modules/Telemetry/EnvironmentTelemetry.cpp)
- **GPS**: VERIFIED. New `GpsdSerial.cpp/.h` (+230/+49 lines, present at 2.8.2) lets gpsd at `GpsdHost:GpsdPort` (default 2947) be the GPS source instead of `SerialPath`. — [GpsdSerial.cpp](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/GpsdSerial.cpp)
- **Displays**: VERIFIED. The `screen_modules` enum on develop is `x11, fb, st7789, st7735, st7735s, st7796, ili9341, ili9342, ili9486, ili9488, hx8357d, hub75`, which is 2.7.26's list plus `hub75` (HUB75Native.cpp). Touchscreen drivers are unchanged: `xpt2046, stmpe610, gt911, ft5x06`. LovyanGFX was bumped from 1.2.21 to 1.2.28. — [PortduinoGlue.h develop L59-60](https://github.com/meshtastic/firmware/blob/develop/src/platform/portduino/PortduinoGlue.h)
- **Input**: VERIFIED. evdev `KeyboardDevice` (existing), new `JoystickDevice`/`JoystickButtons`, and the Adafruit Arcade Bonnet driver (#12116, 2026-10-10). — [C/dbc7daecc](https://github.com/meshtastic/firmware/commit/dbc7daecc)
- **HostMetrics**: VERIFIED. The keys (`ReportInterval`, `Channel`, `UserStringCommand`) are unchanged from 2.7.26 in the config-dist diff. `HostMetricsModule` is created on every `ARCH_PORTDUINO` build. — [Modules.cpp v2.8.2](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/src/modules/Modules.cpp)
- **Other portduino items on develop**: VERIFIED. LinuxPower.cpp plus a test-menu "power off the host" action (#11920). The C++ standard moved from gnu++17 to gnu++20 (#12069). Also: `-DMESHTASTIC_EXCLUDE_RTTTL` on native, a macOS MUI simulator target (#11739), a Windows service build, and a WASM build target. — [C/8b8dc611c](https://github.com/meshtastic/firmware/commit/8b8dc611c), [C/9015b2213](https://github.com/meshtastic/firmware/commit/9015b2213), [C/2c595e935](https://github.com/meshtastic/firmware/commit/2c595e935)

### Inferences
- BELIEVED: any MeshForge box running a 2.8.2 alpha meshtasticd has an always-on DMShell. A TCP API client on port 4403 (from == 0) on an unmanaged node may be able to open a host shell as the `meshtasticd` user. This is read from the authorization code, not exercised. That would make MeshForge's own API connections, and anything else that can reach 4403, shell-capable. Treat 2.8.2 alpha as "do not deploy", or firewall 4403 and set `is_managed`, until a build with #12337 ships (2.8.3?).
- BELIEVED: mDNS advertisement of `_meshtastic._tcp` happens automatically when avahi-daemon runs, so 2.8 boxes will announce their node ID and short name on the LAN. This could be useful for MeshForge discovery. Exposure is LAN-only.
- BELIEVED: since 2.8, gpsd can be shared between meshtasticd and other consumers instead of meshtasticd holding the serial GPS exclusively.

### Gaps
- UNKNOWN: whether #12337 has been cherry-picked to the `2.8.2` release branch after the tag. Only the tag was inspected.
- UNKNOWN: the exact I2C auto-detect list on Linux (the ScanI2C address map was not enumerated). The lib list above is what is compiled, not what is probed.
- UNKNOWN: USB HID (as a device). Nothing was found beyond evdev input.

## Upcoming (develop after 2.8.2.d5f1619) and open PRs

### Takeaway
Develop-only Linux items not in the 2.8.2 tag: `Sensors:` command telemetry (#12276/#12305), DMShell hardening (#12337), the CardputerZero preset (#12341), and the matching config-check and test changes. Changes between 2.8.2 and develop in `src/platform/portduino` are small (+446/-24 across 14 files under bin/, debian/, portduino/). Open PRs could not be listed.

### Cited Findings
- VERIFIED: `git diff --stat v2.8.2.d5f1619 origin/develop` limited to bin/, debian/, src/platform/portduino and variants/native touches 14 files. The functional ones are config-dist.yaml (+17), lora-cardputer-zero.yaml, ConfigCheck.cpp (+75), PortduinoGlue.cpp (+188) and PortduinoGlue.h (+50), plus debian/changelog (2.8.3.0). — `git diff --stat` at those refs
- VERIFIED: develop commits from 2026-11-03 to 11-14 relevant to Linux:
  - #12305 air-quality Sensors (11-13)
  - #12276 Sensors (11-13)
  - #12341 CardputerZero (11-11)
  - #12337 DMShell opt-in (11-10)
  - #12281 BLE address in metadata (11-03)
  - #12284 LR2021 test model (11-03)
  — [develop commit log](https://github.com/meshtastic/firmware/commits/develop)

### Gaps
- UNKNOWN: open PRs. The `gh pr list` and branch-listing commands were denied by the session's permission classifier. Open PRs touching portduino therefore remain unsurveyed. A remote branch named `13302-autodetect` exists (presumably RAK13302 auto-detect). BELIEVED; its contents were not read.
- UNKNOWN: any official "2.8 beta" announcement. Release pages and meshtastic.org were not fetched.

## Packaging: apt/OBS, Docker, systemd unit

### Takeaway
2.8 packaging adds runtime dependencies for jsoncpp, sdbus-c++, avahi-client, libcurl and libnotify. It Recommends avahi-daemon and bluez, installs D-Bus and polkit policy for BLE, adds the `meshtasticd` user to `bluetooth`, and orders the unit after bluetooth/avahi.

### Cited Findings
- VERIFIED: in debian/control, 2.7.26 → develop, the Build-Depends gain `libjsoncpp-dev, libsdbus-c++-dev, libavahi-client-dev, libcurl4-gnutls-dev, libnotify-dev, g++ (>= 4:10)`, and the binary package gains `Recommends: avahi-daemon, bluez`. The v2.8.2 tag already has sdbus/avahi/notify and the Recommends line. — [debian/control develop](https://github.com/meshtastic/firmware/blob/develop/debian/control)
- VERIFIED: debian/meshtasticd.install adds `bin/dbus-1/meshtasticd.conf → /usr/share/dbus-1/system.d`, `bin/polkit-1/meshtasticd.rules → /usr/share/polkit-1/rules.d`, and an icon `org.meshtastic.meshtasticd.svg`. Presets still install to `etc/meshtasticd/available.d`. postinst adds `meshtasticd` to the `bluetooth` group. — [meshtasticd.install](https://github.com/meshtastic/firmware/blob/develop/debian/meshtasticd.install), [meshtasticd.postinst](https://github.com/meshtastic/firmware/blob/develop/debian/meshtasticd.postinst)
- VERIFIED: the `bin/meshtasticd.service` diff is `After=network-online.target bluetooth.service avahi-daemon.service` plus `Wants=bluetooth.service`. The comment says "Ordering only: a missing avahi-daemon.service must not block startup." There are no other unit changes. — [meshtasticd.service develop](https://github.com/meshtastic/firmware/blob/develop/bin/meshtasticd.service)
- VERIFIED: the Docker images (Debian `Dockerfile` and `alpine.Dockerfile`) add runtime packages `libjsoncpp26 libsdbus-c++2 libcurl4t64 libavahi-client3 libnotify4` (Debian) and `jsoncpp avahi-libs libcurl libnotify` (Alpine). — [Dockerfile](https://github.com/meshtastic/firmware/blob/develop/Dockerfile), [alpine.Dockerfile](https://github.com/meshtastic/firmware/blob/develop/alpine.Dockerfile)

### Inferences
- BELIEVED: `Wants=bluetooth.service` means a 2.8 meshtasticd install will pull bluetoothd up on boxes where it was disabled. Boxes that deliberately keep Bluetooth off should mask it or override the unit.
- BELIEVED: MeshForge's `apt-mark hold` meshtasticd policy means none of this reaches the fleet until it is deliberately rolled. The new library dependencies (libsdbus-c++ 1.x vs 2.x by distro) are one more reason to check `objdump -T`/`ldd` on the package before rolling.

### Gaps
- UNKNOWN: OBS/PPA repository names for 2.8 (alpha/beta channels). Not checked, because the web pages were not fetched.
- UNKNOWN: the RPM spec changes beyond jsoncpp. `meshtasticd.spec.rpkg` was not diffed.

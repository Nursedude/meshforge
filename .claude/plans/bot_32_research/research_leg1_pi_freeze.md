I have enough cross-checked material to write the report. One pivotal finding shapes everything: **ramoops/pstore captures kernel *panics/oopses*, not silent hard hangs** — so for a true SoC-level wedge you must first *convert the silence into a panic* (watchdog + softlockup/hung-task panic) before pstore can record anything. Here is the deliverable.

---

# Capturing & Diagnosing Daily Hard-Freezes on an RTC-less Raspberry Pi Fleet

**Scope:** Pi 4 + Pi 5, Raspberry Pi OS, microSD boot, `Storage=volatile` journald. Symptom: ~daily whole-box wedge (SSH dead, no graceful shutdown, service shows `active`/0 restarts), load ~2.2, recoverable only by manual power-cycle.

> **The single most important fact in this report:** A pure hard hang (the SoC/kernel stops scheduling entirely, no oops, no panic) writes **nothing** to pstore — files only appear after a *simulated* crash, not a real hang where you had to hard-reboot [Re: How to get the crash dump if system hangs? — https://www.spinics.net/lists/kernel-hardening/msg01705.html]. ramoops is "an oops/panic logger" and stores records keyed by `max_reason` (OOPS/PANIC) [Ramoops oops/panic logger — https://docs.kernel.org/admin-guide/ramoops.html]. **Therefore the forensics strategy is two-pronged: (1) convert the silent hang into a kernel panic so pstore can record it, and (2) arm an independent hardware watchdog so the box self-recovers and leaves a reset fingerprint.** Everything in Section B is built around this.

---

## A. Ranked Root Causes — with telltale signatures

Ranking reflects how commonly each produces a *whole-box, power-cycle-only* freeze (not a service crash) on this hardware/OS profile. Your specifics — daily cadence, load ~2.2, service stays `active`, total unreachability — most strongly fit causes **1–4**.

| # | Root cause | Why it fits a "whole box wedge" | Telltale signature | Confirm / rule out |
|---|---|---|---|---|
| **1** | **Undervoltage / brownout** (weak PSU, thin/long USB-C cable, peripheral inrush — SSDs, RF dongles) | Below ~4.63–4.75 V the SoC behaves erratically → throttle, random reboot, **and full lockups**; "lack of power means the CPU can both underperform and perform erratically" [Pi My Life Up — https://pimylifeup.com/raspberry-pi-low-voltage-warning/]; "readings below 4.75 V trigger undervoltage… random reboots, and eventual filesystem corruption" [circuitlabs — https://circuitlabs.net/raspberry-pi-5-power-requirements-and-management/] | `vcgencmd get_throttled` non-zero (esp. bits 0/16); on-screen lightning bolt; kernel "Under-voltage detected!" / "hwmon … undervoltage" lines | Poll `vcgencmd get_throttled` on a cron into a persistent file (Section B). **Decisive test:** swap to the official PSU (5V/3A Pi4; 5V/5A 27W PD Pi5) and a short thick cable; remove USB peripherals; see if freezes stop. |
| **2** | **Kernel soft-lockup / hung-task / deadlock** (driver spinlock, I/O stall, mm) | A CPU stuck in-kernel makes the box unreachable while a *systemd service* still reads `active` (it was never scheduled to die). Load ~2.2 with stuck tasks is consistent. | dmesg: `BUG: soft lockup - CPU#x stuck for Ns!`, `watchdog: BUG: soft lockup`, `INFO: task <x> blocked for more than N seconds` [Softlockup/hardlockup detector — https://docs.kernel.org/admin-guide/lockup-watchdogs.html] | **By default these only *warn*** — make them *panic* (Section B sysctls) so pstore captures the stack. PSI (`/proc/pressure`) spiking pre-freeze points to I/O or memory stall. |
| **3** | **microSD corruption / wear / I/O stall** | A failing card can stall all I/O → fs remounts read-only → box appears frozen; corruption is itself often *caused by* cause #1's brownouts | `EXT4-fs error`, `remounting filesystem read-only`, journal/I/O errors; "unexpected freezes or random reboots are indicators of storage degradation" [moldstud — https://moldstud.com/articles/p-how-to-effectively-solve-sd-card-corruption-problems-on-raspberry-pi] | `dmesg`/persistent journal for EXT4/mmcblk errors; `sudo fsck -fy`, `sudo badblocks -v` on a spare boot [Raymii — https://raymii.org/s/blog/Broken_Corrupted_Raspberry_Pi_SD_Card.html]. Note: "power supply/cable being bad is a common cause of ext4 errors" — **#3 and #1 are linked.** |
| **4** | **USB-peripheral-induced hang** (RF dongles, hubs, buggy driver, buffer exhaustion) | A misbehaving USB/UART device can flood the kernel log and exhaust buffers, cascading to total unresponsiveness — *documented Pi 5 freeze pattern* | Pre-freeze log flood, then `/dev/kmsg buffer overrun`, `rx fifo full`, `systemd-journald watchdog` timeouts → full wedge, power-cycle required [raspberrypi/linux #7184 — https://github.com/raspberrypi/linux/issues/7184] | Unplug non-essential USB; check pre-freeze logs (needs persistent journal) for repeating device messages; correlate freezes with peripheral activity. |
| **5** | **Thermal throttle → shutdown** | Throttling alone rarely *freezes*; sustained ≥85 °C throttles, and Pi 5 firmware powers off at ~110 °C / PMIC cuts at 125 °C [firmware-2712 release notes — https://github.com/raspberrypi/rpi-eeprom/blob/master/firmware-2712/release-notes.md] | `get_throttled` bits 1/2/3 (and 17/18/19); high `vcgencmd measure_temp`. A thermal *shutdown* = clean power-off, **not** a wedge needing manual cycle | Log temp on cron. With load ~2.2 and adequate cooling this is unlikely to be primary, but easy to rule out. |
| **6** | **RAM exhaustion / OOM** | OOM-killer usually kills a process (service would show restart), but heavy swap/thrash on a stalled card can *feel* like a freeze | `Out of memory: Killed process`, `oom-kill:` in dmesg; PSI memory `full` climbing | Persistent journal + PSI. Service showing **0 restarts / still active** argues *against* classic OOM of your service. |
| **7** | **Firmware / GPU / VPU / RP1 hang** | A firmware-side hang can take the whole box with no Linux log at all | Often *no* Linux-side evidence; correlates with display/camera/PCIe use; Pi 5 "failed to contact RP1 firmware", "Fatal firmware error" classes [raspberrypi/linux #6642 — https://github.com/raspberrypi/linux/issues/6642] | Update EEPROM/firmware first (Section D). If Linux logs are always empty at freeze and watchdog *still* can't reset, suspect firmware-level. |
| **8** | **Hard lockup (CPU stuck with IRQs disabled)** | Possible but **hard to detect on ARM**: the NMI/hard-lockup detector needs an NMI source many ARM configs lack | `NMI watchdog: Watchdog detected hard LOCKUP` — *if* supported [lockup-watchdogs — https://docs.kernel.org/admin-guide/lockup-watchdogs.html] | Don't rely on NMI detection on Pi. The **hardware watchdog (#B-3) is your only reliable catch** for this class. |
| **9** | **Pi 5-specific** (PMIC sequencer, power-button/EEPROM, PCIe/NVMe timing) | A class of its own — see Section D | PMIC/RP1 messages; freezes tied to reboot/power events; older EEPROM | Update EEPROM, set `POWER_OFF_ON_HALT`, check PCIe power timing fixes (Section D). |

**Adversarial cross-check / honest flags:**
- Causes **1 and 3 are physically coupled** — brownouts cause SD corruption; don't treat a fixed card as proof the PSU is fine. Fix power *first*.
- Throttling/thermal (#5) and OOM (#6) usually produce *recoverable* states (clean reboot, killed process), which **does not match** your "manual power-cycle only" symptom — they're lower-probability here, but cheap to log out.
- The "service is `active`, 0 restarts, whole box dead" detail is the strongest signal: it points at **kernel/hardware-level stalls (#1, #2, #4, #8)** rather than anything userspace.

---

## B. Arm Crash Forensics on a Pi — copy-pasteable checklist

> Paths assume Bookworm/Trixie: boot config is **`/boot/firmware/config.txt`** (older OS: `/boot/config.txt`) [itsfoss — https://itsfoss.com/use-uart-raspberry-pi/]. Items marked **[reboot]** require a reboot to take effect.

### B-0. Verify pstore is even supported on your kernel **[do this first]**
Raspberry Pi OS kernels have historically shipped **without** `CONFIG_PSTORE` — multiple reports of `CONFIG_PSTORE` not set on 64-bit images [raspberrypi/linux #5063 — https://github.com/raspberrypi/linux/issues/5063; forum t=326670 — https://forums.raspberrypi.com/viewtopic.php?t=326670]. **Verify on-box (uncertain across kernel versions — check, don't assume):**
```bash
zcat /proc/config.gz 2>/dev/null | grep -i pstore     # want PSTORE / PSTORE_RAM / PSTORE_CONSOLE =y
ls -la /sys/fs/pstore/                                  # should exist if pstore is active
mount | grep pstore                                     # pstore filesystem mounted?
```
Needed: `CONFIG_PSTORE=y`, `CONFIG_PSTORE_RAM=y`, `CONFIG_PSTORE_CONSOLE=y` [search consensus — https://forums.raspberrypi.com/viewtopic.php?t=326670]. If absent, pstore won't capture anything and you must rely on **persistent journald + hardware watchdog + UART** (B-2/B-3/B-6) instead, or run a kernel with pstore compiled in.

### B-1. ramoops / pstore overlay **[reboot]**
Reserve a RAM region that survives a warm reset so panic logs persist. Create the overlay source:
```dts
// ramoops-overlay.dts  — choose a reserved base that doesn't collide with your RAM/CMA
/dts-v1/;
/plugin/;
/ {
    compatible = "brcm,bcm2711";   // bcm2712 for Pi 5; bcm2837 for Pi3
    fragment@0 {
        target-path = "/";
        __overlay__ {
            reserved-memory {
                #address-cells = <2>; #size-cells = <1>; ranges;
                ramoops@0x0a000000 {
                    compatible = "ramoops";
                    reg = <0x0 0x0a000000 0x00100000>;  // 1 MiB region
                    record-size  = <0x00004000>;        // 16 KiB per oops/panic
                    console-size = <0x00004000>;
                    ftrace-size  = <0x00004000>;
                    pmsg-size    = <0x00004000>;
                    ecc          = <16>;
                };
            };
        };
    };
};
```
Compile, install, enable [lnxblog — https://lnxblog.github.io/2020/02/25/ramoops-on-rpi.html; forum t=199047 — https://forums.raspberrypi.com/viewtopic.php?t=199047]:
```bash
dtc -@ -I dts -O dtb -o /boot/firmware/overlays/ramoops.dtbo ramoops-overlay.dts
echo 'dtoverlay=ramoops' | sudo tee -a /boot/firmware/config.txt
sudo reboot
```
After reboot verify and test:
```bash
dmesg | grep -i ramoops        # "ramoops: using 0x100000@0x0a000000..."
echo 1 | sudo tee /proc/sys/kernel/panic_on_oops
echo c | sudo tee /proc/sysrq-trigger   # SIMULATED panic; box resets
# after it comes back:
ls /sys/fs/pstore/             # expect dmesg-ramoops-0 (+ console-ramoops-0)
sudo cat /sys/fs/pstore/dmesg-ramoops-0
```
> **Caveats (flag):** (a) `record-size`/region base must fit your DRAM map and not overlap CMA/GPU memory — the address above is illustrative; pick per-board. (b) **ramoops captures panics/oopses, not silent hangs** — this is why B-3/B-4 are mandatory, not optional [spinics — https://www.spinics.net/lists/kernel-hardening/msg01705.html]. (c) After reading dumps, clear them: `sudo rm /sys/fs/pstore/*`.

### B-2. Persistent journald **[partial reboot]**
Survive the reset so you can read the *pre-freeze* tail:
```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/persistent.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
SyncIntervalSec=30
EOF
sudo mkdir -p /var/log/journal
sudo systemctl restart systemd-journald
```
Read prior boots after a freeze: `journalctl --list-boots`, `journalctl -b -1 -e`.
> **SD-wear tradeoff (flag):** RPi OS set `Storage=volatile` deliberately to spare the card [raspberrypi/bookworm-feedback #415 — https://github.com/raspberrypi/bookworm-feedback/issues/415]. Persistent logging "generates extensive disk writes that shorten SD lifespan" [HostFission — https://hostfission.com/guides/log-storage-on-devices-with-sd-cards]. **`SyncIntervalSec`** controls flush frequency — a *longer* interval (e.g. 30–60 s) reduces wear but risks losing the **last few seconds before a hard freeze** (exactly your evidence). Mitigations: cap `SystemMaxUse`, accept the wear for the duration of the investigation, then revert; or use `log2ram`-style RAM buffering as a hybrid [forum t=392855 — https://forums.raspberrypi.com/viewtopic.php?t=392855]. For diagnosis, prefer a *shorter* sync interval despite wear.

### B-3. Hardware watchdog (bcm2835_wdt) — self-recovery + reset fingerprint **[reboot]**
This is your **only reliable catch for a true hard lockup** (#8): an on-chip timer that resets the platform if nothing pets `/dev/watchdog`, independent of kernel responsiveness [bends.se — https://bends.se/?page=notebook%2Fsbc%2Fraspberry-pi%2Fhw-watchdog; pysselilivet — https://pysselilivet.blogspot.com/2021/10/raspberry-pi-watchdog-made-simple.html].
```bash
# config.txt (some setups need the driver enabled explicitly): [reboot]
echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt

# systemd pets it automatically — set in /etc/systemd/system.conf:
sudo tee /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=14
RebootWatchdogSec=2min
EOF
sudo systemctl daemon-reexec
```
> **Hard limit (verified):** On Pi the watchdog max is ~15 s — **`RuntimeWatchdogSec` must be ≤ 15**; larger values are silently ignored and you get *no* watchdog [systemd #27427 — https://github.com/systemd/systemd/issues/27427; pysselilivet]. systemd then pings every `RuntimeWatchdogSec/2`. If the box stops scheduling for >14 s, the hardware resets it → freeze becomes an *automatic reboot* (huge availability win) and leaves a watchdog-reset trace. **Does it catch every hang?** It catches anything that stops the kernel from petting the device — soft lockups, most hard lockups. It will *not* help if firmware itself is wedged below the watchdog (#7). Combine with B-1: if the hang first trips softlockup_panic (B-4), pstore records the stack *before* the watchdog resets.

### B-4. Sysctls — turn silent lockups into captured panics **[runtime, persist via file]**
```bash
sudo tee /etc/sysctl.d/99-crash-forensics.conf <<'EOF'
kernel.panic = 10            # reboot 10s after panic (0 = hang forever — don't)
kernel.panic_on_oops = 1     # an oops becomes a panic (so pstore records it)
kernel.softlockup_panic = 1  # soft lockup -> panic -> pstore  (cause #2)
kernel.hung_task_panic = 1   # blocked task -> panic           (cause #2/#3 I/O stall)
kernel.hung_task_timeout_secs = 120
kernel.panic_on_io_nmi = 0
EOF
sudo sysctl --system
```
Behavior basis: `panic_on_oops` 1 = panic immediately; `softlockup_panic`/`hung_task_panic` 1 = panic on detection; `kernel.panic` = seconds before auto-reboot (recommended ~60 with software watchdog, lower is fine here) [/proc/sys/kernel docs — https://www.kernel.org/doc/html/v6.9/admin-guide/sysctl/kernel.html; lockup-watchdogs — https://docs.kernel.org/admin-guide/lockup-watchdogs.html].
> **Nuance (flag):** `nmi_watchdog`/hard-lockup detection is **unreliable on ARM** (no NMI on many Pi configs) — don't depend on `hardlockup_panic` [lockup-watchdogs]. The soft-lockup + hung-task panics are the workhorses here; they convert the most likely freeze classes (#2/#3/#4) into pstore-recordable panics. **Tradeoff:** setting these to panic trades "frozen forever" for "auto-reboot with a dump" — exactly what you want for a fleet, but be aware a transient stall now reboots the box.

### B-5. Continuous health sampling to persistent disk (catches power/thermal/PSI pre-freeze)
The freeze itself may leave nothing; sample *leading indicators* to a file that survives. Cron every minute (or a tiny systemd timer):
```bash
# /usr/local/bin/pi-canary.sh
TS=$(date -Iseconds)
{
  echo "$TS throttled=$(vcgencmd get_throttled) temp=$(vcgencmd measure_temp) volt=$(vcgencmd measure_volts)"
  echo "$TS load=$(cat /proc/loadavg)"
  echo "$TS cpu_psi=$(awk 'NR==1' /proc/pressure/cpu)"
  echo "$TS io_psi=$(awk 'NR==1' /proc/pressure/io)"
  echo "$TS mem_psi=$(awk 'NR==1' /proc/pressure/memory)"
} >> /var/log/pi-canary.log
```
- **`vcgencmd get_throttled`** bit decode (your power/thermal witness) [LibreELEC — https://forum.libreelec.tv/thread/17860-how-to-interpret-rpi-vcgencmd-get-throttled/; 8086.net — https://www.8086.net/tools/decode-get_throttled]:

  | Bit | Hex | Meaning (current) | | Bit | Hex | Meaning (since boot) |
  |---|---|---|---|---|---|---|
  | 0 | `0x1` | under-voltage **now** | | 16 | `0x10000` | under-voltage **occurred** |
  | 1 | `0x2` | ARM freq capped now | | 17 | `0x20000` | freq cap occurred |
  | 2 | `0x4` | throttled now | | 18 | `0x40000` | throttling occurred |
  | 3 | `0x8` | soft temp limit now | | 19 | `0x80000` | soft temp limit occurred |

  E.g. **`0x50000` = bit16 + bit18 = "under-voltage occurred + throttling occurred since boot"** — a classic weak-PSU fingerprint. `0x0` = clean. (Historical bits 16–19 latch and only clear on reboot.)
- **PSI** (`/proc/pressure/{cpu,io,memory}`, kernel ≥4.20): `some`/`full` averages over 10s/1m/5m. Rising `io full` or `memory full` in the last sample before a freeze pinpoints I/O-stall (#3) vs memory (#6) vs CPU (#2) [PSI docs — https://docs.kernel.org/accounting/psi.html; unixism — https://unixism.net/2019/08/linux-pressure-stall-information-psi-by-example/].

### B-6. Serial / UART console — the last-resort ground truth **[reboot, needs hardware]**
When Linux logs are empty and the box is wedged below the OS, a second machine on the GPIO UART captures kernel output up to the instant of death — including panics that never reach disk [itsfoss — https://itsfoss.com/use-uart-raspberry-pi/].
```bash
echo 'enable_uart=1' | sudo tee -a /boot/firmware/config.txt   # [reboot]
# On a second host with a USB-TTL adapter on GPIO14/15 + GND:
sudo screen /dev/ttyUSB0 115200    # log it: screen -L, or: minicom / picocom -l
```
> **Caveats:** Pi 3's mini-UART baud was unstable unless core clock pinned (`enable_uart=1` historically forced 600 MHz) — Pi 4/5 use the stable PL011, so this is mainly a Pi 3-era footnote [archlinuxarm — https://archlinuxarm.org/forum/viewtopic.php?f=60&t=11734]. Pick **one** suspect box, attach UART, and log to a file on the *capture* host so even a firmware-level wedge is recorded.

---

## C. Post-Mortem Discrimination — decision tree

Run this when a box comes back (after the watchdog auto-reboots it, or you power-cycle). Gather: `journalctl --list-boots` + `journalctl -b -1 -e`, `ls /sys/fs/pstore/`, `/var/log/pi-canary.log`, UART log.

```
START — box recovered. Look at the EVIDENCE that survived:

1) /var/log/pi-canary.log last sample before the gap:
   ├─ throttled bit 0/16 set (e.g. 0x50000) ........... ► CAUSE #1 UNDERVOLTAGE/BROWNOUT
   │      (corroborate: "Under-voltage detected!" in journal -b -1)
   │      → fix PSU/cable, drop USB load, retest get_throttled.
   ├─ throttled bit 2/3/18/19 set + temp high ......... ► CAUSE #5 THERMAL (verify clean
   │      power-off vs wedge; if it self-recovered cleanly it wasn't your wedge)
   ├─ io PSI "full" spiking, no power bits ............ ► CAUSE #3 (SD/IO stall) or #4
   └─ memory PSI "full" / oom in log ................. ► CAUSE #6 OOM

2) /sys/fs/pstore populated?  (dmesg-ramoops-N exists)
   ├─ YES → read it:
   │   ├─ "soft lockup - CPU#x stuck" / "watchdog: BUG" ► CAUSE #2 SOFT LOCKUP
   │   │        → read the stuck stack: which subsystem/driver.
   │   ├─ "INFO: task X blocked >N s" / hung_task ...... ► CAUSE #2/#3 (I/O or lock stall;
   │   │        if backtrace is in mmc/ext4 → SD #3)
   │   ├─ "EXT4-fs error" / "remounting read-only" .... ► CAUSE #3 SD CORRUPTION
   │   ├─ "Out of memory: Killed process" ............. ► CAUSE #6 OOM
   │   └─ USB/uart flood, "kmsg buffer overrun",
   │        "rx fifo full" .............................. ► CAUSE #4 USB-PERIPHERAL HANG (#7184)
   └─ NO (pstore empty) AND box needed MANUAL cycle ... ► TRUE HARD HANG:
            no panic was generated → CAUSE #8 (hard lockup) or #7 (firmware/RP1).
            Decisive: did the HW watchdog auto-reboot it (no human)?
              ├─ Watchdog DID reset it → kernel was alive enough to be a deep stall;
              │     check UART log for last lines; likely #2/#4 that didn't reach panic.
              └─ Watchdog did NOT reset (needed human) → wedged BELOW the watchdog
                    → CAUSE #7 FIRMWARE/SoC. Update EEPROM (Section D); rely on UART.

3) Journal shape across the gap (Storage=persistent):
   ├─ Log ends MID-LINE / abruptly, no shutdown msgs .. consistent with hard cut
   │        (power loss #1, or hard hang #8/#7) — pair with #1/#2 above.
   ├─ Orderly "systemd-journald watchdog timeout" then
   │        silence ................................... cascade overload → #4 (or severe #2)
   └─ Clean "reboot/shutdown" target reached ......... NOT a freeze — something rebooted it
            cleanly (thermal shutdown #5, or a real reboot) — re-scope.

4) Cross-confirm power even when bits look clean:
   - get_throttled clears bit 0 the instant voltage recovers; a *cut* may leave NO bit set.
   - So "no throttle bits" + "journal ends mid-line" + "manual cycle needed"
       → STILL suspect #1/brownout; the brownout was faster than the next cron sample.
```

**Two cut-vs-orderly tells worth internalizing:**
- **Empty pstore + manual-cycle-required = hard hang** (no panic was ever raised) — the defining fingerprint of #7/#8, and the reason B-3/B-4 exist (to *force* a panic next time).
- **Populated pstore = the kernel was alive enough to panic** — read the backtrace; it almost always names the subsystem (mmc/ext4 → SD; usb → peripheral; net/driver → that driver).

---

## D. Pi 5-Specific Causes & Fixes

The Pi 5 adds a **PMIC** (DA9091-class power management IC) and the **RP1 southbridge** (all USB/Ethernet/GPIO I/O), each a new freeze surface.

1. **Update the EEPROM/bootloader FIRST.** Most "Pi 5 stuck after update / random freeze" reports are resolved by updating the EEPROM via Raspberry Pi Imager before anything else [home-assistant/operating-system #3943 — https://github.com/home-assistant/operating-system/issues/3943]. Do it:
   ```bash
   sudo rpi-eeprom-update -a && sudo reboot
   sudo rpi-eeprom-update            # confirm current after reboot
   ```
   Firmware fixes that matter here [firmware-2712 release notes — https://github.com/raspberrypi/rpi-eeprom/blob/master/firmware-2712/release-notes.md]:
   - **PMIC sequencer status was being overwritten by RTC event status** — fixed (a stability/PMIC-state bug).
   - **PCIE_PWR timing issue (introduced 2025-01-06) when booting from SD/USB** — fixed; relevant if NVMe/PCIe is attached.
   - Bootloader **thermal protections**: fan on >85 °C; **power off >110 °C**; PMIC hard-cut >125 °C — so a Pi 5 thermal event is a *power-off*, not a wedge.

2. **RP1 firmware contact failures.** "Failed to contact RP1 firmware" / "Fatal firmware error" classes occur "randomly after reboot/power-off events and when power is suddenly unplugged and reconnected" — i.e. **power-event triggered** [raspberrypi/linux #6642 — https://github.com/raspberrypi/linux/issues/6642; #6593 — https://github.com/raspberrypi/linux/issues/6593]. If your daily freeze correlates with a power glitch, this overlaps cause #1. A recovery firmware (`rpi-eeprom-recovery-sdram-init-2025-03-08`) addressed some boot-related cases.

3. **Power / `usb_max_current_enable`.** Pi 5 needs **5V/5A 27W PD** for full capability [raspberrypi.com 27W PSU — https://www.raspberrypi.com/products/27w-power-supply/]. Without a detected 5A PD supply, USB is capped to ~600 mA (vs 1.6 A with PD) — under-powered peripherals then brown the rail [thepihut — https://support.thepihut.com/hc/en-us/articles/13852538984221]. `usb_max_current_enable=1` *forces* full USB current and removes the boot prompt **but** if your supply can't actually deliver it, you've just *engineered* cause #1 [instructables — https://www.instructables.com/Raspberry-Pi-5-Power-USB-Current-Management/]. Use a true 5A PD supply rather than forcing the cap.

4. **`POWER_OFF_ON_HALT` & power-button/watchdog semantics.** The kernel's halt/reboot path writes a magic value (e.g. **63 = shutdown**) into the RSTS register and triggers the WDT; firmware reads it and acts per `POWER_OFF_ON_HALT`/`WAKE_ON_GPIO` [forum t=388657 — https://forums.raspberrypi.com/viewtopic.php?t=388657]. On Pi 5, `POWER_OFF_ON_HALT=1` puts the **PMIC in STANDBY** (all rails off) on halt; the dedicated power button boots it; a **5-second hold = hard power-off** [forum t=365889 — https://forums.raspberrypi.com/viewtopic.php?t=365889]. Set via:
   ```bash
   sudo rpi-eeprom-config -e     # add: POWER_OFF_ON_HALT=1
   ```
   ⚠️ **Watchdog interaction (Pi 5):** *disabling* the firmware watchdog can break reboot/shutdown because that path relies on the WDT mechanism [rpi-eeprom #421 — https://github.com/raspberrypi/rpi-eeprom/issues/421]. A firmware fix also stopped `dtoverlay_is_enabled` from *accidentally* arming the watchdog (now requires an `early-watchdog` property) — harmless under Linux (the driver disarms it) but caused spurious reboots on other OSes [firmware-2712 notes]. Net: leave the firmware watchdog mechanism intact; arm the *Linux* watchdog via B-3.

---

## TL;DR action order for your fleet

1. **Update EEPROM on every Pi 5; swap to official PSU + short thick cable on the freezing box; strip non-essential USB.** (Attacks #1/#4/#9 — the top fits.)
2. **Arm the hardware watchdog** (B-3, `RuntimeWatchdogSec=14`) → freezes become auto-reboots, restoring availability *today* and leaving a reset fingerprint.
3. **Make persistent journald + the canary cron** (B-2/B-5) → capture pre-freeze power/PSI/throttle even when nothing else survives. (`get_throttled` is the cheapest highest-value witness.)
4. **Verify pstore support (B-0); if present, add the ramoops overlay + panic sysctls** (B-1/B-4) → convert the next *soft* lockup/hung-task into a recorded stack trace.
5. **Attach UART to the worst box** (B-6) → ground truth for true hard hangs / firmware wedges that bypass everything else.
6. After the next freeze, run the **Section C decision tree** against pstore + journal + canary + UART.

**Verification status of this report:** Findings are **cross-checked across kernel.org docs, Raspberry Pi official docs/datasheets, the raspberrypi GitHub trackers, and corroborating engineering write-ups**, and synthesized — I did not run any of these commands on your hardware, so all setup steps are **BELIEVED (written, not executed here)**; verify each on one canary box before fleet rollout. **Explicitly flagged uncertainties:** (a) whether your specific RPi-OS kernel has `CONFIG_PSTORE` — *must be verified on-box (B-0)*; (b) the exact ramoops `reg` base/size must be chosen per-board to avoid RAM/CMA collisions; (c) hard-lockup/NMI detection is unreliable on ARM, so the hardware watchdog — not `hardlockup_panic` — is the catch for cause #8.

### Sources
- [raspberrypi/linux #7184 — Pi 5 freeze, kernel log flood / UART buffer exhaustion — https://github.com/raspberrypi/linux/issues/7184]
- [Ramoops oops/panic logger (kernel.org) — https://docs.kernel.org/admin-guide/ramoops.html]
- [Softlockup/hardlockup detector (kernel.org) — https://docs.kernel.org/admin-guide/lockup-watchdogs.html]
- [/proc/sys/kernel sysctl docs (kernel.org) — https://www.kernel.org/doc/html/v6.9/admin-guide/sysctl/kernel.html]
- [PSI – Pressure Stall Information (kernel.org) — https://docs.kernel.org/accounting/psi.html]
- [PSI by example (unixism) — https://unixism.net/2019/08/linux-pressure-stall-information-psi-by-example/]
- [Crash dump if system hangs — ramoops limitation (kernel-hardening list) — https://www.spinics.net/lists/kernel-hardening/msg01705.html]
- [RAMoops panic logger on Raspberry Pi (lnxblog) — https://lnxblog.github.io/2020/02/25/ramoops-on-rpi.html]
- [Using ramoops/pstore to capture kernel panics (RPi forum) — https://forums.raspberrypi.com/viewtopic.php?t=199047]
- [ramoops/pstore on Bullseye 64-bit (RPi forum) — https://forums.raspberrypi.com/viewtopic.php?t=326670]
- [raspberrypi/linux #5063 — please enable CONFIG_PSTORE — https://github.com/raspberrypi/linux/issues/5063]
- [systemd #27427 — RuntimeWatchdogSec >15 ignored on Pi — https://github.com/systemd/systemd/issues/27427]
- [Raspberry Pi watchdog made simple (pysselilivet) — https://pysselilivet.blogspot.com/2021/10/raspberry-pi-watchdog-made-simple.html]
- [HW watchdog notebook (bends.se) — https://bends.se/?page=notebook%2Fsbc%2Fraspberry-pi%2Fhw-watchdog]
- [vcgencmd get_throttled decode (LibreELEC) — https://forum.libreelec.tv/thread/17860-how-to-interpret-rpi-vcgencmd-get-throttled/]
- [get_throttled decoder (8086.net) — https://www.8086.net/tools/decode-get_throttled]
- [Pi power supply view / canary script (Paraphraser gist) — https://gist.github.com/Paraphraser/17fb6320d0e896c6446fb886e1207c7e]
- [Undervoltage / low-voltage warning (Pi My Life Up) — https://pimylifeup.com/raspberry-pi-low-voltage-warning/]
- [Pi 5 power requirements & management (circuitlabs) — https://circuitlabs.net/raspberry-pi-5-power-requirements-and-management/]
- [Which PSU for Pi 5 / USB current (The Pi Hut) — https://support.thepihut.com/hc/en-us/articles/13852538984221]
- [Pi 5 USB current management / usb_max_current_enable (Instructables) — https://www.instructables.com/Raspberry-Pi-5-Power-USB-Current-Management/]
- [Raspberry Pi 27W USB-C PSU (raspberrypi.com) — https://www.raspberrypi.com/products/27w-power-supply/]
- [SD corruption symptoms & repair (moldstud) — https://moldstud.com/articles/p-how-to-effectively-solve-sd-card-corruption-problems-on-raspberry-pi]
- [Broken/corrupted Pi SD card (Raymii) — https://raymii.org/s/blog/Broken_Corrupted_Raspberry_Pi_SD_Card.html]
- [journald default volatile (raspberrypi/bookworm-feedback #415) — https://github.com/raspberrypi/bookworm-feedback/issues/415]
- [Trixie journald volatile (RPi forum) — https://forums.raspberrypi.com/viewtopic.php?t=392855]
- [Log storage on SD devices / wear (HostFission) — https://hostfission.com/guides/log-storage-on-devices-with-sd-cards]
- [Reduce SD writes / Pi reliability (Dzombak) — https://www.dzombak.com/blog/2024/04/pi-reliability-reduce-writes-to-your-sd-card/]
- [firmware-2712 release notes — PMIC/watchdog/thermal fixes (rpi-eeprom) — https://github.com/raspberrypi/rpi-eeprom/blob/master/firmware-2712/release-notes.md]
- [raspberrypi/linux #6642 — Pi5 failed to contact RP1 firmware — https://github.com/raspberrypi/linux/issues/6642]
- [raspberrypi/linux #6593 — rpi5 crash loading RP1 firmware — https://github.com/raspberrypi/linux/issues/6593]
- [home-assistant/operating-system #3943 — Pi5 stuck after update / update EEPROM — https://github.com/home-assistant/operating-system/issues/3943]
- [rpi-eeprom #421 — disabling watchdog breaks reboot/shutdown — https://github.com/raspberrypi/rpi-eeprom/issues/421]
- [Pi 5 poweroff / POWER_OFF_ON_HALT behavior (RPi forum) — https://forums.raspberrypi.com/viewtopic.php?t=388657]
- [RPi5 power button semantics (RPi forum) — https://forums.raspberrypi.com/viewtopic.php?t=365889]
- [Reducing Pi 5 power / POWER_OFF_ON_HALT (Jeff Geerling) — https://www.jeffgeerling.com/blog/2023/reducing-raspberry-pi-5s-power-consumption-140x/]
- [UART debugging on Raspberry Pi (It's FOSS) — https://itsfoss.com/use-uart-raspberry-pi/]
- [mini-UART baud / kernel panic with enable_uart (ArchLinuxARM) — https://archlinuxarm.org/forum/viewtopic.php?f=60&t=11734]
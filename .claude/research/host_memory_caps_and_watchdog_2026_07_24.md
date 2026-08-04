# Host memory caps, cgroup verification, and the hardware watchdog (2026-07-24)

> Three lessons from the manager box's reset #8 and the self-inflicted outage
> that followed it 100 minutes later. Each one cost real downtime, and each is
> the kind a session re-derives badly from first principles.
>
> This is the REPO copy of knowledge that previously lived only in the operator's
> memory root — a corpus that exists on exactly one box. The offline oracle's
> eval cases cite this file, so the local tier can answer these questions on any
> box, not just the one that hosts the sessions.

---

## 1. Never pair `MemoryHigh` with systemd-oomd's pressure kill

**Symptom**: systemd-oomd killed an entire desktop session — 49 processes,
including the operator's compositor and a running agent session — while the HOST
still had **7.7 GB available** (50.2% memory used). The box had not run out of
memory in any sense.

**Mechanism, and it is a PAIRING, not an exhaustion:**

- `MemoryHigh=` does not kill anything. It throttles a cgroup **by generating
  reclaim pressure** — that is its entire implementation.
- `ManagedOOMMemoryPressure=kill` fires **on reclaim pressure**.

Put both on the same cgroup tree and they share one implicit signal, so the
throttle **working exactly as designed** is indistinguishable from the emergency
the killer exists for. The journal said:

```
oomd: Killed .../session-2.scope due to memory pressure for /user.slice
      being 73.31% > 50.00% for > 20s with reclaim activity
systemd: killed 49 process(es) in this unit
```

**The fix: keep `MemoryMax`, drop `MemoryHigh`, and disarm the oomd kill policy.**

The two mechanisms have very different blast radii, and that is the whole reason
to prefer one:

| mechanism | what dies |
|---|---|
| `MemoryMax` (kernel cgroup limit) | the single worst **process** inside the slice |
| systemd-oomd `ManagedOOMMemoryPressure=kill` | the whole **scope** — every process in it |

A session scope holds a login shell, its editor, its browser and its agent. Losing
one process in it is survivable; losing the scope is an outage. **Prefer the
smaller blast radius**, and never arm a guard whose blast radius contains the
thing arming it.

Live configuration after the incident:

```
user-1000.slice     memory.high = max          (MemoryHigh removed)
user-1000.slice     memory.max  = 8589934592   (kept: bounds a runaway)
user.slice          ManagedOOMMemoryPressure = auto   (kill policy disarmed)
user@1000.service   MemoryMin = 384M           (kept: protects the user manager)
```

⚠️ `earlyoom` is **not** the answer for this class either: it triggers on free-RAM
and free-swap percentages, and this box died with 2.5 GB free and zram swap at
0 B used, so it would very likely never have fired.

---

## 2. Verify a cap at the CGROUP FILE, never at `systemctl show`

This was bitten in **both directions in a single evening**, so it is not a
theoretical caution:

- After deleting `MemoryHigh` and running `daemon-reload`, `systemctl show`
  reported `MemoryHigh=infinity` **while `memory.high` still read 7 GB**. The
  manager reported its own intent; the kernel held the old value.
- After adding a `MemoryMax` drop-in and running `daemon-reload`, `memory.max`
  stayed at `max` on two boxes — because **a drop-in does not re-apply to an
  already-running slice**. The unit file said one thing, the live cgroup another.

**The cgroup file is the consumer of record.** Read it directly:

```bash
cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.max     # hard limit
cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.high    # throttle point
cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.current # live usage
cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.peak    # high-water mark
```

To push a value onto a **running** slice rather than waiting for it to restart:

```bash
sudo systemctl set-property --runtime user-1000.slice MemoryMax=8G
```

then re-read the cgroup file to confirm it took. A cap you have not read back
from `/sys/fs/cgroup` is BELIEVED, not verified.

⚠️ **A cap can be a silent no-op for a second reason: the memory controller may
not be enabled at all.** Raspberry Pi firmware PREPENDS `cgroup_disable=memory`
to the kernel command line — it is *not* in `/boot/firmware/cmdline.txt`, so
there is nothing to delete. Without the controller, a `MemoryMax=` drop-in does
nothing and the box merely LOOKS protected. Check and cure:

```bash
grep -o 'cgroup[^ ]*' /proc/cmdline          # what the kernel actually got
cat /sys/fs/cgroup/cgroup.controllers        # must list: memory
# cure: append to /boot/firmware/cmdline.txt, then REBOOT
#   psi=1 cgroup_enable=memory cgroup_memory=1
```

The later `cgroup_enable` beats the firmware's earlier `cgroup_disable`. `psi=1`
is the companion win — without it `/proc/pressure/memory` is absent and any
stall-detection probe is one-legged.

### The cap-viability test — do not cap a box that cannot afford one

A `MemoryMax` on the user slice is only safe when a real WINDOW exists between
what that slice rests at and what the box can spare:

```
ceiling = RAM - system.slice RSS
window  = ceiling / user.slice resting usage
```

**Below about 2x there is no safe value.** On the fleet's smallest box (905 MB,
561 MB system.slice, 276 MB resting user slice) the window is **1.25x**: a cap
tight enough to fit would OOM-kill an ordinary `ssh` + `pytest`, and one generous
enough to be safe would exceed what the box can spare and so would not prevent
exhaustion either. Capping it would reproduce the kills-legitimate-work defect on
the least capable machine. Healthy siblings measure 2–4x. **Run this test before
capping any small box**, and leave it uncapped when the window is too narrow.

---

## 3. `system.conf` drop-ins merge in LEXICAL order across ALL directories

**Symptom**: `/etc/systemd/system.conf.d/10-hw-watchdog.conf` set
`RuntimeWatchdogSec=30` and was INERT for days. The file existed, the syntax was
right, and the value never took effect.

**Mechanism**: drop-ins for `system.conf` are merged in **lexical** filename
order across every directory that supplies them, so the vendor's
`/usr/lib/systemd/system.conf.d/40-rpi-enable-watchdog.conf` (`RuntimeWatchdogSec=1m`)
loads **after** `10-hw-watchdog.conf` and wins.

⚠️ **`/etc` beats `/usr/lib` only for the SAME filename.** A different filename in
`/etc` does not outrank a vendor file — it is sorted, not prioritised. To make a
local value win over a vendor `40-` file, name it so it sorts later, e.g.
`50-hw-watchdog.conf`.

**Read the effective value; never read the drop-in:**

```bash
systemctl show -p RuntimeWatchdogUSec --value    # what systemd actually uses
wdctl /dev/watchdog0                             # what the hardware agrees to
cat /sys/class/watchdog/watchdog0/timeout
```

⚠️ **A real change to this setting requires `systemctl daemon-reexec`, not
`daemon-reload`.** `daemon-reload` re-reads unit files; the watchdog deadline is
PID 1's own configuration and only a re-exec picks it up. A change applied with
`daemon-reload` looks applied and is not — the same shape as the `systemctl show`
lie above.

**Hardware ceilings are real and differ across a mixed fleet.** BCM2835-class
timers cap out near 16 s, so those boxes run a 15 s deadline while a Pi 5 /
BCM2712 runs 60 s — meaning a system-wide stall of 15–60 s that the larger box
FORGIVES will reset the smaller ones. Write the achievable value explicitly (15)
rather than requesting `1m` and letting the driver silently clamp: a file
asserting an ignored value is exactly the trap above.

---

## Why the watchdog matters here — the failure is a STALL, not an exhaustion

The reset this arc began with killed the box **with ~2.5 GB still free and no
oom-kill at all**. `RuntimeWatchdogUSec=1min` was armed, memory pressure starved
PID 1 past its ping deadline, and the HARDWARE watchdog reset the machine
*before* memory ran out.

Two consequences worth carrying:

- **The absence of an oom-kill CONFIRMS this class rather than refuting it.** A
  session concluding "no OOM, so it wasn't memory" has the diagnosis backwards.
- **A LEVEL gate is a tombstone, not a warning.** Replaying the real per-minute
  samples, a 20%-availability threshold first fires ~4 s before the reset; the
  RATE leg (slope over a window, with an availability floor) fires ~94 s before.
  **Tune the tombstone, never the early leg.**

Triage recipe after any unexplained reset on this fleet: read the **10 lines
before** the cut in the per-minute memory log, not just the last one — a single
sample cannot show a ramp, and "2.5 GB available" is not health when it is
falling 45 MB/s.

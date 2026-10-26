# systemd unit-state semantics for boot_survival_audit.sh (v257.13 / v252.39)

**Date:** 2026-10-26 · **Author:** Claude Opus 5.5 (research subagent)
**Scope:** What the `systemctl show` properties used by `scripts/boot_survival_audit.sh` (incl. the uncommitted `cond_state`) actually mean, from systemd source + `man/org.freedesktop.systemd1.xml` at the tags the fleet runs. Live corroboration on VolcanoAI (systemd 257.13-1~deb13u1) only; the 252 box was NOT touched (no ssh) — every 252 claim is source-only.

**Pinned sources**
- 257: `github.com/systemd/systemd` tag **v257.13** (`2dfae495`). Debian `257.13-1~deb13u1` `.debian.tar.xz` (deb.debian.org pool) contains **no `debian/patches/` dir at all** → upstream code as-is. VERIFIED-SOURCE (`find deb257 -path '*patch*'` → empty).
- 252: `github.com/systemd/systemd-stable` tag **v252.39** (`050305d0`). Debian `252.39-1~deb12u2` carries 29 patches; the 4 that touch files grepped below (`src/core/main.c` RLIMIT_CORE revert, `src/core/unit.c` `unit_kill_context` revert, `src/core/dbus-manager.c` CVE-2026-29111 cgroup-path validation, fsckd) touch **none** of the functions cited here. VERIFIED-SOURCE (read each patch's hunks).
- Salsa has no `debian/257.13-1_deb13u1` or `debian/252.39-1_deb12u2` tag (API search) — the pool tarballs were used instead.

Citations are `file:line @ tag`. Line numbers are from the shallow clones at those tags.

---

## TL;DR

1. **ConditionResult defaults to `no`** (unit struct is `malloc0`'d, never initialised) and is only written when a start job actually *runs* `unit_start()`. Zero `Condition*=` lines evaluate to `yes` — but only if evaluated. A start job failed with `dependency` (Requires= path) never reaches `unit_start()`, so it stays `no`/`0`. **`ConditionTimestampMonotonic == 0` ⇔ "conditions not evaluated in this unit object's lifetime"** on both versions — but the unit object's lifetime can be shorter than the manager's (garbage collection, see §1.5).
2. All unit/manager `*Monotonic` stamps are **CLOCK_MONOTONIC, one clock for system and user managers** (kernel boot = 0). Manager `UserspaceTimestamp` = that manager process's exec; `FinishTimestamp` = first time its job queue drained. A restarted `user@UID` gets new values; `daemon-reexec` keeps them; `soft-reboot` (254+, so 257 only) resets them.
3. **`WantedBy`/`RequiredBy` are the LIVE inverse dependency graph**, not `[Install]`. A `.wants/` symlink only counts once the *wanting* unit is loaded; `[Install]` is ignored by PID 1. Live proof: `cups.service` has a `printer.target.wants/` symlink, `printer.target` is not loaded, `WantedBy=multi-user.target` only.
4. **`-p A -p B --value` prints in D-Bus vtable order**, never argument order (filter is a set-membership test over the `GetAll` reply).
5. **Dependency-failed start job ⇒ unit stays `inactive`/`dead`, Result unchanged, NOT in `--failed`.**
6. Nothing above changed semantically 252→257. Differences: soft-reboot timestamp reset (257), new service substates (`dead-before-auto-restart`, `failed-before-auto-restart`, `dead-resources-pinned`, `auto-restart-queued`; all map to the same ActiveStates the script reads), `NRestarts` reset bookkeeping.

---

## 1. ConditionResult / ConditionTimestampMonotonic

**1.1 Value before any evaluation = `no`.** VERIFIED-SOURCE.
- Unit objects are allocated with `malloc0(size)` — `src/core/unit.c:102 @ v257.13`, `src/core/unit.c:96 @ v252.39`; `unit_new()` sets a list of defaults and `condition_result` is not among them, so it is `false`.
- D-Bus property is the raw bool: `src/core/dbus-unit.c:920 @ both` (`ConditionResult`, `offsetof(Unit, condition_result)`); timestamp `:922 @ both`.
- Man page: "ConditionTimestamp … the last time the configured conditions of the unit have been checked **or 0 if they have never been checked**. Conditions are checked when a unit is requested to start." — `man/org.freedesktop.systemd1.xml:2627-2630 @ v257.13`, `:2425-2428 @ v252.39`.

**1.2 Only writer: `unit_test_condition()`, called only from `unit_start()`.** VERIFIED-SOURCE.
- Sets the timestamp then the result: `src/core/unit.c:1747-1769 @ v257.13` (`dual_timestamp_now(&u->condition_timestamp)` :1753), `src/core/unit.c:1730-1751 @ v252.39` (`dual_timestamp_get` :1736 — same function, older name).
- Sole call site: `unit_start()` `src/core/unit.c:1907 @ v257.13` / `:1897 @ v252.39`, skipped when the unit is already active (`-EALREADY` returned first) or `activating`.
- `unit_start()` is reached only when a JOB_START runs: `job_perform_on_unit()` `src/core/job.c:876 @ v257.13` / `:825 @ v252.39`.
- Second writer, result only: `ExecCondition=` exit 1–254 sets `condition_result=false` — `src/core/service.c:4092 @ v257.13` / `:3679 @ v252.39`. The timestamp was already set by `unit_test_condition()` in the same `unit_start()`, so `ts>0` still holds.
- Survives `daemon-reload`/`reexec`: serialized as `condition-timestamp` / `condition-result` — `src/core/unit-serialize.c:90,97 @ v257.13`, `:129,133 @ v252.39`.

**1.3 Unit with zero `Condition*=` lines.** VERIFIED-SOURCE + VERIFIED-LIVE.
- Empty list evaluates **true**: "If the condition list is empty, then it is true" — `src/shared/condition.c:1224 @ v257.13`, `:1204 @ v252.39`.
- So `no` on such a unit can only mean "never evaluated". Live:
  `systemctl --user show gcr-ssh-agent.service -p ConditionResult -p ConditionTimestampMonotonic -p ActiveState -p InactiveEnterTimestampMonotonic -p WantedBy` →
  `WantedBy=graphical-session-pre.target` `ActiveState=inactive` `InactiveEnterTimestampMonotonic=0` `ConditionResult=no` `ConditionTimestampMonotonic=0`; `grep -c '^Condition' /usr/lib/systemd/user/gcr-ssh-agent.service` → `0`.

**1.4 Start job cancelled because a dependency failed.** VERIFIED-SOURCE.
- Requires=/BindsTo=/Requisite= failure: `job_finish_and_invalidate()` → `job_fail_dependencies(u, UNIT_ATOM_PROPAGATE_START_FAILURE)` (`src/core/job.c:1047 @ v257.13`, `:994 @ v252.39`) finishes the *dependent's* pending job with `JOB_DEPENDENCY` (`job.c:1001 @ v257.13`, `:948 @ v252.39`). That job never runs, `unit_start()` is never called → **ConditionResult/ConditionTimestamp untouched (`no`/`0` on a fresh object)**. Matches the caller's live measurement.
- `PROPAGATE_START_FAILURE` sits on `UNIT_REQUIRED_BY`, `REQUISITE_OF`, `BOUND_BY` only — **not `WANTED_BY`** (`src/core/unit-dependency-atom.c:43-62 @ v257.13`, `:46-66 @ v252.39`). A Wants= dep failing does not cancel anything.
- Exception: the *runtime* `JOB_DEPENDENCY` path (`unit_verify_deps()` → `-ENOLINK`, `job.c:975 @ v257.13` / `:924 @ v252.39`) runs **after** the condition test (`unit.c:1907` then `:1925 @ v257.13`; `:1897` then `:1915 @ v252.39`). It only covers BindsTo=+After= on an inactive peer (`unit.c:1830-1850 @ v257.13`). In that case ConditionTimestamp **is** set and ConditionResult may be `yes`.
- A Requires= dep that was merely *condition-skipped* does not fail its dependents (`PROPAGATE_INACTIVE_START_AS_FAILURE` only on `REQUISITE_OF`, `unit-dependency-atom.c:49 @ v257.13`).

**1.5 Is `ConditionTimestampMonotonic == 0` a reliable "never evaluated this manager lifetime"?** Holds for the *unit object*, on both versions; **BREAKS for "manager lifetime" when the unit was garbage-collected.**
- GC: an `inactive` unit with no job, no bus tracker, empty cgroup, default `CollectMode=inactive` is collectable (`unit_may_gc()` `src/core/unit.c:420-480 @ v257.13`, `COLLECT_INACTIVE` :458; `:426 @ v252.39`), and is unloaded unless something loaded and alive holds a `UNIT_ATOM_REFERENCED_BY` edge to it (`unit_gc_sweep()` `src/core/manager.c:1415-1446 @ v257.13`; `:1208 @ v252.39`). Every Wants/Requires edge adds a reference (`unit.c:3221 @ v257.13`, `:3139 @ v252.39`).
- `systemctl show` on an unloaded unit makes PID 1 load a **fresh** object: all-zero timestamps, `ConditionResult=no`, `Result=success`, `NRestarts=0`. VERIFIED-LIVE — the drill unit that hit `dependency` at 09:05 today: `journalctl --user -b -g "with result 'dependency'"` → `mf-cudrill-victim.service: Job … failed with result 'dependency'.`; now `systemctl --user show mf-cudrill-victim.service …` → `LoadState=not-found ActiveState=inactive SubState=dead Result=success ConditionResult=no ConditionTimestampMonotonic=0 InactiveEnterTimestampMonotonic=0`, and `list-units --all 'mf-cudrill*'` → empty.
- Practical scope: an *enabled* unit referenced by a loaded, active target is not collectable. An enabled unit whose only wanters are not loaded/inactive-and-unreferenced **can** be collected after it ran and stopped cleanly — its history then reads exactly like "never evaluated, never started". UNKNOWN how often this happens on the fleet (not measured).

## 2. InactiveEnterTimestampMonotonic, FinishTimestampMonotonic, UserspaceTimestampMonotonic

**Clock.** `dual_timestamp_now()` = `now(CLOCK_REALTIME)` + `now(CLOCK_MONOTONIC)` — `src/basic/time-util.c:67-74 @ v257.13` (252: `dual_timestamp_get`, same pair). CLOCK_MONOTONIC is system-wide, starts at kernel boot, is not stepped by NTP/fake-hwclock (Linux clock semantics; systemd's man page: "as monotonic time begins with the kernel startup, the KernelTimestampMonotonic timestamp will always be 0" — `org.freedesktop.systemd1.xml:1766-1767 @ v257.13`). **System and user managers use the same clock → their values are directly comparable.** VERIFIED-LIVE: `KernelTimestampMonotonic=0` on both `systemctl show` and `systemctl --user show`; user `UserspaceTimestampMonotonic=11427734` ≈ `systemctl show user@1000.service -p ExecMainStartTimestampMonotonic` → `11307789` (120 ms apart: same clock).

**InactiveEnterTimestamp (unit).** Set in `unit_notify()` on any transition from not-(inactive|failed) to (inactive|failed), skipped while the manager is reloading — `src/core/unit.c:2632-2638 @ v257.13`, `:2668-2674 @ v252.39`. Man: "0 in case such a transition has not yet been recorded on this boot" — `org.freedesktop.systemd1.xml:2580-2592 @ v257.13`, `:2380-2390 @ v252.39`. VERIFIED-SOURCE. Consequences:
- `failed → inactive` (reset-failed) and `inactive → failed` do not update it (both sides are inactive-or-failed).
- Condition-skipped and dependency-cancelled units never leave inactive → stays 0.
- Each auto-restart cycle passes through an inactive/failed state on both versions (252: `service_set_state(s, end_state)` then `SERVICE_AUTO_RESTART`, `service_enter_dead` region of `src/core/service.c @ v252.39`; 257: `*_BEFORE_AUTO_RESTART` states mapped to `UNIT_INACTIVE`/`UNIT_FAILED`, `service.c:2074+` and translation table), so it records the **last** death.
- Serialized across reload/reexec (`unit-serialize.c:88 @ v257.13`, `:127 @ v252.39`); lost on GC (§1.5).

**UserspaceTimestamp (manager).** Taken at the top of `main()` of *that* manager process — `src/core/main.c:3061 @ v257.13`, `:2721 @ v252.39`; stored `main.c:3334 @ v257.13` / `:2976 @ v252.39`. For the system manager it is PID 1's start; for a user manager it is the start of that `systemd --user` process. VERIFIED-SOURCE.

**FinishTimestamp (manager).** Set once, in `manager_check_finished()` the first time the job queue is empty and `MANAGER_IS_FINISHED` is still false — `src/core/manager.c:4089 @ v257.13`, `:3685 @ v252.39`; `MANAGER_IS_FINISHED` = finish stamp set (`manager.h:531 @ v257.13`, `:479 @ v252.39`). Man: "when all queued startup jobs finished execution". It is **not** updated by later job activity. VERIFIED-SOURCE. Live: system `UserspaceTimestampMonotonic=2292042`, `FinishTimestampMonotonic=28608097`; user `11427734` / `35301385`, `Linger=yes`.

**Restarts / linger.**
- `daemon-reexec` and reload: manager timestamps serialized and restored (`src/core/manager-serialize.c:119-126, 540-550 @ v257.13`; `:126-133, 570-580 @ v252.39`). VERIFIED-SOURCE.
- `soft-reboot` (exists 254+ — `NEWS:2741 @ v257.13`): `Userspace`/`Finish` are deliberately not serialized when the objective is soft-reboot (`manager-serialize.c:28-38 @ v257.13`), so they restart from the soft-reboot while CLOCK_MONOTONIC keeps counting from the kernel boot. Not applicable to 252. VERIFIED-SOURCE.
- User manager restart (`systemctl restart user@UID`, or linger=no + last session logout + later login): new process → new `Userspace` and `Finish`, and every unit object is new (all unit stamps 0 until they transition). Linger=yes starts `user@UID` during boot (live: user Userspace 11.4 s < system Finish 28.6 s). Behaviour of `user@` start/stop under linger lives in logind, not checked here — UNKNOWN beyond "new process ⇒ new stamps" (VERIFIED-SOURCE via main.c above).

**Comparable?** Yes on the clock: unit `InactiveEnterTimestampMonotonic` and *any* manager's `FinishTimestampMonotonic` are both CLOCK_MONOTONIC µs since kernel boot. Semantically, a user unit's stamp is only meaningful against its own manager's `Userspace` (it cannot predate it), which is what the script's `late` branch does.

## 3. WantedBy / RequiredBy as `systemctl show` properties

**Live graph, not `[Install]`.** VERIFIED-SOURCE.
- PID 1 ignores `[Install]`: `Install.WantedBy, NULL, 0, 0` (and `RequiredBy`, `Alias`, `Also`) — `src/core/load-fragment-gperf.gperf.in:597-601 @ v257.13`, `:560-563 @ v252.39`.
- The property reads the unit's in-memory dependency hashmap: `property_get_dependencies()` → `unit_get_dependencies(u, d)` — `src/core/dbus-unit.c:161-183 @ v257.13`, `:148-170 @ v252.39`; registered `RequiredBy` :855 / `WantedBy` :857 @ v257.13 (`:858/:860 @ v252.39`).
- Entries come from the inverse table in `unit_add_dependency()` (`[UNIT_WANTS] = UNIT_WANTED_BY`, `[UNIT_REQUIRES] = UNIT_REQUIRED_BY` — `src/core/unit.c:3055-3057 @ v257.13`, `:3034-3036 @ v252.39`), fed by *every* source of Wants/Requires on a **loaded** unit: fragment `Wants=`/`Requires=`, `.wants/`/`.requires/` (and 257-only `.upholds/`) symlinks processed when the **wanting** unit loads (`src/core/load-dropin.c:14-80, 92 @ both`), udev `SYSTEMD_WANTS`/`SYSTEMD_USER_WANTS` on device units (`src/core/device.c:536,574 @ v257.13`, `:544,582 @ v252.39`), implicit/default deps.
- Man page wording "as configured in the unit file or determined automatically" (`org.freedesktop.systemd1.xml:2516-2524 @ v257.13`, `:2314-2322 @ v252.39`) does not say "of loaded units"; the code does.

**Aliases / Also=.** An alias resolves to the same Unit object, so a `.wants` entry under an alias name lands on the real unit. `Also=` is install-time only (ignored by PID 1, above). VERIFIED-LIVE: `lightdm.service` has no `WantedBy=` in `[Install]` yet live `WantedBy=graphical.target` (via `/etc/systemd/system/display-manager.service -> lightdm.service`; `systemctl show graphical.target -p Wants` contains `lightdm.service`).

**Enabled into a target that is not loaded.** That target contributes **nothing**. VERIFIED-LIVE:
`ls -la /etc/systemd/system/*.wants/cups.service` → symlinks in `multi-user.target.wants/` **and** `printer.target.wants/`; `systemctl list-units --all printer.target` → no row (not loaded); `systemctl show cups.service -p WantedBy` → `WantedBy=multi-user.target`.
Also non-target wanters appear (live: `avahi-daemon.service` WantedBy+RequiredBy includes `cups-browsed.service`, `meshtasticd.service` includes `meshforge-map.service`).

## 4. Output order of `systemctl show -p A -p B --value`

**D-Bus `GetAll` reply order = interface vtable order; `-p` is a filter, not a projection.** VERIFIED-SOURCE + VERIFIED-LIVE.
- `show_one()` fetches with `bus_map_all_properties()` (`src/systemctl/systemctl-show.c:2216 @ v257.13`), which calls `GetAll` with interface `""` (all interfaces) — `src/shared/bus-map-properties.c:233-236 @ v257.13`, `:231-234 @ v252.39`.
- It then walks the reply in order and prints a property iff `strv_contains(filter, name)` — `bus_message_print_all_properties()` `src/shared/bus-print-properties.c:339-374 @ v257.13`, `:328-361 @ v252.39`; called at `systemctl-show.c:2260 @ v257.13`, `:2058 @ v252.39`.
- Order within `org.freedesktop.systemd1.Unit` is the vtable: `Id` :846, `ActiveState` :883, `SubState` :885, `InactiveEnterTimestamp` :895, `ConditionResult` :920 (`src/core/dbus-unit.c @ v257.13`; `:849/:885/:887/:897/:920 @ v252.39`). Same relative order on both tags.
- Live: `systemctl --user show gcr-ssh-agent.service -p ConditionResult -p ActiveState -p Id --value` → `gcr-ssh-agent.service` / `inactive` / `no` (reverse of argument order). A property named twice prints once; a non-existent one prints nothing (debug log only, `systemctl-show.c:2264-2266 @ v257.13`). **Never parse multi-`-p --value` output positionally**; the script uses one `-p` per call — safe.

## 5. Start job fails with result `dependency`

VERIFIED-SOURCE (both tags), consistent with caller's live reading.
- `job_finish_and_invalidate()` on `JOB_DEPENDENCY` uninstalls the job, logs "Job … failed with result 'dependency'", and fires `OnFailure=` manually **because "a failed job might not cause a unit state change"** (comment wording @ v257.13) — `src/core/job.c:1074-1086 @ v257.13`, `:1020-1032 @ v252.39` (same logic, different comment). It never calls into the unit type's state machine.
- So the unit keeps its prior state: **ActiveState=inactive, SubState=dead** (or `failed` if it already was), **`Result` unchanged** (reset to `success` only inside `service_start()`, `src/core/service.c:2881 @ v257.13`, which is not reached).
- `--failed` = `--state=failed` (`src/systemctl/systemctl.c:754 @ v257.13`, `:685 @ v252.39`); the manager matches load/active/sub state strings (`unit_passes_filter()` `src/core/unit.c:6635-6645 @ v257.13`; inline `src/core/dbus-manager.c:1187-1190 @ v252.39`). `inactive`/`dead` ≠ `failed` ⇒ **not listed**. `ListUnits*` only enumerates loaded units, so a dependency victim that was GC'd (§1.5) is absent from every `list-units` view.
- The audit sees such a unit only via the enabled-but-inactive loop, where `cond_state` reads `no`+`0` → not `refused` → falls to the WantedBy check.

## 6. 252 → 257 differences relevant here

| Area | 252.39 | 257.13 | Evidence |
|---|---|---|---|
| ConditionResult/Timestamp semantics | as §1 | identical | same code paths, line refs above |
| `show -p` ordering | vtable order | identical | §4 |
| WantedBy source | `.wants`, `.requires` | adds `.upholds` (`UpheldBy`) | `load-dropin.c:91-100` both tags |
| Manager timestamps | reexec-preserved | also; **soft-reboot resets Userspace/Finish** | `manager-serialize.c:28-38 @ v257.13`; soft-reboot `NEWS:2741 @ v257.13` (254) |
| Service substates | `dead`, `failed`, `auto-restart` | adds `dead-before-auto-restart`, `failed-before-auto-restart`, `dead-resources-pinned`, `auto-restart-queued` (ActiveState inactive/failed/inactive/activating) | `state_translation_table` `src/core/service.c @ v257.13` |
| NRestarts reset | on next start after a non-restart death (`flush_n_restarts`, `service.c:1869,2569-2570 @ v252.39`) and reset-failed (`:4501`) | on any non-auto-restart start (`service.c:2888-2889 @ v257.13`) and reset-failed (`:5143`) | — net effect same for the audit: counter survives until the next manual start / reset-failed |
| Debian patches | none touching cited code | no patches at all | header |

No NEWS entry 253–257 changes ConditionResult, WantedBy, `show` ordering, or InactiveEnter semantics (grep of `NEWS @ v257.13` lines 1–3999 for Condition/WantedBy/`systemctl show`/`--value`/Timestamp: only soft-reboot and unrelated hits). VERIFIED-SOURCE for the grep; absence in NEWS is not proof of absence in code — the code reads above are the evidence.

---

## What this means for boot_survival_audit.sh

| Assumption | Verdict | Evidence / note |
|---|---|---|
| **Transient=yes ⇒ created after boot by `systemd-run`, not a boot casualty** | HOLDS (both) | Set only in `unit_make_transient()` `unit.c:4737 @ v257.13` / `:4503 @ v252.39`; man: "created as a transient unit (i.e. via StartTransientUnit())". Caveat: a transient unit can be created *during* boot by another unit's ExecStart; it is still not "enabled", so exempting it is correct. |
| **Type ∈ {oneshot, dbus, idle} exempt** | HOLDS (both) | `Type` is a static service property; no version difference found. |
| **TriggeredBy non-empty ⇒ on-demand** | HOLDS, with the §3 caveat | Live graph: socket/path/timer add `UNIT_TRIGGERS` when *they* load (`socket.c:340`, `path.c:367`, `timer.c:127 @ v257.13`; `339/366/131 @ v252.39`). If the trigger unit never loaded, TriggeredBy is empty and the service is judged as long-running. Live: `cups.service TriggeredBy=cups.socket cups.path`. |
| **`ConditionResult=no` + `ConditionTimestampMonotonic>0` ⇒ "a condition was evaluated and refused"** | HOLDS (both) | §1.2. Also true for `ExecCondition=` refusals. |
| **`ConditionResult=no` + `ts=0` ⇒ "never evaluated"** | HOLDS for this unit object; **BREAKS** as "never evaluated since boot" if the unit was GC'd | §1.5 — and a GC'd unit also reads `InactiveEnter=0`, `NRestarts=0`. Only reachable for units no loaded unit references, i.e. exactly the not-pulled-in class, so the `notpulled` label is still right in practice. |
| **Dependency-cancelled unit is caught** | HOLDS via the enabled-but-inactive path, not via `--failed` | §5: stays inactive/dead, never `failed`. `cond_state` → `no`/`0` → WantedBy check → "judge (wanted by X, active)" if its target is up. `when_down` then says "never started this boot" — true (it never ran) but the cause (dependency) is not named; the journal line "failed with result 'dependency'" is the only witness. |
| **WantedBy/RequiredBy = who would pull it in** | **DIFFERS from `[Install]`** — and that is what the script needs | §3: live loaded wanters only. A unit enabled only into an unloaded target reads "wanted by nothing" (cups→printer.target live proof). It also lists non-target wanters (services); `is-active` on those is a fine test. Misses wanters that would pull it in via `Requisite=`/`BindsTo=`/`Upholds=` (`BoundBy`, `UpheldBy` not read) — UNKNOWN whether any fleet unit depends that way. |
| **InactiveEnterTimestampMonotonic vs FinishTimestampMonotonic** | HOLDS (same CLOCK_MONOTONIC, both versions) | §2. `0` means "has not left an active state in this object's life" — never started, condition-skipped, dependency-cancelled, *or* GC'd. `failed→inactive` via reset-failed keeps the death time. |
| **UserspaceTimestampMonotonic of the user manager = when that manager (re)started** | HOLDS | `main.c:3061 @ v257.13` / `:2721 @ v252.39`. Directly comparable with system `FinishTimestampMonotonic` (same clock, VERIFIED-LIVE 11.43 s vs 28.61 s). |
| **`FinishTimestampMonotonic==0` ⇒ manager still booting** | HOLDS | Set once when the queue first drains (§2). On 257, after a `soft-reboot` both Finish and Userspace describe the soft-reboot, not the kernel boot — the script would call that "the boot", which is arguably right. Fleet use of soft-reboot: UNKNOWN. |
| **NRestarts = restarts of the current run** | HOLDS (both, different bookkeeping) | Persists until the next non-auto-restart start or reset-failed (§6 table). After `reset-failed` it reads 0 even though the unit restarted. |
| **`-p X --value`, one property per call** | HOLDS | Multi-`-p` would be vtable-ordered (§4); the script never does that. |

**252 live check (run by the parent session on moc4, systemd 252.39-1~deb12u2, read-only, 2026-10-26):** VERIFIED-LIVE for §1, §3, §4 on 252 —
- unevaluated: `hciuart.service` → `ConditionResult=no ConditionTimestampMonotonic=0 InactiveEnterTimestampMonotonic=0 ActiveState=inactive WantedBy=dev-serial1.device` (a device that never appears on that box).
- ordering: `systemctl show hciuart.service -p ConditionResult -p ActiveState -p Id --value` → `hciuart.service inactive no` (vtable order, same as 257).
- live graph: `cups.service` symlinks in `multi-user.target.wants/` AND `printer.target.wants/`; `-p WantedBy` → `multi-user.target` only (printer.target unloaded).

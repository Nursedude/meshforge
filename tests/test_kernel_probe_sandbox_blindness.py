"""probe_kernel_reboot_pending must stay OBSERVABLE under the unit's own sandbox.

Born 2026-07-27. moc5 reported ``kernel_reboot_pending(degraded)`` for a
debounce streak of 347 ticks AFTER the condition was cured (rebooted, flag
gone, running kernel == newest installed). Nothing was broken:

  * the unit sets ``ProtectKernelModules=yes``, which MASKS ``/lib/modules``
    and ``/usr/lib/modules`` even though they are world-readable (proven in
    the service sandbox: 0 entries visible vs 36 unsandboxed; neither
    ``ReadOnlyPaths`` nor ``BindReadOnlyPaths`` restores them),
  * so leg B reported ``indeterminate`` every tick,
  * and ``indeterminate`` means "not observed", so ``watchdog_tracker`` held
    the last-known verdict forever with ``extra.unobserved_hold``.

Three correct components composing into a permanently wrong answer. A hold
whose observer can never see again is an unfalsifiable assertion.

These tests pin the cure: fall back to ``/boot/vmlinuz-<release>``, which
survives that sandbox and carries identical release strings. They fail
against the pre-fix probe (which had no boot fallback and no kernel_source).
"""

import os

import pytest

from utils.watchdog_probes_env import probe_kernel_reboot_pending


def _mk_modules(root, names):
    d = root / "modules"
    d.mkdir()
    for n in names:
        (d / n).mkdir()
    return str(d)


def _mk_boot(root, releases, extra_files=()):
    d = root / "boot"
    d.mkdir()
    for r in releases:
        (d / f"vmlinuz-{r}").write_text("")
    for f in extra_files:
        (d / f).write_text("")
    return str(d)


class TestBootFallbackWhenModulesMasked:
    """The sandbox case: modules dir unreadable, /boot still visible."""

    def test_masked_modules_falls_back_to_boot_and_stays_clean(self, tmp_path):
        """THE moc5 CASE: running == newest, so the probe must go SILENT.

        Pre-fix this returned None too — but via the *indeterminate* path,
        which the tracker converts into a permanent held signal. The cure is
        that leg B is now genuinely OBSERVED, so the disposition is 'clean'.
        """
        boot = _mk_boot(tmp_path, ["6.8.0-1057-raspi", "6.8.0-1060-raspi"])
        sig = probe_kernel_reboot_pending(
            modules_dir=str(tmp_path / "does-not-exist"),  # masked
            boot_dir=boot,
            reboot_required_path=str(tmp_path / "no-flag"),
            running_release="6.8.0-1060-raspi",
            state_path=str(tmp_path / "streak.json"),
        )
        assert sig is None, "running == newest same-flavor must not fire"

    def test_masked_modules_still_detects_a_real_straggler_via_boot(self, tmp_path):
        """The probe must not go blind-silent: /boot carries a NEWER kernel."""
        boot = _mk_boot(tmp_path, ["6.8.0-1057-raspi", "6.8.0-1060-raspi"])
        st = str(tmp_path / "streak.json")
        kw = dict(
            modules_dir=str(tmp_path / "does-not-exist"),
            boot_dir=boot,
            reboot_required_path=str(tmp_path / "no-flag"),
            running_release="6.8.0-1057-raspi",  # older than installed
            state_path=st,
        )
        assert probe_kernel_reboot_pending(**kw) is None, "2-tick debounce"
        sig = probe_kernel_reboot_pending(**kw)
        assert sig is not None, "a real straggler must fire via the boot leg"
        assert sig.severity == "degraded"
        assert sig.extra["newest_installed"] == "6.8.0-1060-raspi"
        assert sig.extra["kernel_source"] == "boot"
        assert boot in sig.detail, "detail must name the source actually used"

    def test_modules_dir_wins_when_readable(self, tmp_path):
        """Unsandboxed boxes keep the original source; boot is only a fallback."""
        mods = _mk_modules(tmp_path, ["6.12.75+rpt-rpi-2712"])
        boot = _mk_boot(tmp_path, ["9.9.9+rpt-rpi-2712"])  # would fire if used
        sig = probe_kernel_reboot_pending(
            modules_dir=mods,
            boot_dir=boot,
            reboot_required_path=str(tmp_path / "no-flag"),
            running_release="6.12.75+rpt-rpi-2712",
            state_path=str(tmp_path / "streak.json"),
        )
        assert sig is None, "modules dir is authoritative when readable"


class TestFlavorDisciplineSurvivesTheFallback:
    """rpi-v8 vs rpi-2712 must never be compared — /boot holds BOTH."""

    def test_other_flavor_in_boot_is_not_a_straggler(self, tmp_path):
        boot = _mk_boot(tmp_path, [
            "6.18.34+rpt-rpi-2712",   # running, this flavor
            "6.18.99+rpt-rpi-v8",     # NEWER but the other flavor
        ])
        sig = probe_kernel_reboot_pending(
            modules_dir=str(tmp_path / "masked"),
            boot_dir=boot,
            reboot_required_path=str(tmp_path / "no-flag"),
            running_release="6.18.34+rpt-rpi-2712",
            state_path=str(tmp_path / "streak.json"),
        )
        assert sig is None, "a newer OTHER-flavor kernel is not a straggler"


class TestBothSourcesBlindStaysHonest:
    """Blind on both sources must stay indeterminate — never a false clean."""

    def test_both_blind_and_no_flag_does_not_fire(self, tmp_path):
        sig = probe_kernel_reboot_pending(
            modules_dir=str(tmp_path / "masked"),
            boot_dir=str(tmp_path / "also-masked"),
            reboot_required_path=str(tmp_path / "no-flag"),
            running_release="6.8.0-1060-raspi",
            state_path=str(tmp_path / "streak.json"),
        )
        assert sig is None

    def test_both_blind_but_flag_present_still_fires_via_leg_a(self, tmp_path):
        """Leg A is independent — and this is the NameError path.

        With an unparseable release, leg B is skipped entirely, so
        ``kernel_source`` is never assigned inside the branch. The Signal is
        still reachable through the reboot-required flag and reads it — an
        unbound local would raise NameError here.
        """
        flag = tmp_path / "reboot-required"
        flag.write_text("*** System restart required ***\n")
        st = str(tmp_path / "streak.json")
        kw = dict(
            modules_dir=str(tmp_path / "masked"),
            boot_dir=str(tmp_path / "also-masked"),
            reboot_required_path=str(flag),
            running_release="not-a-parseable-release",
            state_path=st,
        )
        assert probe_kernel_reboot_pending(**kw) is None, "2-tick debounce"
        sig = probe_kernel_reboot_pending(**kw)
        assert sig is not None, "the flag leg must fire independently"
        assert sig.extra["reboot_required_file"] is True
        assert sig.extra["kernel_source"] is None, "blind on both → no source"


class TestRealBoxShape:
    """Guard the assumption the fallback rests on, on THIS box."""

    @pytest.mark.skipif(not os.path.isdir("/boot"), reason="no /boot here")
    def test_boot_release_names_parse_like_uname(self):
        """/boot/vmlinuz-<release> must yield strings shaped like uname -r.

        If a distro ever names these differently the fallback silently finds
        nothing, so this fails loudly rather than going quietly blind.
        """
        from utils.watchdog_probes_env import _parse_kernel_release
        names = [n[len("vmlinuz-"):] for n in os.listdir("/boot")
                 if n.startswith("vmlinuz-") and n != "vmlinuz-"]
        if not names:
            pytest.skip("no vmlinuz-* on this box")
        parsed = [n for n in names if _parse_kernel_release(n) is not None]
        assert parsed, f"no /boot kernel name parsed as a release: {names}"

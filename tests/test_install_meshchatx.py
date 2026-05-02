"""Regression guards for ``scripts/install_meshchatx.sh``.

Mirrors the shape of ``test_migrate_map_service.py`` — script-level
contract tests that verify (a) bash-syntax cleanliness, (b) the
canonical structure (modes, placeholders, refuses-loud preconditions),
and (c) the wheel-fetch JSON parser logic in isolation.

Doesn't actually run the installer — that path requires network +
sudo + a real pipx, which belong in field-validation, not unit tests.

Run: python3 -m pytest tests/test_install_meshchatx.py -v
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install_meshchatx.sh"
WRAPPER_TEMPLATE = REPO_ROOT / "templates" / "python" / "meshchatx_wrapper.sh"
UNIT_TEMPLATE = REPO_ROOT / "templates" / "systemd" / "meshchatx-user.service"


# ─────────────────────────────────────────────────────────────────
# (a) Syntax + script-shape contract
# ─────────────────────────────────────────────────────────────────


class TestScriptStructure:
    """The installer's structure is the SLA — these assertions guard it."""

    def test_installer_exists(self):
        assert INSTALLER.is_file()
        # Must be executable so operators can `bash …` or `./… ` it.
        assert INSTALLER.stat().st_mode & 0o111, "installer not executable"

    def test_wrapper_template_exists(self):
        assert WRAPPER_TEMPLATE.is_file()
        # Wrapper itself must also be executable — install -m 0755
        # copies the bit through, but we want template-side parity.
        assert WRAPPER_TEMPLATE.stat().st_mode & 0o111, \
            "wrapper template not executable"

    def test_unit_template_exists(self):
        assert UNIT_TEMPLATE.is_file()

    def test_bash_syntax_clean(self):
        proc = subprocess.run(
            ["bash", "-n", str(INSTALLER)],
            capture_output=True, text=True, timeout=10,
        )
        assert proc.returncode == 0, (
            f"bash -n failed:\nstderr:\n{proc.stderr}"
        )

    def test_wrapper_bash_syntax_clean(self):
        proc = subprocess.run(
            ["bash", "-n", str(WRAPPER_TEMPLATE)],
            capture_output=True, text=True, timeout=10,
        )
        assert proc.returncode == 0, (
            f"wrapper bash -n failed:\nstderr:\n{proc.stderr}"
        )

    def test_set_minus_e_present(self):
        # The installer MUST exit on first error — losing track of a
        # failed pipx install in the middle would silently corrupt the
        # box.
        text = INSTALLER.read_text()
        assert re.search(r"^set -euo pipefail", text, re.MULTILINE), (
            "installer missing 'set -euo pipefail' — partial-failure "
            "guard"
        )

    def test_refuses_root_install(self):
        # The canonical installer must refuse to install for root —
        # mirrors NomadNet's invariant. Identity in /root would block
        # the user-scope systemd unit and split state between accounts.
        text = INSTALLER.read_text()
        assert "refuses to install MeshChatX for root" in text


class TestModesContract:
    """The 4 install modes are operator-facing API — must not regress."""

    @pytest.mark.parametrize("mode", ["check", "refresh", "reinstall"])
    def test_mode_arg_recognized(self, mode):
        text = INSTALLER.read_text()
        # Each --mode flag must appear in the argparse case statement.
        assert f"--{mode})" in text, f"--{mode} not handled"

    def test_wipe_identity_only_with_reinstall(self):
        text = INSTALLER.read_text()
        # The validation MUST exist to avoid silent identity-wipe.
        assert "--wipe-identity is only valid with --reinstall" in text

    def test_check_mode_is_read_only(self):
        # --check must not write anything — callers script it for
        # automation.
        text = INSTALLER.read_text()
        # The do_check function should not call write_wrapper / write_unit.
        check_block = re.search(
            r"do_check\(\)\s*\{(.+?)\n\}", text, re.DOTALL,
        )
        assert check_block, "do_check function not found"
        body = check_block.group(1)
        assert "write_wrapper" not in body
        assert "write_unit" not in body
        assert "pipx install" not in body
        assert "pipx_install_meshchatx" not in body


class TestRPCKeyRefusesLoud:
    """Issue #41 guard — the installer + wrapper must both refuse-loud."""

    def test_installer_checks_rpc_key_pinned(self):
        text = INSTALLER.read_text()
        assert "rpc_key_pinned" in text, (
            "installer missing rpc_key precondition check (Issue #41)"
        )
        assert "rns_alignment.py" in text, (
            "installer should point operator at the alignment script"
        )

    def test_wrapper_checks_rpc_key_pinned(self):
        text = WRAPPER_TEMPLATE.read_text()
        assert "rpc_key_pinned" in text, (
            "wrapper missing rpc_key precondition check"
        )

    def test_wrapper_exits_87_on_mismatch(self):
        # Sentinel matches NomadNet's wrapper for fleet consistency.
        text = WRAPPER_TEMPLATE.read_text()
        assert "EXIT_AUTH_MISMATCH=87" in text
        assert 'exit "${EXIT_AUTH_MISMATCH}"' in text or \
               "exit ${EXIT_AUTH_MISMATCH}" in text, (
            "wrapper does not exit 87 on rpc_key mismatch"
        )


class TestUnitTemplate:
    """The systemd unit must mirror NomadNet's resilience knobs."""

    def test_placeholder_present(self):
        # The installer substitutes this at install time. Drift here
        # silently produces a unit that won't start.
        text = UNIT_TEMPLATE.read_text()
        assert "__MESHCHATX_EXEC__" in text

    def test_no_tmux_machinery(self):
        # MeshChatX is a daemon, not a TUI. tmux-related directives
        # in the systemd body would point at a NomadNet-shaped
        # artefact that doesn't apply here. Comments may compare and
        # contrast against NomadNet, so we filter those out before
        # checking — the SLA is "no tmux in the actual systemd
        # directives", not "no tmux in the doc-block".
        directive_text = "\n".join(
            line for line in UNIT_TEMPLATE.read_text().splitlines()
            if line and not line.lstrip().startswith("#")
        )
        assert "tmux" not in directive_text.lower(), (
            "MeshChatX unit must not contain tmux directives — it is a "
            "web daemon, not a TUI:\n" + directive_text
        )

    def test_type_simple(self):
        # Type=simple matches "long-lived process is the daemon" model.
        # NomadNet uses Type=forking because tmux daemonises away from
        # the tmux command — that doesn't apply here.
        text = UNIT_TEMPLATE.read_text()
        assert re.search(r"^Type=simple\s*$", text, re.MULTILINE), (
            "MeshChatX unit must be Type=simple"
        )

    def test_start_limit_burst_5(self):
        # Issue #46 guard: caps a refuse-loud restart loop at 5 hits in
        # 300s, then parks the unit. Without this the journal carries
        # 392 silent retries and the operator sees nothing useful.
        text = UNIT_TEMPLATE.read_text()
        assert "StartLimitIntervalSec=300" in text
        assert "StartLimitBurst=5" in text

    def test_after_rnsd_no_requires(self):
        # rnsd is optional — the wrapper tolerates transient
        # unavailability. After= ordering is fine; Requires= would
        # block startup and isn't appropriate.
        text = UNIT_TEMPLATE.read_text()
        assert "After=" in text
        assert "rnsd.service" in text
        # No hard Requires= on rnsd
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Requires=") or \
               stripped.startswith("BindsTo="):
                assert "rnsd" not in stripped, (
                    f"unit must not Require= rnsd: {stripped}"
                )

    def test_journal_logging(self):
        text = UNIT_TEMPLATE.read_text()
        assert "StandardOutput=journal" in text
        assert "StandardError=journal" in text
        assert "SyslogIdentifier=meshchatx" in text


# ─────────────────────────────────────────────────────────────────
# (b) Wheel-fetch JSON parser — extracted and exercised in isolation.
# Live API calls are a separate concern; here we feed canned payloads.
# ─────────────────────────────────────────────────────────────────


class TestWheelFetchParser:
    """The inline python3 -c parser inside fetch_latest_wheel().

    We extract its logic into a parametrizable shell invocation so the
    fixture runs without curl. Drift in the parser silently breaks the
    installer.
    """

    PARSER_SCRIPT = textwrap.dedent('''\
        import json, sys
        try:
            data = json.load(sys.stdin)
        except Exception as exc:
            sys.stderr.write(f"ERROR: failed to parse gitea response: {exc}\\n")
            sys.exit(1)
        tag = data.get("tag_name") or ""
        url = ""
        for asset in data.get("assets") or []:
            name = asset.get("name") or ""
            if name.endswith(".whl") and "py3-none-any" in name:
                url = asset.get("browser_download_url") or ""
                break
        if not tag or not url:
            sys.stderr.write("ERROR: no wheel asset found in latest release\\n")
            sys.exit(1)
        print(f"{tag}|{url}")
    ''')

    def _run_parser(self, payload):
        proc = subprocess.run(
            ["python3", "-c", self.PARSER_SCRIPT],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=5,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def test_extracts_wheel_url_from_real_shape(self):
        # Real gitea shape, captured 2026-05-02 — the SLA we promise to
        # the installer. If MeshChatX upstream shifts to per-arch wheels
        # or renames assets, this test is the canary.
        payload = {
            "tag_name": "v4.5.1",
            "assets": [
                {"name": "app-full-release.apk",
                 "browser_download_url": "https://x/apk"},
                {"name": "reticulum_meshchatx-4.5.1-py3-none-any.whl",
                 "browser_download_url": "https://x/whl"},
                {"name": "ReticulumMeshChatX-v4.5.1-linux-arm64.deb",
                 "browser_download_url": "https://x/deb"},
            ],
        }
        rc, out, err = self._run_parser(payload)
        assert rc == 0, err
        tag, url = out.split("|", 1)
        assert tag == "v4.5.1"
        assert url == "https://x/whl"

    def test_no_wheel_asset_fails_loudly(self):
        # Some future release might omit the wheel — installer MUST
        # abort cleanly rather than fall back to an arch-specific
        # build silently.
        payload = {
            "tag_name": "v5.0.0",
            "assets": [
                {"name": "ReticulumMeshChatX-v5.0.0-linux-arm64.deb",
                 "browser_download_url": "https://x/deb"},
            ],
        }
        rc, out, err = self._run_parser(payload)
        assert rc != 0
        assert "no wheel asset" in err

    def test_skips_wheels_with_arch_specific_tag(self):
        # If a build ever produces e.g. a 'cp311-linux_aarch64' wheel,
        # we must NOT pick it — pipx install of an arch-specific wheel
        # on a different Pi family silently fails at runtime.
        payload = {
            "tag_name": "v9.9.9",
            "assets": [
                {"name": "reticulum_meshchatx-9.9.9-cp311-cp311-"
                         "manylinux_2_17_aarch64.whl",
                 "browser_download_url": "https://x/arch_whl"},
                {"name": "reticulum_meshchatx-9.9.9-py3-none-any.whl",
                 "browser_download_url": "https://x/pure_whl"},
            ],
        }
        rc, out, err = self._run_parser(payload)
        assert rc == 0, err
        _, url = out.split("|", 1)
        assert url == "https://x/pure_whl"

    def test_invalid_json_fails_loudly(self):
        proc = subprocess.run(
            ["python3", "-c", self.PARSER_SCRIPT],
            input="not json", capture_output=True, text=True,
            timeout=5,
        )
        assert proc.returncode != 0
        assert "failed to parse" in proc.stderr


# ─────────────────────────────────────────────────────────────────
# (c) --check mode dry-run on this dev box. Skip if rnsd not aligned —
# this is a behavioural test for the audit, not the install.
# ─────────────────────────────────────────────────────────────────


class TestCheckModeAudit:
    """End-to-end exercise of `install_meshchatx.sh --check`."""

    def test_check_mode_runs_cleanly(self):
        proc = subprocess.run(
            ["bash", str(INSTALLER), "--check"],
            capture_output=True, text=True, timeout=30,
        )
        # rc == 0 means aligned; rc == 1 means drift. Both are valid
        # exits; rc >= 2 would indicate a script bug (bad args etc.).
        assert proc.returncode in (0, 1), (
            f"--check returned {proc.returncode}; "
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert "MeshChatX canonical install audit" in proc.stdout
        assert "RESULT:" in proc.stdout

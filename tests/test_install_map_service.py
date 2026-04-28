"""
Regression guards for the meshforge-map.service Phase 2 migration
(User=root → User=<operator>).

These tests lock in the contract so future installer rewrites can't
silently reintroduce User=root, which would cause the config/DB drift
the migration was designed to fix.

Run: python3 -m pytest tests/test_install_map_service.py -v
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "scripts" / "meshforge-map.service"
INSTALLER = REPO_ROOT / "scripts" / "install_noc.sh"
MIGRATOR = REPO_ROOT / "scripts" / "migrate_map_service_to_user.sh"


class TestServiceTemplate:
    """meshforge-map.service must use the placeholder, not User=root."""

    def test_template_exists(self):
        assert TEMPLATE.is_file(), f"missing template: {TEMPLATE}"

    def test_template_uses_placeholder(self):
        text = TEMPLATE.read_text()
        assert "User=__MESHFORGE_USER__" in text, (
            "Template must declare User=__MESHFORGE_USER__ — installer "
            "substitutes the operator login at install time."
        )

    def test_template_has_no_user_root(self):
        text = TEMPLATE.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert stripped != "User=root", (
                "Template must not have User=root — Phase 2 migrated this "
                "service to run as the operator. See "
                "persistent_issues.md / project_map_phase2_handoff.md."
            )


class TestInstallerSubstitution:
    """install_noc.sh must substitute the placeholder, not blindly copy."""

    def test_installer_exists(self):
        assert INSTALLER.is_file(), f"missing installer: {INSTALLER}"

    def test_installer_substitutes_placeholder(self):
        text = INSTALLER.read_text()
        assert "__MESHFORGE_USER__" in text, (
            "Installer must reference __MESHFORGE_USER__ to substitute it."
        )
        assert "MESHFORGE_MAP_USER" in text, (
            "Installer must resolve operator user via "
            "$MESHFORGE_MAP_USER before copying the unit."
        )
        assert "resolve_operator_user" in text, (
            "Installer must define + call resolve_operator_user() helper."
        )

    def test_installer_inline_fallback_uses_operator(self):
        """Inline fallback heredoc must use the resolved operator, not root."""
        text = INSTALLER.read_text()
        marker = "cat > /etc/systemd/system/meshforge-map.service"
        idx = text.find(marker)
        assert idx >= 0, "inline fallback heredoc not found in installer"
        # Skip past the heredoc opener line; closing terminator is "MAP_SERVICE"
        # at column 0 on its own line.
        body_start = text.find("\n", idx) + 1
        end_idx = text.find("\nMAP_SERVICE\n", body_start)
        assert end_idx > body_start, "MAP_SERVICE heredoc terminator not found"
        block = text[body_start:end_idx]
        assert "User=${MESHFORGE_MAP_USER}" in block, (
            "Inline fallback must use User=${MESHFORGE_MAP_USER}, not User=root."
        )
        assert "User=root" not in block, (
            "Inline fallback must not contain User=root (Phase 2 regression)."
        )

    def test_resolve_operator_user_refuses_root(self):
        """The helper must explicitly refuse 'root' / empty."""
        text = INSTALLER.read_text()
        # Just check the guard logic is present
        assert '"$u" == "root"' in text or "'root'" in text, (
            "resolve_operator_user must reject u=root."
        )
        assert "getent passwd" in text, (
            "resolve_operator_user must verify user exists in /etc/passwd."
        )


class TestMigrationScript:
    """Phase 2 migration script must exist + have the right safety properties."""

    def test_migrator_exists(self):
        assert MIGRATOR.is_file(), f"missing migrator: {MIGRATOR}"

    def test_migrator_is_executable(self):
        import os
        assert os.access(MIGRATOR, os.X_OK), (
            f"migrator must be executable: chmod +x {MIGRATOR}"
        )

    def test_migrator_supports_dry_run(self):
        text = MIGRATOR.read_text()
        assert "--dry-run" in text, "migrator must support --dry-run"

    def test_migrator_is_idempotent(self):
        """Re-running on an already-migrated host must be a no-op."""
        text = MIGRATOR.read_text()
        # The check looks at the existing unit's User= and exits 0 if it
        # already matches the resolved operator user.
        assert 'CURRENT_USER" == "$OP_USER"' in text, (
            "migrator must short-circuit when unit already runs as operator."
        )

    def test_migrator_preserves_user_db_as_bak(self):
        """Existing user-side DB must be preserved as .bak.<date>."""
        text = MIGRATOR.read_text()
        assert ".bak." in text, (
            "migrator must rename existing user DB to .bak.<date> before "
            "overwriting (preserves any TUI-side history snapshot)."
        )

    def test_migrator_handles_root_owned_user_db(self):
        """moc3 gotcha: user-side DB may be root-owned from past sudo TUI run."""
        text = MIGRATOR.read_text()
        # Look for the chown + mv pattern — chown runs before mv-aside
        # so the rename works even when user lacks write perms.
        assert "chown" in text and "mv" in text, (
            "migrator must chown user DB before mv-aside (moc3 gotcha)."
        )

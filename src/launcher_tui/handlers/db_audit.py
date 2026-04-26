"""DB Health Handler — runs scripts/db_audit.py and shows results in TUI.

Closes the operator-side gap from the fleet-host 2026-04-26 wedge: HAMs
shouldn't need to remember a script path. One keystroke from the
System menu → table dump of every DB's health, size, permissions,
and PRAGMA state.
"""

import importlib.util
import logging
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class DBAuditHandler(BaseHandler):
    """TUI handler that runs the cross-DB audit and shows results."""

    handler_id = "db_audit"
    menu_section = "system"

    def menu_items(self):
        return [
            ("db_health", "DB Health           Audit all SQLite DBs", None),
        ]

    def execute(self, action):
        if action == "db_health":
            self._run_audit()

    def _run_audit(self):
        """Run db_audit.py and show the table in a msgbox."""
        try:
            audit_db, render_table, inventory = self._load_audit()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "DB Health",
                f"Failed to load audit module: {e}\n\n"
                f"Expected at /opt/meshforge/scripts/db_audit.py and "
                f"/opt/meshforge/src/utils/db_inventory.py."
            )
            return

        try:
            results = [
                audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
                for spec in inventory
            ]
            table = render_table(results, verbose=False)
            n_fail = sum(1 for r in results if r.verdict == "FAIL")
            n_warn = sum(1 for r in results if r.verdict == "WARN")
            n_missing = sum(1 for r in results if r.verdict == "NOT_CREATED")
            n_ok = sum(1 for r in results if r.verdict == "OK")
            summary = (
                f"OK: {n_ok}    WARN: {n_warn}    "
                f"FAIL: {n_fail}    NOT_CREATED: {n_missing}\n\n"
            )
            self.ctx.dialog.msgbox(
                "DB Health",
                summary + table + "\n\n"
                "FAIL = needs attention. NOT_CREATED = never opened on this box "
                "(normal for unused features). Run `python3 "
                "/opt/meshforge/scripts/db_audit.py --verbose` for details."
            )
        except Exception as e:
            logger.exception("DB audit failed")
            self.ctx.dialog.msgbox("DB Health", f"Audit failed: {e}")

    @staticmethod
    def _load_audit():
        """Import scripts/db_audit.py + utils/db_inventory.py.

        scripts/ isn't on the package path; we load by file location.
        """
        repo_root = Path(__file__).resolve().parents[3]
        audit_path = repo_root / "scripts" / "db_audit.py"
        spec = importlib.util.spec_from_file_location("db_audit", audit_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load {audit_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The audit module already imports INVENTORY internally, but
        # we re-import here so the handler stays explicit.
        from utils.db_inventory import INVENTORY
        return mod.audit_db, mod.render_table, INVENTORY

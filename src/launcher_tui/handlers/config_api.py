"""
Config API Server handler — start/stop/status for RESTful config API.

Batch 10c: Extracted from MeshForgeLauncher._config_api_menu() and
lifecycle methods in main.py. Implements LifecycleHandler for
auto-start on TUI launch and auto-stop on TUI exit.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class ConfigAPIHandler(BaseHandler):
    """Config API Server — RESTful configuration API on localhost:8081."""

    handler_id = "config_api"
    menu_section = "configuration"

    def __init__(self):
        super().__init__()
        self._server = None

    def menu_items(self):
        return [
            ("config-api", "Config API Server   REST config endpoint", None),
        ]

    def execute(self, action):
        if action == "config-api":
            self._config_api_menu()

    # -- LifecycleHandler protocol --

    def on_startup(self):
        """Auto-start Config API Server on TUI launch."""
        self._maybe_auto_start()

    def on_shutdown(self):
        """Stop the Config API Server on TUI exit."""
        self._stop_server()

    # -- Menu --

    def _config_api_menu(self):
        """Config API Server start/stop/status menu."""
        while True:
            running = self._server and self._server.is_running
            status = "RUNNING on 127.0.0.1:8081" if running else "STOPPED"

            choices = [
                ("status", f"Status              {status}"),
            ]
            if running:
                choices.append(("stop", "Stop Config API Server"))
            else:
                choices.append(("start", "Start Config API Server"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "Config API Server",
                "RESTful configuration API for dynamic reconfiguration.\n\n"
                f"Status: {status}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                if running:
                    self.ctx.dialog.msgbox(
                        "Config API Status",
                        "Config API Server is RUNNING\n\n"
                        "  Endpoint: http://127.0.0.1:8081/config\n"
                        "  GET /config/<path> - Read config value\n"
                        "  PUT /config/<path> - Set config value\n"
                        "  DELETE /config/<path> - Remove value\n"
                        "  GET /config/_paths - List all paths\n"
                        "  GET /config/_audit - Audit log"
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "Config API Status",
                        "Config API Server is STOPPED\n\n"
                        "Start it to enable dynamic reconfiguration\n"
                        "via RESTful API."
                    )
            elif choice == "start":
                self._maybe_auto_start()
                if self._server and self._server.is_running:
                    self.ctx.dialog.msgbox("Started", "Config API Server started on 127.0.0.1:8081")
                else:
                    self.ctx.dialog.msgbox("Error", "Failed to start Config API Server.\nCheck logs for details.")
            elif choice == "stop":
                self._stop_server()
                self.ctx.dialog.msgbox("Stopped", "Config API Server stopped.")

    # -- Lifecycle helpers --

    def _maybe_auto_start(self):
        """Auto-start Config API Server. Silent on failure."""
        try:
            from utils import config_api as config_api_mod
            create_gateway_config_api = config_api_mod.create_gateway_config_api
            ConfigAPIServer = config_api_mod.ConfigAPIServer
            api = create_gateway_config_api()
            self._server = ConfigAPIServer(api, host="127.0.0.1", port=8081)
            if self._server.start():
                logger.info("Config API server started on 127.0.0.1:8081")
            else:
                logger.debug("Config API server failed to start")
                self._server = None
        except Exception as e:
            logger.debug("Config API auto-start failed: %s", e)
            self._server = None

    def _stop_server(self):
        """Stop the Config API Server."""
        if self._server and self._server.is_running:
            try:
                self._server.stop()
                logger.info("Config API server stopped")
            except Exception as e:
                logger.debug("Config API stop failed: %s", e)
            self._server = None

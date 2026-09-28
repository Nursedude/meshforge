"""Gateway config migrations — split out of gateway/config.py (MF025 cap)."""

import logging

logger = logging.getLogger("gateway.config")


def migrate_stale_http_port(meshtastic_data: dict) -> dict:
    """Migrate the stale http_port=443 default (Issue #62 pattern).

    http_port defaulted to 443 for a long stretch and got baked into
    every rendered/saved gateway.json, while meshtasticd's web API
    lives on 9443 — so the primary stateless TX path was dead
    (connection refused, circuit breaker permanently flapping) and
    every send rode the legacy session fallback (#17 contention
    class). 443 was never a valid value for us (Issue #58 treats a
    :443 webserver override as forbidden), so it is safe to treat
    a saved 443 as the stale default rather than operator intent.
    """
    if meshtastic_data.get('http_port') == 443:
        meshtastic_data = dict(meshtastic_data)
        meshtastic_data['http_port'] = 9443
        logger.info(
            "Migrated stale http_port 443 -> 9443 "
            "(saved default predating the 9443 fix)")
    return meshtastic_data

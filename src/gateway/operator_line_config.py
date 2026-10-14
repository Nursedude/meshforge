"""``gateway.json`` → ``"operator_line"`` section (kept out of config.py, MF025).

Closed by default: ``enabled=False`` and an empty ``operators`` list. An
operator is an LXMF source hash (32 hex) of a person allowed to query the box
and receive its pages. A malformed entry is REFUSED and recorded in
``errors`` (honest_failure_modes #3) — never coerced, never silently dropped.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, List

logger = logging.getLogger(__name__)

_HASH = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class OperatorLineConfig:
    enabled: bool = False
    display_name: str = ""            # "" → "MeshForge Operator (<hostname>)"
    operators: List[str] = field(default_factory=list)
    announce_interval_sec: int = 1800
    cooldown_s: float = 10.0          # per operator, between answers
    alerts_enabled: bool = True
    alert_poll_s: float = 30.0        # watchdog tick is 30 s
    alert_min_interval_s: float = 300.0   # LoRa airtime: at most one page per 5 min
    identity_file: str = ""
    errors: List[str] = field(default_factory=list, compare=False, repr=False)

    @property
    def closed(self) -> bool:
        """Nobody can reach the line (empty allowlist)."""
        return not self.operators

    @classmethod
    def from_dict(cls, data: Any) -> "OperatorLineConfig":
        data = data if isinstance(data, dict) else {}
        errors: List[str] = []
        raw_ops = data.get("operators", [])
        ops: List[str] = []
        if not isinstance(raw_ops, list):
            errors.append("operators must be a list of 32-hex LXMF hashes")
            raw_ops = []
        for item in raw_ops:
            h = str(item).strip().lower()
            if _HASH.match(h):
                if h not in ops:
                    ops.append(h)
            else:
                errors.append(f"operators: refused {str(item)[:40]!r} (not a 32-hex LXMF hash)")

        def num(key, default, lo):
            try:
                v = float(data.get(key, default))
            except (TypeError, ValueError):
                errors.append(f"{key}: not a number")
                return default
            return max(lo, v)

        cfg = cls(
            enabled=data.get("enabled", False) is True,
            display_name=str(data.get("display_name", "") or ""),
            operators=ops,
            announce_interval_sec=int(num("announce_interval_sec", 1800, 0)),
            cooldown_s=num("cooldown_s", 10.0, 0),
            alerts_enabled=data.get("alerts_enabled", True) is not False,
            alert_poll_s=num("alert_poll_s", 30.0, 5),
            alert_min_interval_s=num("alert_min_interval_s", 300.0, 0),
            identity_file=str(data.get("identity_file", "") or ""),
        )
        cfg.errors = errors
        for e in errors:
            logger.warning("gateway.json operator_line: %s", e)
        if cfg.enabled and cfg.closed:
            logger.warning("operator_line is enabled with NO operators: it will "
                           "answer nobody and page nobody")
        return cfg

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled, "display_name": self.display_name,
            "operators": list(self.operators),
            "announce_interval_sec": self.announce_interval_sec,
            "cooldown_s": self.cooldown_s, "alerts_enabled": self.alerts_enabled,
            "alert_poll_s": self.alert_poll_s,
            "alert_min_interval_s": self.alert_min_interval_s,
            "identity_file": self.identity_file,
        }

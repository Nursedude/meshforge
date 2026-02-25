"""
Structured JSON Logging for MeshForge NOC.

Provides machine-parseable JSON log output alongside human-readable logs.
Opt-in: enable via setup_structured_logging() or --structured flag.

Output format (one JSON object per line, .jsonl):
{
    "ts": "2026-01-23T14:30:00.123456",
    "level": "ERROR",
    "logger": "gateway.rns_bridge",
    "msg": "Connection refused to rnsd",
    "module": "rns_bridge",
    "line": 217,
    "thread": "Thread-3",
    "exc": null
}

Usage:
    from utils.logging_structured import setup_structured_logging
    setup_structured_logging()  # Adds JSON handler to root logger

Query examples:
    # Find all errors in last hour
    cat meshforge_structured.jsonl | python3 -c "
        import json, sys
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
        for line in sys.stdin:
            obj = json.loads(line)
            if obj['level'] == 'ERROR' and obj['ts'] > cutoff:
                print(f'{obj[\"ts\"]} [{obj[\"logger\"]}] {obj[\"msg\"]}')
    "
"""

import json
import logging
import logging.handlers
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.paths import get_real_user_home


class StructuredFormatter(logging.Formatter):
    """JSON formatter producing one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            'ts': datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec='microseconds'),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
            'module': record.module,
            'line': record.lineno,
            'thread': record.threadName,
            'exc': None,
        }

        if record.exc_info and record.exc_info[0]:
            log_obj['exc'] = traceback.format_exception(
                record.exc_info[0],
                record.exc_info[1],
                record.exc_info[2],
            )

        return json.dumps(log_obj, ensure_ascii=False)


def setup_structured_logging(
    log_dir: Optional[Path] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    min_level: int = logging.INFO,
) -> logging.Handler:
    """
    Add structured JSON logging handler to root logger.

    Args:
        log_dir: Directory for log files (default: ~/.config/meshforge/logs/)
        max_bytes: Max file size before rotation
        backup_count: Number of rotated files to keep
        min_level: Minimum log level to capture

    Returns:
        The configured handler (for testing/removal)
    """
    if log_dir is None:
        log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "meshforge_structured.jsonl"

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    handler.setLevel(min_level)
    handler.setFormatter(StructuredFormatter())

    logging.getLogger().addHandler(handler)
    return handler

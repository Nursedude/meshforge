"""
System Blueprint - Status, logs, versions, processes

Handles system-level API endpoints for monitoring and diagnostics.
"""

from flask import Blueprint, jsonify, request, Response
import subprocess
import os
import re
from functools import lru_cache
from datetime import datetime

system_bp = Blueprint('system', __name__)


@system_bp.route('/status')
def api_status():
    """Get overall system status."""
    from main_web import check_service_status, get_system_stats

    status = check_service_status()
    stats = get_system_stats()

    return jsonify({
        'service': status,
        'system': stats,
        'timestamp': datetime.now().isoformat()
    })


@system_bp.route('/logs')
def api_logs():
    """Get service logs."""
    from main_web import get_service_logs

    lines = request.args.get('lines', 50, type=int)
    logs = get_service_logs(lines=min(lines, 500))  # Cap at 500 lines
    return jsonify({'logs': logs})


@system_bp.route('/logs/stream')
def api_logs_stream():
    """Stream logs in real-time using Server-Sent Events."""
    from main_web import validate_journalctl_since

    since = request.args.get('since', '5m')

    # Validate since parameter to prevent command injection
    if not validate_journalctl_since(since):
        return jsonify({'error': 'Invalid since parameter'}), 400

    def generate():
        try:
            # Use journalctl with proper argument list (no shell=True)
            process = subprocess.Popen(
                ['journalctl', '-u', 'meshtasticd', '-f', '--since', since, '-n', '100'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            for line in iter(process.stdout.readline, ''):
                if line:
                    yield f"data: {line}\n\n"

        except Exception as e:
            yield f"data: Error: {e}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@system_bp.route('/versions')
def api_versions():
    """Get software version information."""
    versions = {}

    # Python version
    import sys
    versions['python'] = sys.version.split()[0]

    # Meshtasticd version
    try:
        result = subprocess.run(
            ['meshtasticd', '--version'],
            capture_output=True, text=True, timeout=5
        )
        versions['meshtasticd'] = result.stdout.strip() or 'unknown'
    except Exception:
        versions['meshtasticd'] = 'not installed'

    # MeshForge version
    try:
        from __version__ import __version__
        versions['meshforge'] = __version__
    except ImportError:
        versions['meshforge'] = 'unknown'

    return jsonify(versions)


@system_bp.route('/processes')
def api_processes():
    """Get top processes."""
    try:
        result = subprocess.run(
            ['ps', 'aux', '--sort=-%cpu'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[:11]
            return jsonify({'processes': lines})
        return jsonify({'error': result.stderr or 'Failed to get processes'})
    except Exception as e:
        return jsonify({'error': str(e)})


@system_bp.route('/hardware')
def api_hardware():
    """Get hardware detection information."""
    from main_web import detect_hardware

    devices = detect_hardware()
    return jsonify({'devices': devices})

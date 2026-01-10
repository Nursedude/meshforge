"""
Service Blueprint - Service control operations

Handles systemctl operations for meshtasticd and related services.
"""

from flask import Blueprint, jsonify, request
import subprocess

service_bp = Blueprint('service', __name__)

# Valid service actions
VALID_ACTIONS = {'start', 'stop', 'restart', 'status'}

# Services that can be controlled
ALLOWED_SERVICES = {'meshtasticd', 'rnsd'}


@service_bp.route('/service/<action>', methods=['POST'])
def api_service_action(action):
    """Control meshtasticd service.

    Args:
        action: One of 'start', 'stop', 'restart'

    Returns:
        JSON response with success status
    """
    if action not in ['start', 'stop', 'restart']:
        return jsonify({'error': 'Invalid action'}), 400

    try:
        result = subprocess.run(
            ['systemctl', action, 'meshtasticd'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return jsonify({'success': True, 'message': f'Service {action}ed'})
        return jsonify({'success': False, 'error': result.stderr})
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Service operation timed out'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@service_bp.route('/service/<service_name>/status')
def api_service_status(service_name):
    """Get status of a specific service.

    Args:
        service_name: Name of service to check

    Returns:
        JSON with service status information
    """
    if service_name not in ALLOWED_SERVICES:
        return jsonify({'error': 'Service not allowed'}), 400

    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True, text=True, timeout=10
        )
        active = result.stdout.strip() == 'active'

        # Get more detailed status
        status_result = subprocess.run(
            ['systemctl', 'status', service_name, '--no-pager'],
            capture_output=True, text=True, timeout=10
        )

        return jsonify({
            'service': service_name,
            'active': active,
            'status': result.stdout.strip(),
            'details': status_result.stdout[:500] if status_result.stdout else None
        })
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Status check timed out'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@service_bp.route('/service/<service_name>/<action>', methods=['POST'])
def api_service_control(service_name, action):
    """Control a specific service.

    Args:
        service_name: Name of service to control
        action: One of 'start', 'stop', 'restart'

    Returns:
        JSON response with success status
    """
    if service_name not in ALLOWED_SERVICES:
        return jsonify({'error': 'Service not allowed'}), 400

    if action not in ['start', 'stop', 'restart']:
        return jsonify({'error': 'Invalid action'}), 400

    try:
        result = subprocess.run(
            ['systemctl', action, service_name],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': f'{service_name} {action}ed',
                'service': service_name
            })
        return jsonify({
            'success': False,
            'error': result.stderr,
            'service': service_name
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Service operation timed out'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@service_bp.route('/services/status')
def api_all_services_status():
    """Get status of all monitored services."""
    statuses = {}

    for service_name in ALLOWED_SERVICES:
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service_name],
                capture_output=True, text=True, timeout=10
            )
            statuses[service_name] = {
                'active': result.stdout.strip() == 'active',
                'status': result.stdout.strip()
            }
        except Exception:
            statuses[service_name] = {
                'active': False,
                'status': 'unknown'
            }

    return jsonify({'services': statuses})

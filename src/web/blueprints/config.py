"""
Config Blueprint - Configuration file management

Handles meshtasticd configuration file operations.
"""

from flask import Blueprint, jsonify, request
import os
import re
from pathlib import Path

config_bp = Blueprint('config', __name__)


def validate_config_name(config_name: str) -> bool:
    """Validate config name to prevent path traversal."""
    if not config_name:
        return False
    # Only allow alphanumeric, hyphens, underscores, and .yaml/.yml extension
    pattern = r'^[a-zA-Z0-9_-]+\.ya?ml$'
    return bool(re.match(pattern, config_name))


@config_bp.route('/configs')
def api_configs():
    """Get list of available configuration files."""
    from main_web import get_configs

    configs = get_configs()
    return jsonify({'configs': configs})


@config_bp.route('/config/content/<config_name>')
def api_config_content(config_name):
    """Get contents of a specific config file."""
    # Validate config name
    if not validate_config_name(config_name):
        return jsonify({'error': 'Invalid config name'}), 400

    config_dirs = [
        Path('/etc/meshtasticd/config.d'),
        Path('/etc/meshtasticd/available')
    ]

    for config_dir in config_dirs:
        config_path = config_dir / config_name
        if config_path.exists() and config_path.is_file():
            try:
                # Verify the path hasn't escaped the directory
                resolved = config_path.resolve()
                if not str(resolved).startswith(str(config_dir.resolve())):
                    return jsonify({'error': 'Invalid path'}), 400

                content = config_path.read_text()
                return jsonify({
                    'name': config_name,
                    'content': content,
                    'path': str(config_path)
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Config not found'}), 404


@config_bp.route('/config/activate', methods=['POST'])
def api_activate_config():
    """Activate a configuration file."""
    data = request.get_json() or {}
    config_name = data.get('config')

    if not config_name or not validate_config_name(config_name):
        return jsonify({'error': 'Invalid config name'}), 400

    available_dir = Path('/etc/meshtasticd/available')
    active_dir = Path('/etc/meshtasticd/config.d')

    source = available_dir / config_name
    target = active_dir / config_name

    if not source.exists():
        return jsonify({'error': 'Config not found'}), 404

    try:
        # Create symlink
        if target.exists():
            target.unlink()
        target.symlink_to(source)

        return jsonify({
            'success': True,
            'message': f'Activated {config_name}'
        })
    except PermissionError:
        return jsonify({
            'error': 'Permission denied - run as root'
        }), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@config_bp.route('/config/deactivate', methods=['POST'])
def api_deactivate_config():
    """Deactivate a configuration file."""
    data = request.get_json() or {}
    config_name = data.get('config')

    if not config_name or not validate_config_name(config_name):
        return jsonify({'error': 'Invalid config name'}), 400

    active_dir = Path('/etc/meshtasticd/config.d')
    target = active_dir / config_name

    if not target.exists():
        return jsonify({'error': 'Config not active'}), 404

    try:
        target.unlink()
        return jsonify({
            'success': True,
            'message': f'Deactivated {config_name}'
        })
    except PermissionError:
        return jsonify({
            'error': 'Permission denied - run as root'
        }), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@config_bp.route('/config/edit', methods=['POST'])
def api_edit_config():
    """Edit a configuration file."""
    data = request.get_json() or {}
    config_name = data.get('config')
    content = data.get('content')

    if not config_name or not validate_config_name(config_name):
        return jsonify({'error': 'Invalid config name'}), 400

    if content is None:
        return jsonify({'error': 'No content provided'}), 400

    # Validate content is valid YAML
    try:
        import yaml
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return jsonify({'error': f'Invalid YAML: {e}'}), 400
    except ImportError:
        pass  # yaml not available, skip validation

    config_dirs = [
        Path('/etc/meshtasticd/available'),
        Path('/etc/meshtasticd/config.d')
    ]

    for config_dir in config_dirs:
        config_path = config_dir / config_name
        if config_path.exists():
            try:
                # Create backup
                backup_path = config_path.with_suffix('.yaml.bak')
                if config_path.exists():
                    backup_path.write_text(config_path.read_text())

                # Write new content
                config_path.write_text(content)

                return jsonify({
                    'success': True,
                    'message': f'Updated {config_name}',
                    'backup': str(backup_path)
                })
            except PermissionError:
                return jsonify({
                    'error': 'Permission denied - run as root'
                }), 403
            except Exception as e:
                return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Config not found'}), 404


@config_bp.route('/radio')
def api_radio():
    """Get radio configuration and info."""
    from main_web import get_radio_info

    use_cache = request.args.get('cache', 'true').lower() == 'true'
    radio_info = get_radio_info(use_cache=use_cache)
    return jsonify(radio_info)

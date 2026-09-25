"""RNS interface configuration templates.

Pre-built templates for common RNS interface configurations
(AutoInterface, TCP, Serial, LoRa, Meshtastic).

Extracted from rns.py for file size compliance (CLAUDE.md #6).
"""

import logging
from typing import Dict, Any, List

from .base import CommandResult

logger = logging.getLogger(__name__)


def get_interface_templates() -> CommandResult:
    """
    Get pre-built interface configuration templates.

    Returns:
        CommandResult with template list
    """
    templates = {
        'auto': {
            'name': 'AutoInterface',
            'description': 'Zero-config local network discovery (UDP multicast)',
            'type': 'AutoInterface',
            'settings': {}
        },
        'tcp_server': {
            'name': 'TCP Server',
            'description': 'Accept incoming RNS connections',
            'type': 'TCPServerInterface',
            'settings': {
                'listen_ip': '0.0.0.0',
                'listen_port': '4242'
            }
        },
        'tcp_client': {
            'name': 'TCP Client',
            'description': 'Connect to remote RNS server',
            'type': 'TCPClientInterface',
            'settings': {
                'target_host': '192.168.1.100',
                'target_port': '4242'
            }
        },
        'tcp_rns_amsterdam': {
            'name': 'RNS Testnet Amsterdam',
            'description': 'Official RNS testnet (Amsterdam, NL) — offline since 2026-03',
            'type': 'TCPClientInterface',
            'settings': {
                'target_host': 'amsterdam.connect.reticulum.network',
                'target_port': '4965'
            }
        },
        'tcp_rns_betweentheborders': {
            'name': 'RNS BetweenTheBorders',
            'description': 'Community RNS node (USA)',
            'type': 'TCPClientInterface',
            'settings': {
                'target_host': 'reticulum.betweentheborders.com',
                'target_port': '4242'
            }
        },
        'serial': {
            'name': 'Serial Link',
            'description': 'Direct serial/USB connection',
            'type': 'SerialInterface',
            'settings': {
                'port': '/dev/ttyUSB0',
                'speed': '115200'
            }
        },
        'meshtastic': {
            'name': 'Meshtastic Gateway',
            'description': 'RNS over Meshtastic LoRa network',
            'type': 'Meshtastic_Interface',
            'settings': {
                'tcp_port': '127.0.0.1:4403',
                'data_speed': '8',
                'hop_limit': '3'
            }
        },
        'meshtastic_dual': {
            'name': 'Meshtastic Dual-Radio Gateway',
            'description': 'Two radios: Short Turbo + Long Fast',
            'multi_interface': True,
            'interfaces': [
                {
                    'default_name': 'Meshtastic Short Turbo',
                    'type': 'Meshtastic_Interface',
                    'settings': {
                        'mode': 'gateway',
                        'tcp_port': '127.0.0.1:4403',
                        'data_speed': '8',
                        'hop_limit': '3',
                    }
                },
                {
                    'default_name': 'Meshtastic Long Fast',
                    'type': 'Meshtastic_Interface',
                    'settings': {
                        'mode': 'gateway',
                        'tcp_port': '127.0.0.1:4404',
                        'data_speed': '0',
                        'hop_limit': '3',
                    }
                }
            ]
        },
        'rnode': {
            'name': 'RNode LoRa',
            'description': 'Direct LoRa via RNode hardware',
            'type': 'RNodeInterface',
            'settings': _rnode_template_settings(),
        }
    }

    return CommandResult.ok(
        f"Available templates: {len(templates)}",
        data={'templates': templates}
    )


def apply_template(template_name: str, interface_name: str, overrides: Dict[str, str] = None) -> CommandResult:
    """
    Apply an interface template to create a new interface.

    Args:
        template_name: Name of template (auto, tcp_server, etc.)
        interface_name: Name for the new interface
        overrides: Settings to override from template

    Returns:
        CommandResult indicating success
    """
    templates_result = get_interface_templates()
    templates = templates_result.data.get('templates', {})

    if template_name not in templates:
        return CommandResult.fail(
            f"Unknown template: {template_name}",
            data={'available': list(templates.keys())}
        )

    template = templates[template_name]

    if template.get('multi_interface'):
        return CommandResult.fail(
            f"Template '{template_name}' is a multi-interface template. "
            f"Use apply_multi_template() instead."
        )

    settings = template['settings'].copy()

    # Apply overrides
    if overrides:
        settings.update(overrides)

    from .rns import add_interface  # lazy import to avoid circular
    return add_interface(interface_name, template['type'], settings)


def apply_multi_template(
    template_name: str,
    interface_configs: List[Dict[str, Any]],
) -> CommandResult:
    """
    Apply a multi-interface template to create several interfaces at once.

    Args:
        template_name: Name of template (e.g. meshtastic_dual)
        interface_configs: List of dicts with 'name' and optional 'overrides'
            for each interface defined in the template.

    Returns:
        CommandResult indicating success (all added) or failure
    """
    templates_result = get_interface_templates()
    templates = templates_result.data.get('templates', {})

    if template_name not in templates:
        return CommandResult.fail(
            f"Unknown template: {template_name}",
            data={'available': list(templates.keys())}
        )

    template = templates[template_name]
    if not template.get('multi_interface'):
        return CommandResult.fail(
            f"Template '{template_name}' is not a multi-interface template. "
            f"Use apply_template() instead."
        )

    iface_defs = template.get('interfaces', [])
    if len(interface_configs) != len(iface_defs):
        return CommandResult.fail(
            f"Expected {len(iface_defs)} interface configs, got {len(interface_configs)}"
        )

    from .rns import add_interface  # lazy import to avoid circular

    added = []
    for iface_def, user_cfg in zip(iface_defs, interface_configs):
        name = user_cfg.get('name', iface_def['default_name'])
        settings = iface_def['settings'].copy()
        overrides = user_cfg.get('overrides')
        if overrides:
            settings.update(overrides)

        result = add_interface(name, iface_def['type'], settings)
        if not result.success:
            cleanup_hint = ""
            if added:
                cleanup_hint = (
                    f"\n\nAlready added: {', '.join(added)}"
                    f"\nTo clean up, remove them via Manage Interfaces > Remove."
                )
            return CommandResult.fail(
                f"Failed adding [[{name}]]: {result.message}{cleanup_hint}"
            )
        added.append(name)

    return CommandResult.ok(
        f"Added {len(added)} interfaces: {', '.join(added)}",
        data={'added': added}
    )


def _rnode_template_settings() -> dict:
    """RNode template values from the one profile source (utils.rnode_profile).
    A broken declaration is SHOWN, never silently swapped for the default."""
    from utils.rnode_profile import ProfileError, REGION_DEFAULTS, rnode_profile
    try:
        p, warning = rnode_profile(), None
    except ProfileError as e:
        p, warning = dict(REGION_DEFAULTS["US"]), f"declared profile rejected: {e}"
    settings = {'port': '/dev/ttyUSB0', 'frequency': str(p['frequency']),
                'txpower': str(p['tx_power']), 'bandwidth': str(p['bandwidth']),
                'spreadingfactor': str(p['spreading_factor']),
                'codingrate': str(p['coding_rate'])}
    if warning:
        settings['warning'] = warning
    return settings

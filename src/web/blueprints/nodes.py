"""
Nodes Blueprint - Node discovery and management

Handles mesh node information and messaging.
"""

from flask import Blueprint, jsonify, request
import re

nodes_bp = Blueprint('nodes', __name__)


@nodes_bp.route('/nodes')
def api_nodes():
    """Get basic node list."""
    from main_web import get_nodes

    nodes = get_nodes()
    return jsonify({'nodes': nodes})


@nodes_bp.route('/nodes/full')
def api_nodes_full():
    """Get detailed node information."""
    from main_web import get_nodes_full

    nodes = get_nodes_full()
    return jsonify({'nodes': nodes})


@nodes_bp.route('/nodes/geojson')
def api_nodes_geojson():
    """Get nodes in GeoJSON format for mapping."""
    from main_web import get_nodes_full

    nodes = get_nodes_full()

    # Convert to GeoJSON
    features = []
    for node in nodes:
        if node.get('position') and node['position'].get('latitude'):
            pos = node['position']
            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [
                        pos.get('longitude', 0),
                        pos.get('latitude', 0)
                    ]
                },
                'properties': {
                    'id': node.get('id', ''),
                    'name': node.get('user', {}).get('longName', 'Unknown'),
                    'short_name': node.get('user', {}).get('shortName', ''),
                    'hardware': node.get('user', {}).get('hwModel', ''),
                    'battery': node.get('deviceMetrics', {}).get('batteryLevel'),
                    'snr': node.get('snr'),
                    'last_heard': node.get('lastHeard'),
                    'hops_away': node.get('hopsAway', 0),
                    'altitude': pos.get('altitude'),
                }
            }
            features.append(feature)

    geojson = {
        'type': 'FeatureCollection',
        'features': features
    }

    return jsonify(geojson)


def validate_node_id(node_id: str) -> bool:
    """Validate node ID format."""
    if not node_id:
        return False
    # Accept: !abc123def or abc123def (hex format)
    pattern = r'^!?[0-9a-fA-F]{8,16}$'
    return bool(re.match(pattern, node_id))


@nodes_bp.route('/message', methods=['POST'])
def api_send_message():
    """Send a message to the mesh network."""
    data = request.get_json() or {}
    text = data.get('text', '').strip()
    destination = data.get('destination')

    if not text:
        return jsonify({'error': 'Message text required'}), 400

    # Limit message length
    if len(text) > 200:
        return jsonify({'error': 'Message too long (max 200 chars)'}), 400

    # Validate destination if provided
    if destination and destination != 'broadcast':
        if not validate_node_id(destination):
            return jsonify({'error': 'Invalid destination node ID'}), 400

    try:
        from main_web import send_mesh_message

        result = send_mesh_message(text, destination)

        if result:
            return jsonify({
                'success': True,
                'message': f'Sent to {destination or "broadcast"}'
            })
        else:
            return jsonify({'error': 'Failed to send message'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

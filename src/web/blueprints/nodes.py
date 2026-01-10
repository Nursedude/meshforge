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

    # get_nodes() already returns {'nodes': [...], 'raw': '...'} or {'error': '...'}
    return jsonify(get_nodes())


@nodes_bp.route('/nodes/full')
def api_nodes_full():
    """Get detailed node information."""
    from main_web import get_nodes_full

    # get_nodes_full() already returns proper dict format
    return jsonify(get_nodes_full())


@nodes_bp.route('/nodes/geojson')
def api_nodes_geojson():
    """Get nodes in GeoJSON format for mapping."""
    from main_web import get_nodes_full

    data = get_nodes_full()

    # Handle error response
    if 'error' in data:
        return jsonify({'type': 'FeatureCollection', 'features': [], 'error': data['error']})

    # Extract nodes list from response
    nodes = data.get('nodes', [])
    if not isinstance(nodes, list):
        nodes = []

    # Convert to GeoJSON
    features = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        pos = node.get('position')
        if pos and pos.get('latitude') and pos.get('longitude'):
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
                    'name': node.get('name', 'Unknown'),
                    'short_name': node.get('short', ''),
                    'hardware': node.get('hardware', ''),
                    'battery': node.get('battery'),
                    'snr': node.get('snr'),
                    'last_heard': node.get('last_heard_ago'),
                    'hops_away': node.get('hops', 0),
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

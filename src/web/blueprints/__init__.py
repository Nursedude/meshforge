"""
Flask Blueprints for MeshForge Web Interface

Modular routing for the web API, organized by domain.
"""

from .system import system_bp
from .config import config_bp
from .nodes import nodes_bp
from .network import network_bp
from .service import service_bp
from .gateway import gateway_bp

__all__ = [
    'system_bp',
    'config_bp',
    'nodes_bp',
    'network_bp',
    'service_bp',
    'gateway_bp',
]


def register_blueprints(app):
    """Register all blueprints with the Flask app."""
    app.register_blueprint(system_bp, url_prefix='/api')
    app.register_blueprint(config_bp, url_prefix='/api')
    app.register_blueprint(nodes_bp, url_prefix='/api')
    app.register_blueprint(network_bp, url_prefix='/api')
    app.register_blueprint(service_bp, url_prefix='/api')
    app.register_blueprint(gateway_bp, url_prefix='/api')

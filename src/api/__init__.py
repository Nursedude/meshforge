"""
MeshForge REST API

Flask blueprints for API endpoints.
"""

from flask import Blueprint

# Create main API blueprint
api_bp = Blueprint('api', __name__, url_prefix='/api')

# Import and register sub-blueprints
from .diagnostics import diagnostics_bp
api_bp.register_blueprint(diagnostics_bp)

__all__ = ['api_bp', 'diagnostics_bp']

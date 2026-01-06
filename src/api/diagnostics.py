"""
Diagnostics REST API

RESTful endpoints that consume the unified DiagnosticEngine.
All endpoints return JSON responses.

Endpoints:
    GET  /api/diagnostics/health          - Overall health status
    GET  /api/diagnostics/health/<name>   - Single subsystem health
    GET  /api/diagnostics/checks          - All check results
    POST /api/diagnostics/checks/run      - Run diagnostics
    GET  /api/diagnostics/events          - Recent events
    GET  /api/diagnostics/report          - Full report JSON
    POST /api/diagnostics/report/save     - Save report to file
    GET  /api/diagnostics/wizard/<type>   - Run wizard
"""

from flask import Blueprint, jsonify, request
import logging

logger = logging.getLogger(__name__)

diagnostics_bp = Blueprint('diagnostics', __name__, url_prefix='/diagnostics')


def _get_engine():
    """Get DiagnosticEngine instance (lazy import to avoid circular deps)."""
    try:
        from core.diagnostics import DiagnosticEngine
        return DiagnosticEngine.get_instance()
    except ImportError:
        # Fallback for different import paths
        try:
            from ..core.diagnostics import DiagnosticEngine
            return DiagnosticEngine.get_instance()
        except ImportError:
            return None


@diagnostics_bp.route('/health')
def get_health():
    """Get overall health status and all subsystems."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    health = engine.get_health()
    overall = engine.get_overall_health()

    return jsonify({
        'overall': overall.value,
        'subsystems': {k: v.to_dict() for k, v in health.items()}
    })


@diagnostics_bp.route('/health/<subsystem>')
def get_subsystem_health(subsystem: str):
    """Get health for a specific subsystem."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    health = engine.get_health(subsystem)

    if not health or subsystem not in health:
        return jsonify({'error': f'Unknown subsystem: {subsystem}'}), 404

    return jsonify(health[subsystem].to_dict())


@diagnostics_bp.route('/checks')
def get_checks():
    """Get all check results with optional filtering."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    # Optional filters
    category = request.args.get('category')
    status = request.args.get('status')

    from core.diagnostics import CheckCategory, CheckStatus

    cat_filter = None
    status_filter = None

    if category:
        try:
            cat_filter = CheckCategory(category)
        except ValueError:
            return jsonify({'error': f'Unknown category: {category}'}), 400

    if status:
        try:
            status_filter = CheckStatus(status)
        except ValueError:
            return jsonify({'error': f'Unknown status: {status}'}), 400

    results = engine.get_results(category=cat_filter, status=status_filter)

    return jsonify({
        'count': len(results),
        'checks': [r.to_dict() for r in results]
    })


@diagnostics_bp.route('/checks/run', methods=['POST'])
def run_checks():
    """Run diagnostics (optionally for a specific category)."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    data = request.get_json() or {}
    category = data.get('category')

    from core.diagnostics import CheckCategory

    if category:
        try:
            cat = CheckCategory(category)
            results = engine.run_category(cat)
        except ValueError:
            return jsonify({'error': f'Unknown category: {category}'}), 400
    else:
        results = engine.run_all()

    return jsonify({
        'count': len(results),
        'results': [r.to_dict() for r in results]
    })


@diagnostics_bp.route('/events')
def get_events():
    """Get diagnostic events with optional filtering."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    limit = request.args.get('limit', 100, type=int)
    severity = request.args.get('severity')

    from core.diagnostics import EventSeverity

    sev_filter = None
    if severity:
        try:
            sev_filter = EventSeverity(severity)
        except ValueError:
            return jsonify({'error': f'Unknown severity: {severity}'}), 400

    events = engine.get_events(severity=sev_filter, limit=limit)

    return jsonify({
        'count': len(events),
        'events': [e.to_dict() for e in events]
    })


@diagnostics_bp.route('/report')
def get_report():
    """Get full diagnostic report (runs all checks first)."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    # Run all checks to ensure fresh data
    engine.run_all()
    report = engine.generate_report()

    return jsonify(report.to_dict())


@diagnostics_bp.route('/report/save', methods=['POST'])
def save_report():
    """Save diagnostic report to file."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    data = request.get_json() or {}
    filename = data.get('filename')

    try:
        report_path = engine.save_report(filename)
        return jsonify({
            'success': True,
            'path': str(report_path)
        })
    except Exception as e:
        logger.error(f"Failed to save report: {e}")
        return jsonify({'error': str(e)}), 500


@diagnostics_bp.route('/wizard/<wizard_type>')
def run_wizard(wizard_type: str):
    """Run diagnostic wizard."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    if wizard_type not in ('gateway', 'full'):
        return jsonify({'error': f'Unknown wizard type: {wizard_type}'}), 400

    wizard_data = engine.run_wizard(wizard_type)

    return jsonify(wizard_data)


@diagnostics_bp.route('/categories')
def get_categories():
    """List all available check categories."""
    from core.diagnostics import CheckCategory

    return jsonify({
        'categories': [c.value for c in CheckCategory]
    })


@diagnostics_bp.route('/monitor/start', methods=['POST'])
def start_monitoring():
    """Start background health monitoring."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    data = request.get_json() or {}
    interval = data.get('interval', 30)

    engine.start_monitoring(interval=interval)

    return jsonify({
        'success': True,
        'interval': interval
    })


@diagnostics_bp.route('/monitor/stop', methods=['POST'])
def stop_monitoring():
    """Stop background health monitoring."""
    engine = _get_engine()
    if not engine:
        return jsonify({'error': 'DiagnosticEngine not available'}), 500

    engine.stop_monitoring()

    return jsonify({'success': True})

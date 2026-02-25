"""
Tactical Map Module for MeshForge.

Generates tactical overlays on Folium maps:
- Zone polygons and circles (from ZoneMarking messages)
- Tactical markers (incident, hazard, checkpoint, rally point, etc.)
- Check-in positions with status indicators

Export formats:
- KML/KMZ for Google Earth and ATAK
- Cursor-on-Target (CoT) XML for the TAK ecosystem

Uses Folium (optional) for map generation. KML/CoT export uses
stdlib xml.etree.ElementTree (no extra dependencies).

Usage:
    from tactical.tactical_map import (
        generate_tactical_map, export_kml, export_cot_xml,
    )

    zones = timeline.get_active_zones()
    checkins = timeline.get_recent_checkins(minutes=60)
    generate_tactical_map(zones, checkins, [], Path("tactical_map.html"))
    export_kml(zones, [], Path("tactical.kml"))
"""

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.safe_import import safe_import

from tactical.models import CheckIn, ZoneMarking

logger = logging.getLogger(__name__)

# Optional Folium
_folium, _HAS_FOLIUM = safe_import('folium')


class TacticalMarkerType(Enum):
    """Tactical marker types with display properties."""
    INCIDENT = "incident"
    RESOURCE = "resource"
    HAZARD = "hazard"
    CHECKPOINT = "checkpoint"
    RALLY_POINT = "rally"
    STAGING = "staging"
    COMMAND_POST = "command"


# Zone type colors for map display
_ZONE_COLORS: Dict[str, str] = {
    'hazard': '#FF0000',        # Red
    'exclusion': '#FF4500',     # Orange-red
    'operations': '#0000FF',    # Blue
    'staging': '#00FF00',       # Green
    'safe': '#00AA00',          # Dark green
    '': '#808080',              # Gray (default)
}

# Marker icons and colors
_MARKER_STYLES: Dict[str, Dict[str, str]] = {
    'incident': {'icon': 'exclamation-triangle', 'color': 'red'},
    'resource': {'icon': 'cube', 'color': 'green'},
    'hazard': {'icon': 'warning', 'color': 'orange'},
    'checkpoint': {'icon': 'flag', 'color': 'blue'},
    'rally': {'icon': 'users', 'color': 'purple'},
    'staging': {'icon': 'archive', 'color': 'darkgreen'},
    'command': {'icon': 'star', 'color': 'darkblue'},
}

# Check-in status colors
_CHECKIN_COLORS: Dict[str, str] = {
    'ok': 'green',
    'needs_help': 'orange',
    'injured': 'red',
    'evacuating': 'darkred',
}


def is_map_available() -> bool:
    """Check if Folium map generation is available."""
    return _HAS_FOLIUM


def add_zones_to_map(folium_map: Any, zones: List[ZoneMarking]) -> None:
    """Add tactical zone overlays to a Folium map.

    Renders circles for zones with radius, polygons for zones with vertices.

    Args:
        folium_map: Folium Map object.
        zones: List of ZoneMarking objects.
    """
    if not _HAS_FOLIUM:
        return

    import folium

    for zone in zones:
        color = _ZONE_COLORS.get(zone.zone_type, _ZONE_COLORS[''])

        tooltip = f"{zone.name} ({zone.zone_type})" if zone.name else zone.zone_type

        if zone.is_polygon() and zone.vertices:
            # Polygon zone
            folium.Polygon(
                locations=[(lat, lon) for lat, lon in zone.vertices],
                color=color,
                fill=True,
                fill_opacity=0.2,
                weight=2,
                tooltip=tooltip,
            ).add_to(folium_map)

        elif zone.is_circle():
            # Circle zone
            folium.Circle(
                location=(zone.center_lat, zone.center_lon),
                radius=zone.radius_m,
                color=color,
                fill=True,
                fill_opacity=0.15,
                weight=2,
                tooltip=tooltip,
            ).add_to(folium_map)

        else:
            # Point marker (no radius, no polygon)
            folium.Marker(
                location=(zone.center_lat, zone.center_lon),
                tooltip=tooltip,
                icon=folium.Icon(color='gray', icon='map-pin', prefix='fa'),
            ).add_to(folium_map)


def add_checkins_to_map(folium_map: Any, checkins: List[CheckIn]) -> None:
    """Add check-in positions to a Folium map.

    Args:
        folium_map: Folium Map object.
        checkins: List of CheckIn objects (must have lat/lon).
    """
    if not _HAS_FOLIUM:
        return

    import folium

    for checkin in checkins:
        if checkin.latitude is None or checkin.longitude is None:
            continue

        color = _CHECKIN_COLORS.get(checkin.status, 'gray')
        tooltip = (
            f"{checkin.callsign} [{checkin.status}] "
            f"({checkin.personnel_count} personnel)"
        )

        folium.CircleMarker(
            location=(checkin.latitude, checkin.longitude),
            radius=8,
            color=color,
            fill=True,
            fill_opacity=0.7,
            tooltip=tooltip,
        ).add_to(folium_map)


def add_markers_to_map(
    folium_map: Any,
    markers: List[Dict[str, Any]],
) -> None:
    """Add tactical markers to a Folium map.

    Args:
        folium_map: Folium Map object.
        markers: List of dicts with keys: 'lat', 'lon', 'type', 'name'.
    """
    if not _HAS_FOLIUM:
        return

    import folium

    for marker in markers:
        lat = marker.get('lat', 0)
        lon = marker.get('lon', 0)
        marker_type = marker.get('type', '')
        name = marker.get('name', marker_type)

        style = _MARKER_STYLES.get(marker_type, {'icon': 'info-sign', 'color': 'gray'})

        folium.Marker(
            location=(lat, lon),
            tooltip=name,
            icon=folium.Icon(
                color=style['color'],
                icon=style['icon'],
                prefix='fa',
            ),
        ).add_to(folium_map)


def generate_tactical_map(
    zones: List[ZoneMarking],
    checkins: List[CheckIn],
    markers: List[Dict[str, Any]],
    output_path: Path,
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
    zoom_start: int = 13,
) -> Path:
    """Generate a standalone tactical map HTML file.

    Args:
        zones: Zone markings to overlay.
        checkins: Check-in positions to display.
        markers: Additional tactical markers.
        output_path: Path to write HTML file.
        center_lat: Map center latitude (auto-calculated if None).
        center_lon: Map center longitude (auto-calculated if None).
        zoom_start: Initial zoom level.

    Returns:
        Path to the generated HTML file.

    Raises:
        RuntimeError: If Folium is not available.
    """
    if not _HAS_FOLIUM:
        raise RuntimeError(
            "Tactical map generation requires 'folium' package. "
            "Install with: pip install folium"
        )

    import folium

    # Auto-calculate center from available data
    if center_lat is None or center_lon is None:
        all_points = _collect_points(zones, checkins, markers)
        if all_points:
            center_lat = sum(p[0] for p in all_points) / len(all_points)
            center_lon = sum(p[1] for p in all_points) / len(all_points)
        else:
            center_lat = center_lat or 0.0
            center_lon = center_lon or 0.0

    # Create map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom_start,
        tiles='OpenStreetMap',
    )

    # Add overlays
    add_zones_to_map(m, zones)
    add_checkins_to_map(m, checkins)
    add_markers_to_map(m, markers)

    # Save
    m.save(str(output_path))
    logger.info(f"Tactical map saved to {output_path}")
    return output_path


# ============================================================================
# KML Export
# ============================================================================


def export_kml(
    zones: List[ZoneMarking],
    markers: List[Dict[str, Any]],
    output_path: Path,
    document_name: str = "MeshForge Tactical",
) -> Path:
    """Export tactical data as KML for Google Earth / ATAK.

    Args:
        zones: Zone markings.
        markers: Tactical markers.
        output_path: Path to write KML file.
        document_name: KML document name.

    Returns:
        Path to the generated KML file.
    """
    kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
    doc = ET.SubElement(kml, 'Document')
    ET.SubElement(doc, 'name').text = document_name

    # Add zone styles
    for zone_type, color in _ZONE_COLORS.items():
        if not zone_type:
            continue
        style = ET.SubElement(doc, 'Style', id=f'zone_{zone_type}')
        line_style = ET.SubElement(style, 'LineStyle')
        ET.SubElement(line_style, 'color').text = _hex_to_kml_color(color, alpha='ff')
        ET.SubElement(line_style, 'width').text = '2'
        poly_style = ET.SubElement(style, 'PolyStyle')
        ET.SubElement(poly_style, 'color').text = _hex_to_kml_color(color, alpha='40')

    # Add zones
    if zones:
        zones_folder = ET.SubElement(doc, 'Folder')
        ET.SubElement(zones_folder, 'name').text = 'Zones'

        for zone in zones:
            placemark = ET.SubElement(zones_folder, 'Placemark')
            ET.SubElement(placemark, 'name').text = zone.name or zone.zone_type

            style_url = f'#zone_{zone.zone_type}' if zone.zone_type else '#zone_'
            ET.SubElement(placemark, 'styleUrl').text = style_url

            if zone.is_polygon() and zone.vertices:
                polygon = ET.SubElement(placemark, 'Polygon')
                outer = ET.SubElement(polygon, 'outerBoundaryIs')
                ring = ET.SubElement(outer, 'LinearRing')
                coords = ' '.join(
                    f'{lon},{lat},0' for lat, lon in zone.vertices
                )
                # Close the polygon
                if zone.vertices:
                    first = zone.vertices[0]
                    coords += f' {first[1]},{first[0]},0'
                ET.SubElement(ring, 'coordinates').text = coords
            else:
                # Point marker for circles (KML doesn't natively support circles)
                point = ET.SubElement(placemark, 'Point')
                ET.SubElement(point, 'coordinates').text = (
                    f'{zone.center_lon},{zone.center_lat},0'
                )
                if zone.radius_m > 0:
                    desc = f"Circle radius: {zone.radius_m:.0f}m"
                    ET.SubElement(placemark, 'description').text = desc

    # Add markers
    if markers:
        markers_folder = ET.SubElement(doc, 'Folder')
        ET.SubElement(markers_folder, 'name').text = 'Markers'

        for marker in markers:
            placemark = ET.SubElement(markers_folder, 'Placemark')
            ET.SubElement(placemark, 'name').text = marker.get('name', '')
            point = ET.SubElement(placemark, 'Point')
            ET.SubElement(point, 'coordinates').text = (
                f"{marker.get('lon', 0)},{marker.get('lat', 0)},0"
            )

    # Write file
    tree = ET.ElementTree(kml)
    ET.indent(tree, space='  ')
    tree.write(str(output_path), xml_declaration=True, encoding='UTF-8')

    logger.info(f"KML exported to {output_path}")
    return output_path


# ============================================================================
# Cursor-on-Target (CoT) XML Export
# ============================================================================


def export_cot_xml(
    checkins: List[CheckIn],
    output_path: Path,
) -> Path:
    """Export check-ins as Cursor-on-Target (CoT) XML for the TAK ecosystem.

    CoT is the standard data format for ATAK (Android Team Awareness Kit),
    WinTAK, and other TAK applications.

    Args:
        checkins: List of CheckIn objects with position data.
        output_path: Path to write CoT XML file.

    Returns:
        Path to the generated XML file.
    """
    root = ET.Element('events')

    now = datetime.now(timezone.utc)
    stale = now.replace(hour=now.hour + 1) if now.hour < 23 else now

    for checkin in checkins:
        if checkin.latitude is None or checkin.longitude is None:
            continue

        event_uid = f"meshforge-{uuid.uuid4().hex[:12]}"

        # CoT event element
        event = ET.SubElement(root, 'event')
        event.set('version', '2.0')
        event.set('uid', event_uid)
        event.set('type', _checkin_status_to_cot_type(checkin.status))
        event.set('how', 'm-g')  # machine-generated
        event.set('time', now.strftime('%Y-%m-%dT%H:%M:%SZ'))
        event.set('start', now.strftime('%Y-%m-%dT%H:%M:%SZ'))
        event.set('stale', stale.strftime('%Y-%m-%dT%H:%M:%SZ'))

        # Point element
        point = ET.SubElement(event, 'point')
        point.set('lat', str(checkin.latitude))
        point.set('lon', str(checkin.longitude))
        point.set('hae', str(checkin.altitude or 0))
        point.set('ce', '9999999')  # Circular error (unknown)
        point.set('le', '9999999')  # Linear error (unknown)

        # Detail element
        detail = ET.SubElement(event, 'detail')

        # Contact info
        contact = ET.SubElement(detail, 'contact')
        contact.set('callsign', checkin.callsign or 'Unknown')

        # Remarks
        remarks = ET.SubElement(detail, 'remarks')
        remarks.text = (
            f"Status: {checkin.status}, "
            f"Personnel: {checkin.personnel_count}"
        )
        if checkin.notes:
            remarks.text += f", Notes: {checkin.notes}"

    # Write file
    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(str(output_path), xml_declaration=True, encoding='UTF-8')

    logger.info(f"CoT XML exported to {output_path} ({len(checkins)} check-ins)")
    return output_path


# ============================================================================
# Helpers
# ============================================================================


def _collect_points(
    zones: List[ZoneMarking],
    checkins: List[CheckIn],
    markers: List[Dict[str, Any]],
) -> List[tuple]:
    """Collect all lat/lon points from zones, checkins, and markers."""
    points = []

    for zone in zones:
        if zone.center_lat and zone.center_lon:
            points.append((zone.center_lat, zone.center_lon))
        for lat, lon in zone.vertices:
            points.append((lat, lon))

    for checkin in checkins:
        if checkin.latitude is not None and checkin.longitude is not None:
            points.append((checkin.latitude, checkin.longitude))

    for marker in markers:
        lat = marker.get('lat')
        lon = marker.get('lon')
        if lat is not None and lon is not None:
            points.append((lat, lon))

    return points


def _hex_to_kml_color(hex_color: str, alpha: str = 'ff') -> str:
    """Convert hex color (#RRGGBB) to KML color (aaBBGGRR)."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return f'{alpha}808080'  # Default gray
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f'{alpha}{b}{g}{r}'


def _checkin_status_to_cot_type(status: str) -> str:
    """Map check-in status to CoT type string.

    CoT types follow the 2525B/C MILSTD symbology hierarchy.
    """
    mapping = {
        'ok': 'a-f-G-U-C',           # Friendly ground unit combat
        'needs_help': 'a-f-G-U-C',   # Friendly ground (flagged in remarks)
        'injured': 'a-f-G-U-H',      # Friendly ground unit medical
        'evacuating': 'a-f-G-U-H',   # Friendly ground unit medical
    }
    return mapping.get(status, 'a-f-G-U-C')

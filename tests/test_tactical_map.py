"""Tests for tactical map module (KML/CoT export — no Folium dependency)."""

import pytest
import xml.etree.ElementTree as ET
from pathlib import Path

from tactical.models import CheckIn, ZoneMarking
from tactical.tactical_map import (
    TacticalMarkerType,
    export_kml,
    export_cot_xml,
    _hex_to_kml_color,
    _checkin_status_to_cot_type,
    _collect_points,
)


class TestKMLExport:
    """Test KML export (stdlib XML, no optional deps needed)."""

    def test_export_zones(self, tmp_path):
        zones = [
            ZoneMarking(
                name="Staging A", zone_type="staging",
                center_lat=21.3, center_lon=-157.8, radius_m=200.0,
            ),
            ZoneMarking(
                name="Hazard Zone", zone_type="hazard",
                center_lat=21.31, center_lon=-157.79, radius_m=100.0,
            ),
        ]

        output = tmp_path / "test.kml"
        result = export_kml(zones, [], output)
        assert result == output
        assert output.exists()

        # Parse the KML
        tree = ET.parse(str(output))
        root = tree.getroot()
        # KML namespace
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        placemarks = root.findall('.//kml:Placemark', ns)
        assert len(placemarks) == 2

    def test_export_polygon_zone(self, tmp_path):
        zones = [
            ZoneMarking(
                name="Polygon Zone",
                zone_type="exclusion",
                vertices=[(21.3, -157.8), (21.31, -157.79), (21.3, -157.79)],
            ),
        ]

        output = tmp_path / "polygon.kml"
        export_kml(zones, [], output)

        tree = ET.parse(str(output))
        root = tree.getroot()
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        polygons = root.findall('.//kml:Polygon', ns)
        assert len(polygons) == 1

    def test_export_markers(self, tmp_path):
        markers = [
            {'name': 'Command Post', 'lat': 21.3, 'lon': -157.8, 'type': 'command'},
        ]

        output = tmp_path / "markers.kml"
        export_kml([], markers, output)

        tree = ET.parse(str(output))
        root = tree.getroot()
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        placemarks = root.findall('.//kml:Placemark', ns)
        assert len(placemarks) == 1

    def test_export_empty(self, tmp_path):
        output = tmp_path / "empty.kml"
        export_kml([], [], output)
        assert output.exists()


class TestCoTExport:
    """Test Cursor-on-Target XML export."""

    def test_export_checkins(self, tmp_path):
        checkins = [
            CheckIn(
                callsign="WH6GXZ", status="ok",
                latitude=21.3, longitude=-157.8,
            ),
            CheckIn(
                callsign="KH6ABC", status="injured",
                latitude=21.31, longitude=-157.79, personnel_count=3,
            ),
        ]

        output = tmp_path / "test.xml"
        result = export_cot_xml(checkins, output)
        assert result == output
        assert output.exists()

        tree = ET.parse(str(output))
        root = tree.getroot()
        events = root.findall('event')
        assert len(events) == 2

        # Check first event
        event = events[0]
        assert event.get('type') is not None
        point = event.find('point')
        assert point is not None
        assert point.get('lat') == '21.3'

        contact = event.find('.//contact')
        assert contact is not None
        assert contact.get('callsign') == 'WH6GXZ'

    def test_export_skips_no_position(self, tmp_path):
        checkins = [
            CheckIn(callsign="N0POS", status="ok"),  # No lat/lon
        ]

        output = tmp_path / "nopos.xml"
        export_cot_xml(checkins, output)

        tree = ET.parse(str(output))
        events = tree.getroot().findall('event')
        assert len(events) == 0


class TestHelpers:
    """Test helper functions."""

    def test_hex_to_kml_color(self):
        # Red (#FF0000) → KML aaBBGGRR = ff0000FF
        assert _hex_to_kml_color('#FF0000') == 'ff0000FF'
        # Blue (#0000FF) → KML ff FF0000
        assert _hex_to_kml_color('#0000FF') == 'ffFF0000'
        # With alpha
        assert _hex_to_kml_color('#00FF00', alpha='40') == '4000FF00'

    def test_checkin_status_to_cot_type(self):
        assert _checkin_status_to_cot_type('ok').startswith('a-f-')
        assert _checkin_status_to_cot_type('injured').startswith('a-f-')

    def test_collect_points(self):
        zones = [ZoneMarking(center_lat=21.3, center_lon=-157.8)]
        checkins = [CheckIn(latitude=21.31, longitude=-157.79)]
        markers = [{'lat': 21.32, 'lon': -157.78}]

        points = _collect_points(zones, checkins, markers)
        assert len(points) == 3


class TestTacticalMarkerType:
    """Test marker type enum."""

    def test_all_types(self):
        assert TacticalMarkerType.INCIDENT.value == "incident"
        assert TacticalMarkerType.HAZARD.value == "hazard"
        assert TacticalMarkerType.COMMAND_POST.value == "command"

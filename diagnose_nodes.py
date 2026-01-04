#!/usr/bin/env python3
"""Quick diagnostic to compare node data sources"""
import sys
sys.path.insert(0, 'src')

try:
    from meshtastic.tcp_interface import TCPInterface
    import time

    print("Connecting to meshtasticd on localhost:4403...")
    interface = TCPInterface(hostname='localhost', portNumber=4403)

    # Wait and show node count growing
    print("\nWaiting for nodes to load (MQTT can take time)...")
    for i in range(10):
        time.sleep(1)
        count = len(interface.nodes or {})
        print(f"  {i+1}s: {count} nodes")

    print(f"\n=== Node Analysis after 10 seconds ===")
    print(f"Total nodes in interface.nodes: {len(interface.nodes or {})}")

    nodes_with_pos = 0
    nodes_without_pos = 0
    mqtt_nodes = 0

    for node_id, node in (interface.nodes or {}).items():
        pos = node.get('position', {})
        user = node.get('user', {})
        name = user.get('longName', user.get('shortName', str(node_id)))
        via_mqtt = node.get('viaMqtt', False)

        if via_mqtt:
            mqtt_nodes += 1

        # Check all position formats
        lat = pos.get('latitude')
        if lat is None:
            lat_i = pos.get('latitudeI')
            lat = lat_i / 1e7 if lat_i is not None else None

        lon = pos.get('longitude')
        if lon is None:
            lon_i = pos.get('longitudeI')
            lon = lon_i / 1e7 if lon_i is not None else None

        has_valid_pos = lat is not None and lon is not None and not (lat == 0 and lon == 0)

        if has_valid_pos:
            nodes_with_pos += 1
            mqtt_flag = " [MQTT]" if via_mqtt else ""
            print(f"  ✓ {name}: {lat:.4f}, {lon:.4f}{mqtt_flag}")
        else:
            nodes_without_pos += 1
            mqtt_flag = " [MQTT]" if via_mqtt else ""
            if pos:
                print(f"  ✗ {name}: NO VALID POS{mqtt_flag} - raw: {pos}")
            else:
                print(f"  ✗ {name}: NO POSITION DATA{mqtt_flag}")

    print(f"\n=== Summary ===")
    print(f"With valid position: {nodes_with_pos}")
    print(f"Without position: {nodes_without_pos}")
    print(f"Via MQTT: {mqtt_nodes}")
    print(f"Total: {nodes_with_pos + nodes_without_pos}")

    interface.close()

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

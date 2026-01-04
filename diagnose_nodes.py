#!/usr/bin/env python3
"""Quick diagnostic to compare node data sources"""
import sys
sys.path.insert(0, 'src')

try:
    from meshtastic.tcp_interface import TCPInterface
    import time
    
    print("Connecting to meshtasticd on localhost:4403...")
    interface = TCPInterface(hostname='localhost', portNumber=4403)
    time.sleep(3)  # Wait for nodes to load
    
    print(f"\n=== Raw Interface Data ===")
    print(f"Total nodes in interface.nodes: {len(interface.nodes or {})}")
    
    nodes_with_pos = 0
    nodes_without_pos = 0
    
    for node_id, node in (interface.nodes or {}).items():
        pos = node.get('position', {})
        user = node.get('user', {})
        name = user.get('longName', user.get('shortName', str(node_id)))
        
        lat = pos.get('latitude') or (pos.get('latitudeI', 0) / 1e7 if pos.get('latitudeI') else 0)
        lon = pos.get('longitude') or (pos.get('longitudeI', 0) / 1e7 if pos.get('longitudeI') else 0)
        
        if lat and lon and not (lat == 0 and lon == 0):
            nodes_with_pos += 1
            print(f"  ✓ {name}: {lat:.4f}, {lon:.4f}")
        else:
            nodes_without_pos += 1
            # Show raw position data for debugging
            if pos:
                print(f"  ✗ {name}: NO VALID POS - raw: {pos}")
            else:
                print(f"  ✗ {name}: NO POSITION DATA")
    
    print(f"\n=== Summary ===")
    print(f"With valid position: {nodes_with_pos}")
    print(f"Without position: {nodes_without_pos}")
    print(f"Total: {nodes_with_pos + nodes_without_pos}")
    
    interface.close()
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

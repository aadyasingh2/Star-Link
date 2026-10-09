import yaml
import sys
import os
import copy
import networkx as nx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.constellation import Constellation, TopologySeries

def analyze_cutoff(base_config, cutoff):
    config = copy.deepcopy(base_config)
    config['constellation']['isl_inter_plane_max_lat_deg'] = cutoff
    
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    
    dt = config['simulation']['timestep_s']
    duration = config['simulation']['duration_s']
    
    t = 0.0
    connected_steps = 0
    total_steps = 0
    
    pairs = [
        ("gs_Chennai", "gs_London"),
        ("gs_Chennai", "gs_Sydney"),
        ("gs_New York", "gs_Tokyo")
    ]
    
    path_exists = {p: 0 for p in pairs}
    path_delays = {p: [] for p in pairs}
    
    prev_isl_edges = set()
    total_isl_appear = 0
    total_isl_disappear = 0
    
    while t <= duration:
        g = ts.get_snapshot(t)
        total_steps += 1
        
        # 1. ISL Churn
        current_isl_edges = set()
        for u, v, d in g.edges(data=True):
            if d['link_type'] in ['intra', 'inter']:
                current_isl_edges.add(tuple(sorted([u, v])))
                
        if t > 0:
            total_isl_appear += len(current_isl_edges - prev_isl_edges)
            total_isl_disappear += len(prev_isl_edges - current_isl_edges)
        prev_isl_edges = current_isl_edges
        
        # 2. Connectivity
        sat_nodes = [n for n in g.nodes() if g.nodes[n]['type'] == 'satellite']
        sat_g = g.subgraph(sat_nodes)
        if nx.is_connected(sat_g):
            connected_steps += 1
            
        # 3. Path metrics
        for p in pairs:
            u, v = p
            if u in g and v in g and nx.has_path(g, u, v):
                path_exists[p] += 1
                delay = nx.shortest_path_length(g, u, v, weight='propagation_delay_ms')
                path_delays[p].append(delay)
                
        t += dt
        
    print(f"\n=== Cutoff: {cutoff if cutoff is not None else 'null'} ===")
    print(f"Total ISL Churn over {duration}s: {total_isl_appear} appeared, {total_isl_disappear} disappeared")
    print(f"Satellites Graph Connected Fraction: {connected_steps / total_steps:.2%}")
    for p in pairs:
        exist_frac = path_exists[p] / total_steps
        avg_delay = sum(path_delays[p]) / len(path_delays[p]) if path_delays[p] else float('inf')
        print(f"Path {p[0].split('_')[1]} -> {p[1].split('_')[1]}: Exists {exist_frac:.2%}, Mean Shortest Delay: {avg_delay:.2f} ms")

def main():
    with open(os.path.join(os.path.dirname(__file__), "..", "config.yaml"), "r") as f:
        base_config = yaml.safe_load(f)
        
    print("Running Cutoff Analysis...")
    for cutoff in [None, 50, 45, 40]:
        analyze_cutoff(base_config, cutoff)

if __name__ == "__main__":
    main()

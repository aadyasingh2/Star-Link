import yaml
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.constellation import Constellation, TopologySeries

def main():
    with open(os.path.join(os.path.dirname(__file__), "..", "config.yaml"), "r") as f:
        config = yaml.safe_load(f)
        
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    
    dt = config['simulation']['timestep_s']
    duration = config['simulation']['duration_s']
    
    print(f"Simulating link churn over {duration} seconds with dt={dt}...")
    t = 0.0
    prev_edges = set()
    
    minute_new = 0
    minute_lost = 0
    current_minute = 0
    
    while t <= duration:
        g = ts.get_snapshot(t)
        
        current_edges = set()
        for u, v, d in g.edges(data=True):
            if d['link_type'] in ['intra', 'inter']:
                edge = tuple(sorted([u, v]))
                current_edges.add(edge)
                
        if t > 0:
            new_links = current_edges - prev_edges
            lost_links = prev_edges - current_edges
            
            minute_new += len(new_links)
            minute_lost += len(lost_links)
            
            if int(t) % 60 == 0:
                print(f"Minute {current_minute+1:02d} (t={int(t):04d}s): {minute_new} links appeared, {minute_lost} links disappeared")
                current_minute += 1
                minute_new = 0
                minute_lost = 0
                
        prev_edges = current_edges
        t += dt

if __name__ == "__main__":
    main()

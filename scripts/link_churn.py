import yaml
import sys
import os
from collections import defaultdict

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
    
    # Tracking ISLs
    prev_isl_edges = set()
    minute_isl_new = 0
    minute_isl_lost = 0
    
    # Tracking GSLs
    prev_gsl_edges = defaultdict(set) # gs_name -> set of sat names
    minute_gsl_new = defaultdict(int)
    minute_gsl_lost = defaultdict(int)
    
    # For average visibility duration
    gsl_start_time = {} # (gs_name, sat_name) -> start_time
    gsl_durations = [] # list of durations in seconds
    
    current_minute = 0
    
    while t <= duration:
        g = ts.get_snapshot(t)
        
        current_isl_edges = set()
        current_gsl_edges = defaultdict(set)
        
        for u, v, d in g.edges(data=True):
            if d['link_type'] in ['intra', 'inter']:
                edge = tuple(sorted([u, v]))
                current_isl_edges.add(edge)
            elif d['link_type'] == 'gsl':
                # Identify which is gs and which is sat
                gs = u if g.nodes[u]['type'] == 'ground_station' else v
                sat = v if gs == u else u
                current_gsl_edges[gs].add(sat)
                
                # Track start time
                if (gs, sat) not in gsl_start_time:
                    gsl_start_time[(gs, sat)] = t
                    
        # Check for ended GSLs
        for (gs, sat), start_t in list(gsl_start_time.items()):
            if sat not in current_gsl_edges.get(gs, set()):
                gsl_durations.append(t - start_t)
                del gsl_start_time[(gs, sat)]
                
        if t > 0:
            # ISL churn
            new_isl = current_isl_edges - prev_isl_edges
            lost_isl = prev_isl_edges - current_isl_edges
            minute_isl_new += len(new_isl)
            minute_isl_lost += len(lost_isl)
            
            # GSL churn
            all_gs = set(list(prev_gsl_edges.keys()) + list(current_gsl_edges.keys()))
            for gs in all_gs:
                new_gsl = current_gsl_edges[gs] - prev_gsl_edges[gs]
                lost_gsl = prev_gsl_edges[gs] - current_gsl_edges[gs]
                minute_gsl_new[gs] += len(new_gsl)
                minute_gsl_lost[gs] += len(lost_gsl)
            
            if int(t) % 60 == 0:
                has_churn = minute_isl_new > 0 or minute_isl_lost > 0 or any(minute_gsl_new.values()) or any(minute_gsl_lost.values())
                if has_churn:
                    print(f"Minute {current_minute+1:02d} (t={int(t):04d}s):")
                    if minute_isl_new > 0 or minute_isl_lost > 0:
                        print(f"  ISL: {minute_isl_new} appeared, {minute_isl_lost} disappeared")
                    for gs in sorted(all_gs):
                        if minute_gsl_new[gs] > 0 or minute_gsl_lost[gs] > 0:
                            print(f"  {gs}: {minute_gsl_new[gs]} appeared, {minute_gsl_lost[gs]} disappeared")
                
                current_minute += 1
                minute_isl_new = 0
                minute_isl_lost = 0
                minute_gsl_new.clear()
                minute_gsl_lost.clear()
                
        prev_isl_edges = current_isl_edges
        prev_gsl_edges = current_gsl_edges
        t += dt

    # Add remaining active links to durations (assuming they end at duration)
    for (gs, sat), start_t in gsl_start_time.items():
        gsl_durations.append(duration - start_t)
        
    if gsl_durations:
        avg_visibility_s = sum(gsl_durations) / len(gsl_durations)
        print(f"\nAverage satellite visibility duration: {avg_visibility_s / 60:.2f} minutes")
    else:
        print("\nNo ground-to-satellite links established.")

if __name__ == "__main__":
    main()

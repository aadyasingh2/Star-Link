import yaml, sys, os, numpy as np
sys.path.insert(0, os.path.abspath('.'))
from src.constellation import Constellation, TopologySeries

with open('config.yaml') as f: config = yaml.safe_load(f)
constellation = Constellation(config)
ts = TopologySeries(config, constellation)
period = int(constellation.period_s)

intra = [e for e in ts.logical_isl if e[2] == 'intra']
inter = [e for e in ts.logical_isl if e[2] == 'inter']

min_intra = float('inf'); max_intra = 0.0
min_inter = float('inf'); max_inter = 0.0

for t in range(0, period, 10):
    pos = constellation.get_ecef_positions(t)
    # intra
    p1 = pos[[e[0] for e in intra]]
    p2 = pos[[e[1] for e in intra]]
    dists = np.linalg.norm(p2 - p1, axis=1)
    min_intra = min(min_intra, np.min(dists))
    max_intra = max(max_intra, np.max(dists))
    # inter
    p1 = pos[[e[0] for e in inter]]
    p2 = pos[[e[1] for e in inter]]
    dists = np.linalg.norm(p2 - p1, axis=1)
    min_inter = min(min_inter, np.min(dists))
    max_inter = max(max_inter, np.max(dists))
    
print(f'Intra: {min_intra:.2f} to {max_intra:.2f} km')
print(f'Inter: {min_inter:.2f} to {max_inter:.2f} km')

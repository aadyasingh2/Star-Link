import pytest
import yaml
import numpy as np
import os
import sys

# Ensure src can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.constellation import Constellation, TopologySeries

@pytest.fixture
def config():
    with open(os.path.join(os.path.dirname(__file__), "..", "config.yaml"), "r") as f:
        return yaml.safe_load(f)

def test_orbital_period(config):
    constellation = Constellation(config)
    a = 6371.0 + config['constellation']['altitude_km']
    mu = 398600.4418
    expected_t = 2 * np.pi * np.sqrt((a**3) / mu)
    assert np.isclose(constellation.period_s, expected_t)

def test_intra_plane_distances_constant(config):
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    
    g0 = ts.get_snapshot(0)
    g1000 = ts.get_snapshot(1000)
    
    for u, v, data in g0.edges(data=True):
        if data['link_type'] == 'intra':
            assert g1000.has_edge(u, v)
            d0 = data['distance_km']
            d1000 = g1000[u][v]['distance_km']
            assert np.isclose(d0, d1000, atol=1e-3)

def test_inter_plane_link_counts(config):
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    
    g0 = ts.get_snapshot(0)
    g1000 = ts.get_snapshot(1000)
    
    inter0 = sum(1 for _, _, d in g0.edges(data=True) if d['link_type'] == 'inter')
    inter1000 = sum(1 for _, _, d in g1000.edges(data=True) if d['link_type'] == 'inter')
    
    assert inter0 > 0
    assert inter1000 > 0

def test_ground_station_visibility(config):
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    g0 = ts.get_snapshot(0)
    
    gs_nodes = [n for n in g0.nodes() if g0.nodes[n]['type'] == 'ground_station']
    has_sat = 0
    for gs in gs_nodes:
        if len(list(g0.neighbors(gs))) > 0:
            has_sat += 1
    assert has_sat > 0

def test_no_link_crosses_seam(config):
    constellation = Constellation(config)
    ts = TopologySeries(config, constellation)
    g0 = ts.get_snapshot(0)
    
    P = config['constellation']['planes']
    S = config['constellation']['sats_per_plane']
    
    plane_0_sats = [f"sat_{s}" for s in range(S)]
    plane_last_sats = [f"sat_{((P-1)*S) + s}" for s in range(S)]
    
    for u in plane_0_sats:
        for v in plane_last_sats:
            assert not g0.has_edge(u, v)

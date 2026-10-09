import pytest
import networkx as nx
from src.routing.engine import RoutingEngine
from src.routing.link_state import LinkStateRouter
from src.routing.distance_vector import DistanceVectorRouter

class MockTopologySeries:
    def __init__(self, snapshots):
        self.snapshots = snapshots
    def get_snapshot(self, t):
        keys = sorted(self.snapshots.keys())
        best_t = keys[0]
        for k in keys:
            if k <= t:
                best_t = k
        return self.snapshots[best_t]

@pytest.fixture
def config():
    return {
        'routing': {
            'detection_delay_s': 0.1,
            'lsa_processing_delay_ms': 1.0,
            'spf_holddown_s': 1.0,
            'dv_update_interval_s': 30.0,
            'dv_infinity': 10000.0
        },
        'simulation': {'random_seed': 42}
    }

def test_link_state_zero_delay(config):
    config['routing']['detection_delay_s'] = 0.0
    config['routing']['lsa_processing_delay_ms'] = 0.0
    config['routing']['spf_holddown_s'] = 0.0
    
    g = nx.Graph()
    edges = [
        ('A', 'B', 10.0), ('B', 'C', 10.0), ('A', 'C', 30.0),
        ('C', 'D', 10.0), ('B', 'D', 15.0), ('D', 'E', 10.0)
    ]
    for u, v, d in edges:
        g.add_edge(u, v, propagation_delay_ms=d*1000, available=True)
        
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    
    path = lsr.compute_route('A', 'E', 0.0)
    assert path == ['A', 'B', 'D', 'E']
    assert lsr.control_message_count > 0

def test_dv_poison_reverse(config):
    config['routing']['zero_delay'] = True
    # A - B - C
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=10000, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=10000, available=True)
    
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=10000, available=True)
    
    ts = MockTopologySeries({0.0: g1, 10.0: g2})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    
    engine.initialize()
    engine.step_to(9.0)
    
    assert dvr.compute_route('A', 'C', 9.0) == ['A', 'B', 'C']
    
    engine.step_to(11.0)
    engine.drain_queue_until(30.0)
    
    assert dvr.compute_route('B', 'C', 30.0) is None
    assert dvr.control_message_count > 0

def test_stale_route_window(config):
    config['routing']['detection_delay_s'] = 5.0
    config['routing']['spf_holddown_s'] = 0.0
    config['routing']['lsa_processing_delay_ms'] = 0.0
    
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    
    g2 = nx.Graph()
    
    ts = MockTopologySeries({0.0: g1, 10.0: g2})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    
    engine.initialize()
    
    engine.step_to(11.0) 
    # Link down at 10.0, detection at 15.0, so at 11.0 route is stale but alive in router
    assert lsr.compute_route('A', 'B', 11.0) == ['A', 'B']
    
    engine.step_to(16.0) 
    assert lsr.compute_route('A', 'B', 16.0) is None

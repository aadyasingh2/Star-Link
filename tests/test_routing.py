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
    config['routing']['zero_delay'] = True
    
    g = nx.Graph()
    edges = [
        ('A', 'B', 10.0), ('B', 'C', 10.0), ('A', 'C', 30.0),
        ('C', 'D', 10.0), ('B', 'D', 15.0), ('D', 'E', 10.0)
    ]
    for u, v, d in edges:
        g.add_edge(u, v, propagation_delay_ms=d, available=True)
        
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    
    path = lsr.compute_route('A', 'E', 0.0)
    assert path == ['A', 'B', 'D', 'E']
    assert lsr.boot_message_count > 0

def test_dv_poison_reverse(config):
    config['routing']['zero_delay'] = True
    # A - B - C
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=10.0, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=10.0, available=True)
    
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=10.0, available=True)
    
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

def test_boot_messages(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    engine.drain_queue_until(5.0)
    assert dvr.boot_message_count > 0
    assert dvr.control_message_count == 0
    assert dvr.compute_route('A', 'B', 5.0) == ['A', 'B']

def test_dv_delivery_time(config):
    config['routing']['dv_processing_delay_ms'] = 2.0
    config['routing']['zero_delay'] = False
    g = nx.Graph()
    g.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    # Record time just before triggering a send from A
    send_time = engine.current_time
    dvr._send_updates('A')
    prop_s = 1.0  # 1000ms propagation
    proc_s = 0.002  # 2ms processing
    expected_delivery = send_time + prop_s + proc_s
    # Find the _receive_update event in the queue (filter by function name)
    receive_evs = [ev for ev in engine._queue if hasattr(ev[2], '__name__') and ev[2].__name__ == '_receive_update']
    assert len(receive_evs) > 0, "No _receive_update event found"
    assert abs(receive_evs[0][0] - expected_delivery) < 1e-9

def test_dv_drop_in_flight(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()
    ts = MockTopologySeries({0.0: g1, 0.5: g2})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    engine.step_to(2.0)
    assert dvr.dropped_message_count > 0

def test_dv_gs_transit(config):
    g = nx.Graph()
    g.add_node('gs_1', type='ground_station')
    g.add_node('sat_1', type='satellite')
    g.add_node('sat_2', type='satellite')
    g.add_edge('sat_1', 'gs_1', propagation_delay_ms=10, available=True)
    g.add_edge('gs_1', 'sat_2', propagation_delay_ms=10, available=True)
    g.add_edge('sat_1', 'sat_2', propagation_delay_ms=100, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    engine.drain_queue_until(5.0)
    # path should be sat_1 -> sat_2 despite longer delay
    assert dvr.compute_route('sat_1', 'sat_2', 5.0) == ['sat_1', 'sat_2']

def test_dv_6_node_convergence(config):
    config['routing']['zero_delay'] = True
    g = nx.Graph()
    edges = [
        ('A', 'B', 10.0), ('B', 'C', 10.0), ('A', 'C', 30.0),
        ('C', 'D', 10.0), ('B', 'D', 15.0), ('D', 'E', 10.0),
        ('E', 'F', 10.0)
    ]
    for u, v, d in edges:
        g.add_edge(u, v, propagation_delay_ms=d, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    engine.drain_queue_until(5.0)
    assert dvr.compute_route('A', 'F', 5.0) == ['A', 'B', 'D', 'E', 'F']

def test_dv_stale_route_window(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    # Link B-C goes down at 10.0
    ts = MockTopologySeries({0.0: g1, 10.0: g2})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    engine.step_to(10.5)
    # B-C down at 10.0. B's DV immediately drops C. A's DV still has stale entry via B.
    # This is a black-hole: A's table says C reachable via B, but B has no route to C.
    # Assert the stale entry exists in A's table (not delivered via compute_route which follows hops):
    cost_to_c, nxt_to_c = dvr.dv.get('A', {}).get('C', (dvr.infinity, None))
    assert cost_to_c < dvr.infinity, "A should still have stale DV entry for C at 10.5"
    assert nxt_to_c == 'B'
    engine.drain_queue_until(200.0)
    cost_after, _ = dvr.dv.get('A', {}).get('C', (dvr.infinity, None))
    assert cost_after >= dvr.infinity

def test_dv_count_to_infinity(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=100, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=100, available=True)
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=100, available=True)
    
    # Without poison reverse
    config['routing']['dv_poison_reverse'] = False
    ts_no_pr = MockTopologySeries({0.0: g1, 10.0: g2})
    eng1 = RoutingEngine(config, ts_no_pr)
    dvr1 = DistanceVectorRouter(config)
    eng1.add_router('dvr', dvr1)
    eng1.initialize()
    eng1.step_to(11.0)          # trigger link B-C down at step 10
    eng1.drain_queue_until(300.0)
    msgs_no_pr = dvr1.control_message_count

    # With poison reverse
    config['routing']['dv_poison_reverse'] = True
    ts_pr = MockTopologySeries({0.0: g1, 10.0: g2})
    eng2 = RoutingEngine(config, ts_pr)
    dvr2 = DistanceVectorRouter(config)
    eng2.add_router('dvr', dvr2)
    eng2.initialize()
    eng2.step_to(11.0)          # trigger link B-C down at step 10
    eng2.drain_queue_until(300.0)
    msgs_pr = dvr2.control_message_count

    assert msgs_pr < msgs_no_pr

def test_ls_delivery_time(config):
    config['routing']['lsa_processing_delay_ms'] = 2.0
    config['routing']['zero_delay'] = False
    g = nx.Graph()
    g.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    send_time = engine.current_time
    lsr._local_change('A', send_time)
    prop_s = 1.0  # 1000ms
    proc_s = 0.002  # 2ms
    expected_delivery = send_time + prop_s + proc_s
    receive_evs = [ev for ev in engine._queue if hasattr(ev[2], '__name__') and ev[2].__name__ == '_receive_lsa']
    assert len(receive_evs) > 0
    assert abs(receive_evs[0][0] - expected_delivery) < 1e-9

def test_ls_drop_in_flight(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()
    ts = MockTopologySeries({0.0: g1, 0.5: g2})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    engine.step_to(2.0)
    assert lsr.dropped_message_count > 0

def test_ls_gs_transit(config):
    g = nx.Graph()
    g.add_node('gs_1', type='ground_station')
    g.add_node('sat_1', type='satellite')
    g.add_node('sat_2', type='satellite')
    g.add_edge('sat_1', 'gs_1', propagation_delay_ms=10, available=True)
    g.add_edge('gs_1', 'sat_2', propagation_delay_ms=10, available=True)
    g.add_edge('sat_1', 'sat_2', propagation_delay_ms=100, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    engine.drain_queue_until(5.0)
    assert lsr.compute_route('sat_1', 'sat_2', 5.0) == ['sat_1', 'sat_2']

def test_ls_6_node_convergence(config):
    config['routing']['zero_delay'] = True
    g = nx.Graph()
    edges = [
        ('A', 'B', 10.0), ('B', 'C', 10.0), ('A', 'C', 30.0),
        ('C', 'D', 10.0), ('B', 'D', 15.0), ('D', 'E', 10.0),
        ('E', 'F', 10.0)
    ]
    for u, v, d in edges:
        g.add_edge(u, v, propagation_delay_ms=d*1000, available=True)
    ts = MockTopologySeries({0.0: g})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    engine.drain_queue_until(5.0)
    assert lsr.compute_route('A', 'F', 5.0) == ['A', 'B', 'D', 'E', 'F']

def test_ls_stale_route_window_nonzero(config):
    config['routing']['zero_delay'] = False
    config['routing']['lsa_processing_delay_ms'] = 1.0
    config['routing']['detection_delay_s'] = 0.0  # instantaneous detection
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g1, 10.0: g2})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    engine.step_to(10.5)
    # B detects B-C down at 10.0 and floods. LSA reaches A at ~11.0.
    # At 10.5, A still has the old LSA.
    assert lsr.compute_route('A', 'C', 10.5) == ['A', 'B', 'C']
    engine.drain_queue_until(25.0)
    assert lsr.compute_route('A', 'C', 25.0) is None

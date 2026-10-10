import pytest
import networkx as nx
import random
import time
import yaml
from pathlib import Path
from src.routing.engine import RoutingEngine
from src.routing.link_state import LinkStateRouter
from src.routing.distance_vector import DistanceVectorRouter
from src.constellation import Constellation, TopologySeries

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
            'dv_triggered_holdoff_s': 1.0,
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
    
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    
    engine.initialize()
    down_step = int(engine.current_time) + 10
    ts.snapshots[float(down_step)] = g2
    engine.step_to(down_step - 1.0)
    
    assert dvr.compute_route('A', 'C', down_step - 1.0) == ['A', 'B', 'C']
    
    engine.step_to(down_step + 1.0)
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
    
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    
    engine.initialize()
    down_step = int(engine.current_time) + 10
    ts.snapshots[float(down_step)] = g2
    
    engine.step_to(down_step + 1.0)
    # Detection is delayed by 5 seconds, so the route is still stale and alive.
    assert lsr.compute_route('A', 'B', down_step + 1.0) == ['A', 'B']
    
    engine.step_to(down_step + 6.0)
    assert lsr.compute_route('A', 'B', down_step + 6.0) is None

def test_boot_messages(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    assert dvr.boot_message_count > 0
    assert dvr.control_message_count == 0
    engine.drain_queue_until(5.0)
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
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    down_step = int(engine.current_time) + 2
    ts.snapshots[float(down_step)] = g2
    engine.step_to(down_step + 3.0)
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

def test_dv_triggered_holdoff_reduces_boot_events(config):
    graph = nx.Graph()
    for index in range(5):
        graph.add_edge(
            f'N{index}', f'N{index + 1}',
            propagation_delay_ms=10.0, available=True,
        )
    results = []
    for holdoff in (1.0, 0.0):
        run_config = {
            'routing': dict(config['routing']),
            'simulation': dict(config['simulation']),
        }
        run_config['routing']['zero_delay'] = True
        run_config['routing']['dv_triggered_holdoff_s'] = holdoff
        engine = RoutingEngine(run_config, MockTopologySeries({0.0: graph}))
        dvr = DistanceVectorRouter(run_config)
        engine.add_router('dvr', dvr)
        engine.initialize()
        expected = nx.shortest_path(
            graph, 'N0', 'N5', weight='propagation_delay_ms'
        )
        assert dvr.compute_route('N0', 'N5', 0.0) == expected
        results.append((dvr.boot_message_count, engine.events_processed))

    assert results[0][0] < results[1][0]
    assert results[0][1] < results[1][1]

def test_dv_stale_route_window(config):
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()
    g2.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    # Link B-C goes down at 10.0
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    dvr = DistanceVectorRouter(config)
    engine.add_router('dvr', dvr)
    engine.initialize()
    down_step = int(engine.current_time) + 10
    ts.snapshots[float(down_step)] = g2
    engine.step_to(down_step + 0.5)
    # B's DV immediately drops C. A's DV still has stale entry via B.
    # This is a black-hole: A's table says C reachable via B, but B has no route to C.
    # Assert the stale entry exists in A's table (not delivered via compute_route which follows hops):
    cost_to_c, nxt_to_c = dvr.dv.get('A', {}).get('C', (dvr.infinity, None))
    assert cost_to_c < dvr.infinity, f"A should still have stale DV entry for C at {down_step + 0.5}"
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
    # C floods at t0; B receives at about t0+1 (A-B still up) and forwards to A, arriving at about t0+2, which is the step where A-B is gone, so the message is in flight when the link dies and is dropped at delivery.
    g1 = nx.Graph()
    g1.add_edge('A', 'B', propagation_delay_ms=1000, available=True)
    g1.add_edge('B', 'C', propagation_delay_ms=1000, available=True)
    g2 = nx.Graph()  # A-B gone, B-C still up
    g2.add_edge('B', 'C', propagation_delay_ms=1000, available=True)
    ts = MockTopologySeries({0.0: g1})
    engine = RoutingEngine(config, ts)
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()
    t0 = engine.current_time
    assert lsr.dropped_message_count == 0
    down_step = int(t0) + 2
    ts.snapshots[float(down_step)] = g2
    lsr._local_change('C', t0)
    dropped_before = lsr.dropped_message_count
    engine.step_to(down_step + 3.0)
    assert lsr.dropped_message_count > dropped_before

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
    assert lsr.compute_route('A', 'C', 0.0) == ['A', 'B', 'C']
    engine.step_to(10.5)
    # B detects B-C down at 10.0 and floods. LSA reaches A at ~11.0.
    # At 10.5, A still has the old LSA.
    assert lsr.compute_route('A', 'C', 10.5) == ['A', 'B', 'C']
    engine.drain_queue_until(25.0)
    assert lsr.compute_route('A', 'C', 25.0) is None

def test_ls_lazy_spf_waits_for_route_query(config):
    config['routing']['zero_delay'] = True
    graph = nx.Graph()
    graph.add_edge('A', 'B', propagation_delay_ms=5.0, available=True)
    engine = RoutingEngine(config, MockTopologySeries({0.0: graph}))
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()

    assert lsr.compute_route('A', 'B', 0.0) == ['A', 'B']
    runs_before_change = lsr.spf_runs
    origin_seq = lsr.seq_nums['A']['B']
    lsr._receive_lsa('A', 'B', origin_seq + 1, {'C': 2.0}, 'B')
    assert lsr.dirty['A']
    assert lsr.spf_runs == runs_before_change

def test_ls_lazy_spf_holddown_keeps_old_route_until_expiry(config):
    config['routing']['zero_delay'] = True
    config['routing']['spf_holddown_s'] = 2.0
    graph = nx.Graph()
    graph.add_edge('S', 'A', propagation_delay_ms=1.0, available=True)
    graph.add_edge('A', 'D', propagation_delay_ms=1.0, available=True)
    graph.add_edge('S', 'B', propagation_delay_ms=3.0, available=True)
    graph.add_edge('B', 'D', propagation_delay_ms=1.0, available=True)
    engine = RoutingEngine(config, MockTopologySeries({0.0: graph}))
    lsr = LinkStateRouter(config)
    engine.add_router('lsr', lsr)
    engine.initialize()

    assert lsr.compute_route('S', 'D', 0.0) == ['S', 'A', 'D']
    lsr.handle_link_down('S', 'A', 0.0)
    assert lsr.compute_route('S', 'D', 0.5) == ['S', 'A', 'D']
    engine.drain_queue_until(2.0)
    assert lsr.compute_route('S', 'D', 2.0) == ['S', 'B', 'D']

def test_ls_lazy_spf_matches_networkx_on_random_graphs(config):
    config['routing']['zero_delay'] = True
    config['routing']['spf_holddown_s'] = 0.0
    rng = random.Random(93751)
    for _ in range(20):
        graph = nx.Graph()
        nodes = [f'N{index}' for index in range(10)]
        graph.add_nodes_from(nodes)
        for index in range(1, len(nodes)):
            parent = rng.randrange(index)
            graph.add_edge(
                nodes[index], nodes[parent],
                propagation_delay_ms=rng.uniform(1.0, 100.0),
                available=True,
            )
        for left in range(len(nodes)):
            for right in range(left + 1, len(nodes)):
                if not graph.has_edge(nodes[left], nodes[right]) and rng.random() < 0.2:
                    graph.add_edge(
                        nodes[left], nodes[right],
                        propagation_delay_ms=rng.uniform(1.0, 100.0),
                        available=True,
                    )

        engine = RoutingEngine(config, MockTopologySeries({0.0: graph}))
        lsr = LinkStateRouter(config)
        engine.add_router('lsr', lsr)
        engine.initialize()
        for src in nodes:
            _, expected_paths = nx.single_source_dijkstra(
                graph, src, weight='propagation_delay_ms'
            )
            for dst in nodes:
                assert lsr.compute_route(src, dst, 0.0) == expected_paths[dst]

def test_boot_on_t0_topology_for_both_routers(config):
    config['routing']['zero_delay'] = True
    graph = nx.Graph()
    edges = [
        ('A', 'B', 2.0), ('B', 'C', 3.0), ('A', 'C', 9.0),
        ('C', 'D', 1.5), ('B', 'D', 5.0), ('D', 'E', 4.0)
    ]
    for u, v, delay_ms in edges:
        graph.add_edge(u, v, propagation_delay_ms=delay_ms, available=True)

    engine = RoutingEngine(config, MockTopologySeries({0.0: graph}))
    routers = [LinkStateRouter(config), DistanceVectorRouter(config)]
    for name, router in zip(('lsr', 'dvr'), routers):
        engine.add_router(name, router)
    engine.initialize()

    assert engine.current_time == 0.0
    for router in routers:
        assert router.boot_message_count > 0
        assert router.control_message_count == 0
        for src in graph.nodes:
            for dst in graph.nodes:
                expected = nx.shortest_path(
                    graph, src, dst, weight='propagation_delay_ms'
                )
                assert router.compute_route(src, dst, 0.0) == expected

@pytest.mark.slow
def test_real_constellation_routes_after_boot(config):
    config_path = Path(__file__).resolve().parents[1] / 'config.yaml'
    with config_path.open(encoding='utf-8') as config_file:
        real_config = yaml.safe_load(config_file)
    assert real_config['constellation']['isl_inter_plane_max_lat_deg'] == 45
    constellation = Constellation(real_config)
    topology = TopologySeries(real_config, constellation)
    engine = RoutingEngine(real_config, topology)
    routers = [LinkStateRouter(real_config), DistanceVectorRouter(real_config)]
    for name, router in zip(('lsr', 'dvr'), routers):
        engine.add_router(name, router)

    boot_started = time.perf_counter()
    engine.initialize()
    boot_runtime = time.perf_counter() - boot_started
    print(f'Real constellation boot runtime: {boot_runtime:.3f} seconds')
    assert boot_runtime <= 120.0
    engine.step_to(60.0)

    snapshot = topology.get_snapshot(60.0)
    src, dst = 'gs_Chennai', 'gs_London'
    for router in routers:
        path = router.compute_route(src, dst, 60.0)
        assert path is not None
        assert path[0] == src
        assert path[-1] == dst
        assert all(snapshot.has_edge(u, v) for u, v in zip(path, path[1:]))

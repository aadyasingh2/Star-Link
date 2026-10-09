import pytest
from src.routing.engine import RoutingEngine

class DummyRouter:
    def __init__(self):
        self.events = []
        self.engine = None
    def attach_engine(self, engine):
        self.engine = engine
    def handle_link_up(self, u, v, delay, t):
        self.events.append(('up', u, v, t))
    def handle_link_down(self, u, v, t):
        self.events.append(('down', u, v, t))


def test_event_order_and_cancellation():
    cfg = {'routing': {'detection_delay_s': 0.1}}
    # Minimal topology_series stub with required method
    class StubTS:
        def get_snapshot(self, t):
            # Return a graph with no edges for simplicity
            import networkx as nx
            return nx.DiGraph()
    ts = StubTS()
    router = DummyRouter()
    engine = RoutingEngine(cfg, ts, [router])
    # Schedule two events at same time
    calls = []
    def f1():
        calls.append('first')
    def f2():
        calls.append('second')
    engine.schedule(0.5, f1)
    engine.schedule(0.5, f2)
    engine.run_until(0.5)
    assert calls == ['first', 'second']
    # Test cancellation
    calls.clear()
    timer = engine.schedule(0.4, f1)
    engine.cancel(timer)
    engine.run_until(1.0)
    assert calls == []

def test_subsecond_events_inside_step():
    cfg = {'routing': {'detection_delay_s': 0.2}}
    class StubTS:
        def __init__(self):
            self.snapshots = []
        def get_snapshot(self, t):
            import networkx as nx
            g = nx.DiGraph()
            # add a dummy edge at t=1 only
            if t == 1:
                g.add_edge('A', 'B', propagation_delay_s=0.05)
            return g
    ts = StubTS()
    router = DummyRouter()
    engine = RoutingEngine(cfg, ts, [router])
    # Step 0->1: generate events, detection delay 0.2 => event at t=1.2 (outside step)
    engine.generate_events_for_step(1)
    # advance engine to end of step 1.0 (no events should fire yet)
    engine.step(1.0)
    assert router.events == []
    # advance to 1.3, detection should have fired
    engine.step(1.3)
    assert router.events == [('up', 'A', 'B', 1)]

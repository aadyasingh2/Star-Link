import heapq
import itertools

class RoutingEngine:
    """Custom heapq based Routing Engine.
    The engine steps through a TopologySeries snapshot at 1‑s intervals.
    For each step it generates link‑up/down events by comparing snapshot t‑1 and t
    and schedules a detection timer (`routing.detection_delay_s`).
    Routers receive events only after the detection delay, never looking ahead.
    The engine provides a generic `schedule`/`cancel` API used by routers for
    LSA flooding, SPF hold‑down, and DV periodic/triggered updates.
    """

    def __init__(self, config, topology_series, routers=None):
        self.config = config
        self.topology_series = topology_series
        self.routers = routers if routers is not None else []
        self.current_time = 0.0
        self._queue = []  # (event_time, seq_id, func, args, timer_id)
        self._counter = itertools.count()
        self._cancelled = set()
        # Attach engine to each router
        for r in self.routers:
            r.attach_engine(self)

    # ---------------------------------------------------------------------
    # Scheduler API used by routers
    # ---------------------------------------------------------------------
    def schedule(self, delay, func, *args):
        """Schedule ``func(*args)`` to run after ``delay`` seconds.
        Returns a ``timer_id`` that can be cancelled via ``cancel``.
        """
        ev_time = self.current_time + delay
        seq = next(self._counter)
        timer_id = (ev_time, seq)
        heapq.heappush(self._queue, (ev_time, seq, func, args, timer_id))
        return timer_id

    def cancel(self, timer_id):
        """Cancel a previously scheduled timer.
        The actual heap entry is left in place and ignored when popped.
        """
        self._cancelled.add(timer_id)

    # ---------------------------------------------------------------------
    # Step execution – processes all events whose timestamp falls within the
    # current 1‑second simulation step, preserving sub‑second ordering.
    # ---------------------------------------------------------------------
    def run_until(self, target_time):
        while self._queue and self._queue[0][0] <= target_time:
            ev_time, seq, func, args, timer_id = heapq.heappop(self._queue)
            if timer_id in self._cancelled:
                self._cancelled.remove(timer_id)
                continue
            self.current_time = ev_time
            func(*args)
        self.current_time = target_time

    def drain_queue_until(self, t):
        """Process any pending events up to time ``t`` without generating new events.
        Used by tests to advance the engine after scheduled timers.
        """
        self.run_until(t)
    def step(self, step_time):
        """Process events up to the given integer ``step_time``.
        Used internally by ``step_to`` to advance the engine through each
        integer snapshot without generating new link events. It simply runs
        the scheduler until ``step_time``.
        """
        self.run_until(step_time)

    # ---------------------------------------------------------------------
    # New method for absolute‑time scheduling
    # ---------------------------------------------------------------------
    def schedule_at(self, abs_time, func, *args):
        """Schedule ``func(*args)`` to run at the exact ``abs_time``.
        Used when the engine must schedule events relative to a snapshot time
        rather than the engine's current_time.
        """
        seq = next(self._counter)
        timer_id = (abs_time, seq)
        heapq.heappush(self._queue, (abs_time, seq, func, args, timer_id))
        return timer_id

    # ---------------------------------------------------------------------
    # Event generation – compare snapshot t‑1 and t and schedule detection.
    # ---------------------------------------------------------------------
    def generate_events_for_step(self, t):
        """Generate link up/down events for step ``t``.
        Returns a list of event tuples for debugging (u, v, type, delay).
        The routers are notified *after* the detection delay.
        """
        if t == 0:
            return []  # boot handled separately
        prev = self.topology_series.get_snapshot(t - 1)
        curr = self.topology_series.get_snapshot(t)
        # Extract propagation delays, supporting both seconds and milliseconds keys
        prev_edges = {}
        for u, v, d in prev.edges(data=True):
            if "propagation_delay_s" in d:
                delay = d["propagation_delay_s"]
            elif "propagation_delay_ms" in d:
                delay = d["propagation_delay_ms"] / 1000.0
            else:
                raise KeyError("Missing propagation delay attribute")
            prev_edges[(u, v)] = delay
        curr_edges = {}
        for u, v, d in curr.edges(data=True):
            if "propagation_delay_s" in d:
                delay = d["propagation_delay_s"]
            elif "propagation_delay_ms" in d:
                delay = d["propagation_delay_ms"] / 1000.0
            else:
                raise KeyError("Missing propagation delay attribute")
            curr_edges[(u, v)] = delay
        events = []
        # Up events
        for (u, v), delay in curr_edges.items():
            if (u, v) not in prev_edges:
                events.append((u, v, "up", delay))
        # Down events
        for (u, v) in prev_edges:
            if (u, v) not in curr_edges:
                events.append((u, v, "down", None))
        # Schedule detection for each router using absolute time t
        detection = self.config["routing"]["detection_delay_s"]
        for u, v, typ, delay in events:
            if typ == "up":
                for r in self.routers:
                    self.schedule_at(t + detection, r.handle_link_up, u, v, delay, t)
            else:
                for r in self.routers:
                    self.schedule_at(t + detection, r.handle_link_down, u, v, t)
        return events

    # Router management ---------------------------------------------------
    def add_router(self, name, router):
        """Add a router instance to the engine.
        ``name`` is retained for potential future use.
        """
        self.routers.append(router)
        router.attach_engine(self)

    def initialize(self):
        """Load the initial topology (snapshot at time 0) into routers.
        This ensures routers have a complete view before any events.
        """
        snapshot = self.topology_series.get_snapshot(0)
        for u, v, d in snapshot.edges(data=True):
            if "propagation_delay_s" in d:
                delay = d["propagation_delay_s"]
            elif "propagation_delay_ms" in d:
                delay = d["propagation_delay_ms"] / 1000.0
            else:
                raise KeyError("Missing propagation delay attribute")
            for r in self.routers:
                r.handle_link_up(u, v, delay, 0)
        # Process any immediate scheduled events (e.g., SPF runs, DV updates)
        self.run_until(0)

    def link_delay(self, u, v, t):
        """Return the propagation delay (in seconds) of link (u, v) in the
        snapshot at ``floor(t)``, or ``None`` if the link does not exist.
        The topology is undirected so (u, v) or (v, u) is checked.
        """
        import math
        snapshot = self.topology_series.get_snapshot(int(math.floor(t)))
        for a, b in [(u, v), (v, u)]:
            if snapshot.has_edge(a, b):
                d = snapshot[a][b]
                if "propagation_delay_s" in d:
                    return d["propagation_delay_s"]
                elif "propagation_delay_ms" in d:
                    return d["propagation_delay_ms"] / 1000.0
                else:
                    raise KeyError("Missing propagation delay attribute")
        return None

    def link_is_up(self, u, v, t):
        """Return True if link (u, v) exists in snapshot at time t."""
        return self.link_delay(u, v, t) is not None

    def is_ground_station(self, node):
        """Return True if *node* is a ground station.
        Uses the node's ``type`` attribute set by the topology builder.
        Falls back to checking the ``gs_`` prefix if the snapshot has no
        node attributes (e.g. in unit tests with plain graphs).
        """
        snapshot = self.topology_series.get_snapshot(0)
        if snapshot.has_node(node):
            ntype = snapshot.nodes[node].get('type', '')
            if ntype:
                return ntype == 'ground_station'
        # Fallback: prefix convention used by topology.py
        return str(node).startswith('gs_')

    def step_to(self, target_time):
        """Step the engine from the current time up to ``target_time``.
        Generates link events for each integer snapshot and processes sub‑second
        timers.
        """
        import math
        start_int = int(math.floor(self.current_time))
        end_int = int(math.floor(target_time))
        for step in range(start_int + 1, end_int + 1):
            self.generate_events_for_step(step)
            self.step(step)
        # Process any remaining events up to the exact target_time
        self.run_until(target_time)

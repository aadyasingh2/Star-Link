import heapq
import itertools
import math

from .base import Router


class SAPRRouter(Router):
    """Predictive routing from locally computed orbital topology forecasts.

    Every node computes orbit predictions locally from ephemeris, so SAPR
    requires no routing signalling and keeps control-message counters at zero.
    If guard-time filtering disconnects a destination, path selection retries
    without the guard exclusion rather than returning no route.
    """

    def __init__(self, config):
        super().__init__(config)
        self.look_ahead_s = float(config['sapr']['look_ahead_window_s'])
        self.alpha = float(config['sapr']['alpha'])
        self.guard_time_s = float(config['sapr']['guard_time_s'])
        self.hysteresis_margin_ms = float(config['sapr']['hysteresis_margin_ms'])
        self.latency_bound_multiplier = float(
            config['sapr']['latency_bound_multiplier']
        )
        self.step_s = float(config.get('simulation', {}).get('timestep_s', 1.0))
        self.look_ahead_steps = max(0, math.ceil(self.look_ahead_s / self.step_s))
        self.reset()

    def reset(self):
        super().reset()
        self._lifetime_cache = {}
        self.current = {}
        self.last_failure = None

    def handle_link_up(self, u, v, delay, t):
        pass

    def handle_link_down(self, u, v, t):
        pass

    def handle_cost_change(self, u, v, delay, t):
        pass

    @staticmethod
    def _edge_key(u, v):
        return tuple(sorted((u, v), key=str))

    @staticmethod
    def _delay_ms(snapshot, u, v):
        data = snapshot[u][v]
        if 'propagation_delay_ms' in data:
            return float(data['propagation_delay_ms'])
        if 'propagation_delay_s' in data:
            return float(data['propagation_delay_s']) * 1000.0
        raise KeyError('Missing propagation delay attribute')

    def _remaining_lifetime(self, u, v, step):
        key = self._edge_key(u, v)
        cached = self._lifetime_cache.get(key)
        known_lifetime_steps = 0
        scan_start = step + 1
        if cached is not None:
            kind, bound = cached
            if kind == 'expires' and step < bound:
                return max(0, bound - step - 1) * self.step_s
            if kind == 'horizon' and step <= bound:
                known_lifetime_steps = min(
                    self.look_ahead_steps, bound - step
                )
                if known_lifetime_steps == self.look_ahead_steps:
                    return known_lifetime_steps * self.step_s
                scan_start = bound + 1

        horizon_step = step + self.look_ahead_steps
        for future_step in range(scan_start, horizon_step + 1):
            future = self.engine.topology_series.get_snapshot(future_step)
            if not future.has_edge(u, v):
                self._lifetime_cache[key] = ('expires', future_step)
                return (future_step - step - 1) * self.step_s
            known_lifetime_steps += 1
        self._lifetime_cache[key] = ('horizon', horizon_step)
        return known_lifetime_steps * self.step_s

    def _edge_cost(self, snapshot, u, v, step, enforce_guard):
        lifetime_s = self._remaining_lifetime(u, v, step)
        if enforce_guard and lifetime_s < self.guard_time_s:
            return None
        delay_ms = self._delay_ms(snapshot, u, v)
        penalty = max(0.0, 1.0 - lifetime_s / self.look_ahead_s)
        return delay_ms * (1.0 + self.alpha * penalty)

    def _shortest(self, snapshot, src, dst, step, predictive, enforce_guard):
        if not snapshot.has_node(src) or not snapshot.has_node(dst):
            return None, None
        if src == dst:
            return [src], 0.0
        distances = {src: 0.0}
        previous = {}
        sequence = itertools.count()
        queue = [(0.0, next(sequence), src)]
        while queue:
            distance, _, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            if node == dst:
                path = [dst]
                while path[-1] != src:
                    path.append(previous[path[-1]])
                path.reverse()
                return path, distance
            if (
                node != src
                and snapshot.nodes[node].get('type') == 'ground_station'
            ):
                continue
            for neighbor in sorted(snapshot.neighbors(node), key=str):
                if predictive:
                    edge_cost = self._edge_cost(
                        snapshot, node, neighbor, step, enforce_guard
                    )
                    if edge_cost is None:
                        continue
                else:
                    edge_cost = self._delay_ms(snapshot, node, neighbor)
                candidate = distance + edge_cost
                if candidate < distances.get(neighbor, float('inf')):
                    distances[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(
                        queue, (candidate, next(sequence), neighbor)
                    )
        return None, None

    def _actual_delay(self, snapshot, path):
        return sum(
            self._delay_ms(snapshot, u, v)
            for u, v in zip(path, path[1:])
        )

    def _valid_current_path(self, snapshot, path, src, dst, step):
        if not path or path[0] != src or path[-1] != dst:
            return False
        if any(not snapshot.has_edge(u, v) for u, v in zip(path, path[1:])):
            return False
        for node in path[1:-1]:
            if snapshot.nodes[node].get('type') == 'ground_station':
                return False
        return all(
            self._remaining_lifetime(u, v, step) >= self.guard_time_s
            for u, v in zip(path, path[1:])
        )

    def compute_route(self, src, dst, t):
        step = math.floor(t)
        snapshot = self.engine.topology_series.get_snapshot(step)
        shortest_path, shortest_delay = self._shortest(
            snapshot, src, dst, step, predictive=False, enforce_guard=False
        )
        if shortest_path is None:
            self.last_failure = 'no_route'
            self.current.pop((src, dst), None)
            return None

        best_path, best_cost = self._shortest(
            snapshot, src, dst, step, predictive=True, enforce_guard=True
        )
        if best_path is None:
            best_path, best_cost = self._shortest(
                snapshot, src, dst, step, predictive=True, enforce_guard=False
            )
        if best_path is None:
            self.last_failure = 'no_route'
            self.current.pop((src, dst), None)
            return None

        if (
            self._actual_delay(snapshot, best_path)
            > self.latency_bound_multiplier * shortest_delay
        ):
            best_path = shortest_path
            best_cost = sum(
                self._edge_cost(snapshot, u, v, step, enforce_guard=False)
                for u, v in zip(best_path, best_path[1:])
            )

        current_path = self.current.get((src, dst))
        if current_path and self._valid_current_path(
            snapshot, current_path, src, dst, step
        ):
            current_cost = sum(
                self._edge_cost(snapshot, u, v, step, enforce_guard=False)
                for u, v in zip(current_path, current_path[1:])
            )
            within_latency_bound = (
                self._actual_delay(snapshot, current_path)
                <= self.latency_bound_multiplier * shortest_delay
            )
            if (
                within_latency_bound
                and current_cost <= best_cost + self.hysteresis_margin_ms
            ):
                best_path = current_path

        self.current[(src, dst)] = list(best_path)
        self.last_failure = None
        return list(best_path)

    def compute_route_hop(self, node, dst, t):
        path = self.compute_route(node, dst, t)
        return path[1] if path is not None and len(path) > 1 else None

from collections import defaultdict
import random
from .base import Router

class DistanceVectorRouter(Router):
    def __init__(self, config):
        super().__init__(config)
        self.update_interval = config['routing']['dv_update_interval_s']
        self.infinity = config['routing']['dv_infinity']
        self.random = random.Random(config['simulation'].get('random_seed', 42))
        self.reset()

    def reset(self):
        super().reset()
        self.dv = defaultdict(dict)  # node -> dst -> (cost, next_hop)
        self.link_costs = defaultdict(dict)  # node -> neighbor -> cost
        self.neighbor_dvs = defaultdict(lambda: defaultdict(dict))  # node -> neighbor -> dst -> cost
        self.periodic_timers = {}
        self.active_nodes = set()

    def compute_route(self, src, dst, t):
        """Return full path from src to dst using DV table.
        Repeatedly follow next hops from compute_route_hop. Detect loops.
        If no route, return None.
        """
        if src == dst:
            return [src]
        path = [src]
        visited = {src}
        current = src
        while True:
            nxt = self.compute_route_hop(current, dst, t)
            if nxt is None:
                return None
            if nxt in visited:
                return None
            path.append(nxt)
            if nxt == dst:
                return path
            visited.add(nxt)
            current = nxt

    def compute_route_hop(self, node, dst, t):
        """Return next hop for *node* toward *dst* using DV table.
        Returns ``None`` if no route or cost is infinite.
        """
        cost, nxt = self.dv.get(node, {}).get(dst, (self.infinity, None))
        return nxt if cost < self.infinity else None

    def handle_link_up(self, u, v, delay, t):
        self._add_active_node(u)
        self._add_active_node(v)
        self.link_costs[u][v] = delay
        self.link_costs[v][u] = delay
        self._recompute_dv(u)
        self._recompute_dv(v)
        # Immediately send DV updates to neighbors (instantaneous)
        self._send_updates(u)
        self._send_updates(v)

    def handle_link_down(self, u, v, t):
        if v in self.link_costs[u]:
            del self.link_costs[u][v]
        if u in self.link_costs[v]:
            del self.link_costs[v][u]
        if v in self.neighbor_dvs[u]:
            del self.neighbor_dvs[u][v]
        if u in self.neighbor_dvs[v]:
            del self.neighbor_dvs[v][u]
        self._recompute_dv(u)
        self._recompute_dv(v)

    def _add_active_node(self, node):
        if node not in self.active_nodes:
            self.active_nodes.add(node)
            self.dv[node][node] = (0.0, node)
            jitter = self.random.uniform(0, 0.1 * self.update_interval)
            self._schedule_periodic(node, jitter)

    def _schedule_periodic(self, node, delay=None):
        if delay is None:
            delay = self.update_interval
        self.periodic_timers[node] = self.engine.schedule(delay, self._send_periodic, node)

    def _send_periodic(self, node):
        self._send_updates(node)
        self._schedule_periodic(node)

    def _recompute_dv(self, node):
        changed = False
        all_dsts = set([node])
        for n_dv in self.neighbor_dvs[node].values():
            all_dsts.update(n_dv.keys())
        for dst in self.dv[node].keys():
            all_dsts.add(dst)
        for dst in all_dsts:
            if dst == node:
                continue
            best_cost = self.infinity
            best_nxt = None
            for neighbor, link_cost in self.link_costs[node].items():
                if neighbor in self.neighbor_dvs[node] and dst in self.neighbor_dvs[node][neighbor]:
                    cost = link_cost + self.neighbor_dvs[node][neighbor][dst]
                    if cost < best_cost:
                        best_cost = cost
                        best_nxt = neighbor
            old_cost, old_nxt = self.dv[node].get(dst, (self.infinity, None))
            if abs(best_cost - old_cost) > 1e-9 or best_nxt != old_nxt:
                if best_cost < self.infinity:
                    self.dv[node][dst] = (best_cost, best_nxt)
                else:
                    self.dv[node][dst] = (self.infinity, None)
                changed = True
        if changed:
            self._send_updates(node)

    def _send_updates(self, node):
        if not self.link_costs[node]:
            return
        for neighbor, delay in self.link_costs[node].items():
            update_vector = {}
            for dst, (cost, nxt) in self.dv[node].items():
                if nxt == neighbor and dst != node:
                    update_vector[dst] = self.infinity
                else:
                    update_vector[dst] = cost
            self._control_message_count += 1
            self.engine.schedule(0, self._receive_update, neighbor, node, update_vector)

    def _receive_update(self, node, neighbor, update_vector):
        self.neighbor_dvs[node][neighbor] = update_vector
        self._recompute_dv(node)

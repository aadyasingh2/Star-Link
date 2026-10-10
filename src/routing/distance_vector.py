from collections import defaultdict
import random
import numpy as np
from .base import Router

class DistanceVectorRouter(Router):
    def __init__(self, config):
        super().__init__(config)
        self.update_interval = config['routing']['dv_update_interval_s']
        self.infinity = config['routing']['dv_infinity']
        self.processing_delay_s = config['routing'].get('dv_processing_delay_ms', 1.0) / 1000.0
        self.zero_delay = config['routing'].get('zero_delay', False)
        self.bytes_per_dv_header = config['routing'].get('bytes_per_dv_header', 24)
        self.bytes_per_dv_entry = config['routing'].get('bytes_per_dv_entry', 8)
        self.poison_reverse = config['routing'].get('dv_poison_reverse', True)
        self.random = random.Random(config['simulation'].get('random_seed', 42))
        self.reset()

    def reset(self):
        super().reset()
        self.cost = {}
        self.nxt = {}
        self.link_cost = defaultdict(dict)
        self.neighbor_vec = {}
        self.periodic_timers = {}
        self.active_nodes = set()
        self._batching = False
        self._batch_dirty_nodes = set()
        self._dropped_message_count = 0
        self._boot_message_count = 0

    @property
    def dv(self):
        """Materialize the legacy node/destination view for inspection."""
        result = {}
        node_index = self.engine.node_index
        for node, row in self.cost.items():
            result[node] = {
                dst: (
                    float(row[dst_idx]),
                    self.engine.nodes[int(self.nxt[node][dst_idx])]
                    if self.nxt[node][dst_idx] >= 0 else None,
                )
                for dst, dst_idx in node_index.items()
            }
        return result

    def begin_batch(self):
        self._batching = True
        self._batch_dirty_nodes.clear()
        for node in self.engine.node_index:
            self._ensure_node(node)

    def end_batch(self):
        dirty_nodes = self._batch_dirty_nodes
        self._batch_dirty_nodes = set()
        self._batching = False
        for node in dirty_nodes:
            self._add_active_node(node)
            self._recompute_dv(node, send=False)
            self._send_updates(node)

    def compute_route(self, src, dst, t):
        """Return full path from src to dst using DV table.
        Repeatedly follow next hops from compute_route_hop. Detect loops.
        If no route, return None and set self.last_failure.
        """
        self.last_failure = None
        if src == dst:
            return [src]
        path = [src]
        visited = {src}
        current = src
        while True:
            nxt = self.compute_route_hop(current, dst, t)
            if nxt is None:
                self.last_failure = "no_route"
                return None
            if nxt in visited:
                self.last_failure = "loop"
                return None
            if self.engine.is_ground_station(nxt) and nxt != dst:
                self.last_failure = "gs_transit"
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
        dst_idx = self.engine.node_index.get(dst)
        if node not in self.cost or dst_idx is None:
            return None
        if self.cost[node][dst_idx] >= self.infinity:
            return None
        nxt_idx = int(self.nxt[node][dst_idx])
        return self.engine.nodes[nxt_idx] if nxt_idx >= 0 else None

    def handle_link_up(self, u, v, delay, t):
        cost_ms = delay * 1000.0
        u_idx = self.engine.node_index[u]
        v_idx = self.engine.node_index[v]
        self.link_cost[u][v_idx] = cost_ms
        self.link_cost[v][u_idx] = cost_ms
        if self._batching:
            self._batch_dirty_nodes.update((u, v))
            return
        self._add_active_node(u)
        self._add_active_node(v)
        self._recompute_dv(u)
        self._recompute_dv(v)
        # Immediately send DV updates to neighbors (instantaneous)
        self._send_updates(u)
        self._send_updates(v)

    def handle_link_down(self, u, v, t):
        u_idx = self.engine.node_index[u]
        v_idx = self.engine.node_index[v]
        self.link_cost[u].pop(v_idx, None)
        self.link_cost[v].pop(u_idx, None)
        self.neighbor_vec.pop((u, v_idx), None)
        self.neighbor_vec.pop((v, u_idx), None)
        if self._batching:
            self._batch_dirty_nodes.update((u, v))
            return
        self._recompute_dv(u)
        self._recompute_dv(v)

    def _ensure_node(self, node):
        if node not in self.cost:
            own_idx = self.engine.node_index[node]
            self.cost[node] = np.full(self.engine.n_nodes, self.infinity, dtype=float)
            self.nxt[node] = np.full(self.engine.n_nodes, -1, dtype=int)
            self.cost[node][own_idx] = 0.0
            self.nxt[node][own_idx] = own_idx

    def _add_active_node(self, node):
        if node not in self.active_nodes:
            self.active_nodes.add(node)
            self._ensure_node(node)
            jitter = self.random.uniform(0, 0.1 * self.update_interval)
            self._schedule_periodic(node, jitter)

    def _schedule_periodic(self, node, delay=None):
        if delay is None:
            delay = self.update_interval
        self.periodic_timers[node] = self.engine.schedule(delay, self._send_periodic, node)

    def _send_periodic(self, node):
        self._send_updates(node)
        self._schedule_periodic(node)

    def _recompute_dv(self, node, send=True):
        self._ensure_node(node)
        own_idx = self.engine.node_index[node]
        neighbors = list(self.link_cost[node].items())
        if neighbors:
            neighbor_indices = np.fromiter(
                (neighbor_idx for neighbor_idx, _ in neighbors),
                dtype=int,
                count=len(neighbors),
            )
            link_costs = np.fromiter(
                (link_cost for _, link_cost in neighbors),
                dtype=float,
                count=len(neighbors),
            )
            vectors = np.stack([
                self.neighbor_vec.get(
                    (node, int(neighbor_idx)),
                    np.full(self.engine.n_nodes, self.infinity, dtype=float),
                )
                for neighbor_idx in neighbor_indices
            ])
            candidates = vectors + link_costs[:, np.newaxis]
            ground_neighbors = self.engine.gs_mask[neighbor_indices]
            candidates[ground_neighbors, :] = self.infinity
            ground_rows = np.flatnonzero(ground_neighbors)
            candidates[ground_rows, neighbor_indices[ground_rows]] = (
                vectors[ground_rows, neighbor_indices[ground_rows]]
                + link_costs[ground_rows]
            )
            best_rows = np.argmin(candidates, axis=0)
            new_cost = candidates[best_rows, np.arange(self.engine.n_nodes)]
            new_cost[new_cost >= self.infinity] = self.infinity
            new_nxt = neighbor_indices[best_rows].copy()
            unreachable = new_cost >= self.infinity
            new_nxt[unreachable] = -1
        else:
            new_cost = np.full(self.engine.n_nodes, self.infinity, dtype=float)
            new_nxt = np.full(self.engine.n_nodes, -1, dtype=int)
        new_cost[own_idx] = 0.0
        new_nxt[own_idx] = own_idx
        changed = (
            np.any(np.abs(new_cost - self.cost[node]) > 1e-9)
            or np.any(new_nxt != self.nxt[node])
        )
        self.cost[node] = new_cost
        self.nxt[node] = new_nxt
        if changed and send:
            self._send_updates(node)

    def _send_updates(self, node):
        if not self.link_cost[node]:
            return
        self._ensure_node(node)
        own_idx = self.engine.node_index[node]
        for neighbor_idx in list(self.link_cost[node]):
            neighbor = self.engine.nodes[neighbor_idx]
            # Check if link is physically up at send time
            d = self.engine.link_delay(node, neighbor, self.engine.current_time)
            if d is None:
                continue  # link not physically up; silently skip
            update_vector = self.cost[node].copy()
            if self.poison_reverse:
                poison = self.nxt[node] == neighbor_idx
                poison[own_idx] = False
                update_vector[poison] = self.infinity
            if self.engine.gs_mask[own_idx]:
                update_vector[:] = self.infinity
                update_vector[own_idx] = 0.0
            # Compute message delay
            if self.zero_delay:
                msg_delay = 0
            else:
                msg_delay = d + self.processing_delay_s
            # Count message and bytes (boot messages counted separately)
            finite_entries = int(np.count_nonzero(update_vector < self.infinity))
            msg_bytes = (
                self.bytes_per_dv_header
                + self.bytes_per_dv_entry * finite_entries
            )
            if self.engine.boot_mode:
                self._boot_message_count += 1
            else:
                self._control_message_count += 1
                self._control_bytes_count += msg_bytes
            self.engine.schedule(msg_delay, self._receive_update, neighbor, node, update_vector)

    def _receive_update(self, node, neighbor, update_vector):
        # Check if the link is still up at delivery time
        if not self.engine.link_is_up(neighbor, node, self.engine.current_time):
            self._dropped_message_count += 1
            return
        neighbor_idx = self.engine.node_index[neighbor]
        self.neighbor_vec[(node, neighbor_idx)] = update_vector
        self._recompute_dv(node)

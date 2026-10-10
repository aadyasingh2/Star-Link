from collections import defaultdict
import heapq
import itertools
from .base import Router

class LinkStateRouter(Router):
    def __init__(self, config):
        super().__init__(config)
        self.spf_holddown = config['routing']['spf_holddown_s']
        self.lsa_proc_delay = config['routing']['lsa_processing_delay_ms'] / 1000.0
        self.zero_delay = config['routing'].get('zero_delay', False)
        self.bytes_per_lsa_header = config['routing'].get('bytes_per_lsa_header', 24)
        self.bytes_per_lsa_link = config['routing'].get('bytes_per_lsa_link', 8)
        self.reset()

    def reset(self):
        super().reset()
        self.lsdb = defaultdict(dict)  # node -> {neighbor: cost}
        self.global_lsdb = defaultdict(lambda: defaultdict(dict))  # node -> origin -> {neighbor: cost}
        self.seq_nums = defaultdict(lambda: defaultdict(int))  # node -> origin -> max_seq
        self.my_seq = defaultdict(int)  # node -> seq
        self.next_hop = defaultdict(dict)  # node -> dst -> hop
        self.dirty = defaultdict(bool)
        self.dirty_since = {}
        self._tables = set()
        self.spf_runs = 0
        self.boot_spf_runs = 0
        self.active_links = defaultdict(dict)  # node -> {neighbor: cost}
        self._batching = False
        self._batch_dirty_nodes = set()
        self._dropped_message_count = 0
        self._boot_message_count = 0

    def begin_batch(self):
        self._batching = True
        self._batch_dirty_nodes.clear()

    def end_batch(self):
        dirty_nodes = self._batch_dirty_nodes
        self._batch_dirty_nodes = set()
        self._batching = False
        for node in dirty_nodes:
            self._local_change(node, self.engine.current_time)

    def compute_route_hop(self, node, dst, t):
        """Return next hop for *node* toward *dst* using stored next_hop table."""
        self._ensure_table(node)
        return self.next_hop.get(node, {}).get(dst)

    def compute_route(self, src, dst, t):
        """Return full path from src to dst using the LSDB."""
        self.last_failure = None
        self._ensure_table(src)
        if src == dst:
            return [src]
        
        path = [src]
        cur = src
        visited = {src}
        while cur != dst:
            self._ensure_table(cur)
            nxt = self.next_hop.get(cur, {}).get(dst)
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
            visited.add(nxt)
            cur = nxt
        return path

    def handle_link_up(self, u, v, delay, t):
        # Add bidirectional link with cost in ms
        cost_ms = delay * 1000.0
        self.active_links[u][v] = cost_ms
        self.active_links[v][u] = cost_ms
        if self._batching:
            self._batch_dirty_nodes.update((u, v))
        else:
            self._local_change(u, t)
            self._local_change(v, t)

    def handle_link_down(self, u, v, t):
        # Remove bidirectional link
        if v in self.active_links[u]:
            del self.active_links[u][v]
        if u in self.active_links[v]:
            del self.active_links[v][u]
        if self._batching:
            self._batch_dirty_nodes.update((u, v))
        else:
            self._local_change(u, t)
            self._local_change(v, t)

    def _local_change(self, node, t):
        self.my_seq[node] += 1
        seq = self.my_seq[node]
        links_copy = self.active_links[node].copy()
        self.global_lsdb[node][node] = links_copy
        self.seq_nums[node][node] = seq
        self._mark_dirty(node)
        self._flood(node, node, seq, links_copy, exclude=None, t=t)

    def _mark_dirty(self, node):
        if not self.dirty[node]:
            self.dirty_since[node] = self.engine.current_time
        self.dirty[node] = True

    def _ensure_table(self, node):
        if node not in self._tables:
            self._run_spf(node)
        elif (
            self.dirty[node]
            and self.engine.current_time
            >= self.dirty_since[node] + self.spf_holddown
        ):
            self._run_spf(node)

    def warm_up(self):
        for node in self.engine.nodes:
            self._ensure_table(node)
        self.boot_spf_runs = self.spf_runs
        self.spf_runs = 0

    def _run_spf(self, node):
        distances = {node: 0.0}
        first_hop = {}
        queue = []
        sequence = itertools.count()
        heapq.heappush(queue, (0.0, next(sequence), node))
        while queue:
            distance, _, current = heapq.heappop(queue)
            if distance != distances.get(current):
                continue
            if (
                current != node
                and current in self.engine.node_index
                and self.engine.gs_mask[self.engine.node_index[current]]
            ):
                continue
            for neighbor, cost in self.global_lsdb[node].get(current, {}).items():
                candidate = distance + cost
                if candidate < distances.get(neighbor, float('inf')):
                    distances[neighbor] = candidate
                    first_hop[neighbor] = (
                        neighbor if current == node else first_hop[current]
                    )
                    heapq.heappush(
                        queue, (candidate, next(sequence), neighbor)
                    )
        self.next_hop[node] = first_hop
        self._tables.add(node)
        self.dirty[node] = False
        self.spf_runs += 1

    def _flood(self, current_node, origin, seq, links, exclude, t):
        if self.engine.is_ground_station(current_node) and current_node != origin:
            return
        for neighbor in self.active_links[current_node]:
            if neighbor == exclude:
                continue
            d = self.engine.link_delay(current_node, neighbor, self.engine.current_time)
            if d is None:
                continue
            
            if self.zero_delay:
                msg_delay = 0
            else:
                msg_delay = d + self.lsa_proc_delay

            msg_bytes = self.bytes_per_lsa_header + self.bytes_per_lsa_link * len(links)
            if self.engine.boot_mode:
                self._boot_message_count += 1
            else:
                self._control_message_count += 1
                self._control_bytes_count += msg_bytes
                
            self.engine.schedule(msg_delay, self._receive_lsa, neighbor, origin, seq, links, current_node)


    def _receive_lsa(self, node, origin, seq, links, from_neighbor):
        if not self.engine.link_is_up(from_neighbor, node, self.engine.current_time):
            self._dropped_message_count += 1
            return
        if seq <= self.seq_nums[node].get(origin, 0):
            return
        self.seq_nums[node][origin] = seq
        self.global_lsdb[node][origin] = links
        self._mark_dirty(node)
        self._flood(node, origin, seq, links, exclude=from_neighbor, t=self.engine.current_time)

from collections import defaultdict
import networkx as nx
from .base import Router

class LinkStateRouter(Router):
    def __init__(self, config):
        super().__init__(config)
        self.spf_holddown = config['routing']['spf_holddown_s']
        self.lsa_proc_delay = config['routing']['lsa_processing_delay_ms'] / 1000.0
        self.reset()

    def reset(self):
        super().reset()
        self.lsdb = defaultdict(dict)  # node -> {neighbor: cost}
        self.global_lsdb = defaultdict(lambda: defaultdict(dict))  # node -> origin -> {neighbor: cost}
        self.seq_nums = defaultdict(lambda: defaultdict(int))  # node -> origin -> max_seq
        self.my_seq = defaultdict(int)  # node -> seq
        self.next_hop = defaultdict(dict)  # node -> dst -> hop
        self.spf_timers = {}  # node -> timer_id
        self.active_links = defaultdict(dict)  # node -> {neighbor: cost}

    def compute_route_hop(self, node, dst, t):
        """Return next hop for *node* toward *dst* using stored next_hop table."""
        return self.next_hop.get(node, {}).get(dst)

    def compute_route(self, src, dst, t):
        """Return full path from src to dst using the LSDB."""
        if src == dst:
            return [src]
        # Use next_hop table if available
        if src in self.next_hop and dst in self.next_hop[src]:
            path = [src]
            cur = src
            visited = {src}
            while cur != dst:
                nxt = self.next_hop.get(cur, {}).get(dst)
                if nxt is None or nxt in visited:
                    return None
                path.append(nxt)
                visited.add(nxt)
                cur = nxt
            return path
        # Fallback: build temporary graph from global LSDB
        # Fallback: build temporary graph from LSDB entries that are currently active locally
        G = nx.DiGraph()
        for origin, neighbor_dict in self.active_links.items():
            for neighbor, cost in neighbor_dict.items():
                G.add_edge(origin, neighbor, weight=cost)
        try:
            return nx.shortest_path(G, source=src, target=dst, weight='weight')
        except Exception:
            return None

    def handle_link_up(self, u, v, delay, t):
        # Add bidirectional link
        self.active_links[u][v] = delay
        self.active_links[v][u] = delay
        self._local_change(u, t)
        self._local_change(v, t)

    def handle_link_down(self, u, v, t):
        # Remove bidirectional link
        if v in self.active_links[u]:
            del self.active_links[u][v]
        if u in self.active_links[v]:
            del self.active_links[v][u]
        self._local_change(u, t)
        self._local_change(v, t)

    def _local_change(self, node, t):
        self.my_seq[node] += 1
        seq = self.my_seq[node]
        links_copy = self.active_links[node].copy()
        self.global_lsdb[node][node] = links_copy
        self.seq_nums[node][node] = seq
        self._schedule_spf(node)
        self._flood(node, node, seq, links_copy, exclude=None, t=t)

    def _schedule_spf(self, node):
        if self.spf_holddown == 0:
            # Immediate SPF run for zero holddown
            self._run_spf(node)
        else:
            if node in self.spf_timers:
                self.engine.cancel(self.spf_timers[node])
            self.spf_timers[node] = self.engine.schedule(self.spf_holddown, self._run_spf, node)

    def _run_spf(self, node):
        if node in self.spf_timers:
            del self.spf_timers[node]
        g = nx.DiGraph()
        for origin, links in self.global_lsdb[node].items():
            for neighbor, cost in links.items():
                # Only include edge if link is currently up according to engine snapshot
                if self.engine.link_is_up(origin, neighbor, self.engine.current_time):
                    g.add_edge(origin, neighbor, weight=cost)
        self.next_hop[node] = {}
        try:
            paths = nx.single_source_dijkstra_path(g, node)
            for dst, path in paths.items():
                if len(path) > 1:
                    self.next_hop[node][dst] = path[1]
        except Exception:
            pass

    def _flood(self, current_node, origin, seq, links, exclude, t):
        for neighbor, delay in self.active_links[current_node].items():
            if neighbor != exclude:
                # count only if we actually schedule a send
                if self.engine.link_is_up(current_node, neighbor, self.engine.current_time):
                    self._control_message_count += 1
                    total_delay = delay + self.lsa_proc_delay
                    self.engine.schedule(total_delay, self._receive_lsa, neighbor, origin, seq, links, current_node)
                else:
                    # link down at send time: count as dropped
                    self._dropped_message_count = getattr(self, '_dropped_message_count', 0) + 1


    def _receive_lsa(self, node, origin, seq, links, from_neighbor):
        if seq <= self.seq_nums[node].get(origin, 0):
            return
        self.seq_nums[node][origin] = seq
        self.global_lsdb[node][origin] = links
        self._schedule_spf(node)
        self._flood(node, origin, seq, links, exclude=from_neighbor, t=self.engine.current_time)

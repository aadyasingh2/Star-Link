import heapq
import itertools


def _edge_delay_ms(snapshot, u, v):
    data = snapshot[u][v]
    if "propagation_delay_ms" in data:
        return data["propagation_delay_ms"]
    if "propagation_delay_s" in data:
        return data["propagation_delay_s"] * 1000.0
    raise KeyError("Missing propagation delay attribute")


def oracle_path(snapshot, src, dst):
    """Return the lowest-delay path and its delay using the true snapshot."""
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
        if node != src and snapshot.nodes[node].get("type") == "ground_station":
            continue
        for neighbor in snapshot.neighbors(node):
            candidate = distance + _edge_delay_ms(snapshot, node, neighbor)
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, next(sequence), neighbor))
    return None, None


def path_delay_ms(snapshot, path):
    """Sum true edge delays for a path, or return None if any edge is absent."""
    total = 0.0
    for u, v in zip(path, path[1:]):
        if not snapshot.has_edge(u, v):
            return None
        total += _edge_delay_ms(snapshot, u, v)
    return total


def classify(router_path, last_failure, snapshot, oracle_path_, oracle_delay_ms):
    """Classify one router result against the true-snapshot oracle."""
    if last_failure == "loop":
        return "loop"
    if last_failure == "gs_transit":
        return "blackhole"
    if router_path is None:
        return "blackhole" if oracle_path_ is not None else "correct"
    actual_delay = path_delay_ms(snapshot, router_path)
    if actual_delay is None:
        return "blackhole"
    if router_path == oracle_path_:
        return "correct"
    if (
        oracle_delay_ms is not None
        and abs(actual_delay - oracle_delay_ms) <= 1e-6
    ):
        return "correct"
    return "stale_working"

import argparse
import datetime as dt
import gzip
import itertools
import json
import math
import os
import random
import sys
import time

import numpy as np
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

from scripts.baseline_report import (  # noqa: E402
    CLASSES,
    flatten_config,
    format_metric,
    make_row,
    mean,
    new_metrics,
    record_sample,
)
from src.constellation import Constellation, TopologySeries  # noqa: E402
from src.routing.distance_vector import DistanceVectorRouter  # noqa: E402
from src.routing.engine import RoutingEngine  # noqa: E402
from src.routing.evaluation import classify, oracle_path, path_delay_ms  # noqa: E402
from src.routing.link_state import LinkStateRouter  # noqa: E402
from src.routing.sapr import SAPRRouter  # noqa: E402


ROUTER_NAMES = ('LinkState', 'DistanceVector', 'SAPR')
ROUTER_COLOURS = {
    'LinkState': '#22d3ee',
    'DistanceVector': '#fbbf24',
    'SAPR': '#a78bfa',
    'Oracle': '#ffffff',
}
NOT_MEASURED = [
    'packet_queues',
    'packet_loss_beyond_route_probes',
    'tcp_throughput',
]


def finite_or_fail(value, path='root'):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f'Non-finite number at {path}: {value}')
    if isinstance(value, dict):
        for key, child in value.items():
            finite_or_fail(child, f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            finite_or_fail(child, f'{path}[{index}]')


def edge_type(graph, u, v):
    link_type = graph[u][v].get('link_type')
    if link_type in ('intra', 'inter', 'gsl'):
        return link_type
    if (
        graph.nodes[u].get('type') == 'ground_station'
        or graph.nodes[v].get('type') == 'ground_station'
    ):
        return 'gsl'
    return 'inter'


def make_node_table(config, constellation, snapshot):
    satellites = [
        {
            'id': f'sat_{index}',
            'type': 'satellite',
            'plane': index // constellation.sats_per_plane,
            'index_in_plane': index % constellation.sats_per_plane,
        }
        for index in range(constellation.n_sats)
    ]
    ground_stations = [
        {
            'id': f"gs_{station['name']}",
            'type': 'ground_station',
            'name': station['name'],
            'lat': station['lat'],
            'lon': station['lon'],
        }
        for station in config['ground_stations']
    ]
    nodes = satellites + ground_stations
    if len(nodes) != 533 or len(snapshot.nodes) != len(nodes):
        raise ValueError(
            f'Expected 533 nodes; metadata={len(nodes)}, snapshot={len(snapshot.nodes)}'
        )
    if set(node['id'] for node in nodes) != set(snapshot.nodes):
        raise ValueError('Fixed node order does not match the topology snapshot')
    return nodes, {node['id']: index for index, node in enumerate(nodes)}


def graph_edges(graph, node_index):
    extracted = {}
    for u, v, data in graph.edges(data=True):
        key = tuple(sorted((node_index[u], node_index[v])))
        extracted[key] = {
            'a': key[0],
            'b': key[1],
            'type': edge_type(graph, u, v),
            'delay_ms': float(data['propagation_delay_ms']),
        }
    return extracted


def sample_positions(topology, constellation, nodes, times):
    frames = []
    for sample_t in times:
        positions = constellation.get_ecef_positions(sample_t)
        flat = np.round(positions, 1).reshape(-1).tolist()
        graph = topology.get_snapshot(sample_t)
        for node in nodes[constellation.n_sats:]:
            pos = graph.nodes[node['id']]['pos']
            flat.extend(round(float(value), 1) for value in pos)
        if len(flat) != len(nodes) * 3:
            raise ValueError('Position frame does not contain three values per node')
        frames.append(flat)
    return frames


def build_summary(router_metrics, pair_metrics, duration_s, routers, engine):
    headers = [
        'Router', '%correct', '%stale_working', '%loop', '%blackhole',
        'mean actual delay ms', 'p99 actual delay ms', 'mean stretch vs oracle',
        'route changes/min', 'measured control messages', 'control bytes',
        'dropped messages', 'SPF runs', 'cost events generated', 'boot messages',
    ]
    rows = [
        make_row('Oracle', router_metrics['Oracle'], duration_s, oracle_row=True),
    ]
    for name, router in routers:
        rows.append(make_row(
            name, router_metrics[name], duration_s,
            router.control_message_count, router.control_bytes_count,
            router.dropped_message_count,
            getattr(router, 'spf_runs', None),
            engine.cost_events_generated, router.boot_message_count,
        ))

    breakdown = {}
    for pair, by_router in pair_metrics.items():
        pair_name = f'{pair[0]} -> {pair[1]}'
        breakdown[pair_name] = {}
        for name, metrics in by_router.items():
            breakdown[pair_name][name] = {
                'classes': dict(metrics['classes']),
                'oracle_unreachable': metrics['oracle_unreachable'],
                'mean_actual_delay_ms': format_metric(mean(metrics['delays'])),
            }
    return {'headers': headers, 'rows': rows, 'per_pair': breakdown}


def validate_export(meta, positions, links, routes, events):
    if len(meta['nodes']) != 533:
        raise ValueError('Export node count must be 533')
    valid_ids = set(range(len(meta['nodes'])))
    for second, pair_data in enumerate(routes['samples']):
        for pair_index, router_data in enumerate(pair_data):
            for route in router_data['paths']:
                if route is not None and any(index not in valid_ids for index in route):
                    raise ValueError(
                        f'Invalid path node index at second {second}, '
                        f'pair {pair_index}'
                    )
            for category in router_data['classes']:
                if category not in CLASSES:
                    raise ValueError(f'Invalid route class: {category}')
    if any(
        len(frame) != len(meta['nodes']) * 3
        for frame in positions['frames_km']
    ):
        raise ValueError('Position frame size does not match node table')
    if any(
        delay is not None and not math.isfinite(delay)
        for frame in links['delay_ms']
        for delay in frame
    ):
        raise ValueError('Link delay contains NaN or infinity')
    for payload in (meta, positions, links, routes, events):
        finite_or_fail(payload)


def write_json(path, payload):
    with open(path, 'w', encoding='utf-8', newline='') as output:
        json.dump(payload, output, separators=(',', ':'), allow_nan=False)
        output.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=int, default=600)
    parser.add_argument('--step-frames', type=int, default=5)
    parser.add_argument('--out', default=os.path.join(ROOT, 'web', 'public', 'data'))
    args = parser.parse_args()
    if args.duration <= 0 or args.step_frames <= 0:
        parser.error('--duration and --step-frames must be positive')

    started = time.perf_counter()
    with open(os.path.join(ROOT, 'config.yaml'), encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file)
    config['constellation']['isl_inter_plane_max_lat_deg'] = 45
    seed = int(config['simulation']['random_seed'])
    random.seed(seed)
    np.random.seed(seed)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    constellation = Constellation(config)
    topology = TopologySeries(config, constellation)
    initial_snapshot = topology.get_snapshot(0)
    nodes, node_index = make_node_table(config, constellation, initial_snapshot)
    lsr = LinkStateRouter(config)
    dvr = DistanceVectorRouter(config)
    sapr = SAPRRouter(config)
    routers = (
        ('LinkState', lsr),
        ('DistanceVector', dvr),
        ('SAPR', sapr),
    )
    engine = RoutingEngine(config, topology, [router for _, router in routers])
    initial_edges = graph_edges(initial_snapshot, node_index)
    edge_table = dict(initial_edges)
    initial_edge_keys = list(initial_edges)
    link_events = []
    delay_frame_maps = []
    lifetime_frame_maps = []
    positions_times = list(range(0, args.duration + 1, args.step_frames))
    if not positions_times or positions_times[-1] != args.duration:
        positions_times.append(args.duration)
    positions_frames = sample_positions(
        topology, constellation, nodes, positions_times
    )

    pair_ids = [
        (a['id'], b['id'])
        for a, b in itertools.combinations(nodes[constellation.n_sats:], 2)
    ]
    pair_index = {
        (node_index[src], node_index[dst]): index
        for index, (src, dst) in enumerate(pair_ids)
    }
    all_names = ('Oracle', 'LinkState', 'DistanceVector', 'SAPR')
    router_metrics = {name: new_metrics() for name in all_names}
    pair_metrics = {
        pair: {name: new_metrics() for name in all_names}
        for pair in pair_ids
    }
    route_samples = []
    event_records = []
    previous_paths = {}
    previous_classes = {}
    captured_events = []
    state_frames = {
        'ls_spf_runs': [],
        'ls_control_messages': [],
        'dv_control_messages': [],
        'sapr_control_messages': [],
        'ls_dropped': [],
        'dv_dropped': [],
        'sapr_dropped': [],
    }
    original_generate = engine.generate_events_for_step

    def capture_generated(step):
        generated = original_generate(step)
        captured_events.extend((step, event) for event in generated)
        return generated

    engine.generate_events_for_step = capture_generated
    boot_started = time.perf_counter()
    engine.initialize()
    boot_seconds = time.perf_counter() - boot_started

    current_edges = initial_edges
    for key, edge in current_edges.items():
        edge_table.setdefault(key, edge)
    initial_lifetimes = {}
    for key in current_edges:
        u, v = nodes[key[0]]['id'], nodes[key[1]]['id']
        lifetime = topology.link_remaining_lifetime(
            u, v, 0, float(config['sapr']['look_ahead_window_s'])
        )
        initial_lifetimes[key] = {
            'seconds': min(lifetime, float(config['sapr']['look_ahead_window_s'])),
            'capped': lifetime >= float(config['sapr']['look_ahead_window_s']),
        }
    delay_frame_maps.append({
        key: edge['delay_ms'] for key, edge in current_edges.items()
    })
    lifetime_frame_maps.append(initial_lifetimes)
    for second in range(1, args.duration + 1):
        engine.step_to(float(second))
        snapshot = topology.get_snapshot(second)
        current_edges = graph_edges(snapshot, node_index)
        for key, edge in current_edges.items():
            edge_table.setdefault(key, {**edge, 'delay_ms': None})
        new_events = captured_events
        captured_events = []
        for event_t, (u, v, event_type, delay_s) in new_events:
            key = tuple(sorted((node_index[u], node_index[v])))
            edge_table.setdefault(key, {
                'a': key[0], 'b': key[1],
                'type': edge_type(snapshot, u, v) if snapshot.has_edge(u, v) else edge_table.get(key, {}).get('type', 'inter'),
                'delay_ms': None,
            })
            link_events.append({
                't': event_t,
                'a': key[0],
                'b': key[1],
                'type': event_type,
                'delay_ms': delay_s * 1000.0 if delay_s is not None else None,
            })
        if second in positions_times:
            delay_row = {}
            lifetime_row = {}
            for key, edge in current_edges.items():
                delay_row[key] = edge['delay_ms']
                u, v = nodes[key[0]]['id'], nodes[key[1]]['id']
                lifetime = topology.link_remaining_lifetime(
                    u, v, second,
                    float(config['sapr']['look_ahead_window_s']),
                )
                capped = lifetime >= float(config['sapr']['look_ahead_window_s'])
                lifetime_row[key] = {
                    'seconds': min(
                        lifetime, float(config['sapr']['look_ahead_window_s'])
                    ),
                    'capped': capped,
                }
            delay_frame_maps.append(delay_row)
            lifetime_frame_maps.append(lifetime_row)

        samples_this_second = []
        for pair in pair_ids:
            src, dst = pair
            truth_path, truth_delay = oracle_path(snapshot, src, dst)
            oracle_delay = (
                path_delay_ms(snapshot, truth_path) if truth_path is not None else None
            )
            outcomes = [('Oracle', truth_path, None, 'correct', oracle_delay)]
            for name, router in routers:
                path = router.compute_route(src, dst, float(second))
                category = classify(
                    path, router.last_failure, snapshot, truth_path, truth_delay
                )
                delay = path_delay_ms(snapshot, path) if path is not None else None
                outcomes.append((name, path, router.last_failure, category, delay))

            pair_key = (src, dst)
            sample_data = {
                'paths': [],
                'last_failure': [],
                'classes': [],
                'delay_ms': [],
            }
            for name, path, failure, category, delay in outcomes:
                indices = (
                    [node_index[node] for node in path] if path is not None else None
                )
                sample_data['paths'].append(indices)
                sample_data['last_failure'].append(failure)
                sample_data['classes'].append(category)
                sample_data['delay_ms'].append(delay)
                record_sample(
                    router_metrics[name], pair_key, path, category, delay,
                    truth_path is not None, truth_delay,
                )
                record_sample(
                    pair_metrics[pair_key][name], pair_key, path, category, delay,
                    truth_path is not None, truth_delay,
                )
                route_key = (name, pair_index[(node_index[src], node_index[dst])])
                old_path = previous_paths.get(route_key)
                old_class = previous_classes.get(route_key)
                if route_key in previous_paths and old_path != indices:
                    event_records.append({
                        't': second, 'type': 'route_change',
                        'router': name, 'pair': pair_index[(node_index[src], node_index[dst])],
                        'old_path': old_path, 'new_path': indices,
                    })
                if route_key in previous_classes and old_class != category:
                    event_records.append({
                        't': second, 'type': 'class_transition',
                        'router': name, 'pair': pair_index[(node_index[src], node_index[dst])],
                        'old_class': old_class, 'new_class': category,
                    })
                previous_paths[route_key] = indices
                previous_classes[route_key] = category
            samples_this_second.append(sample_data)
        route_samples.append(samples_this_second)
        state_frames['ls_spf_runs'].append(lsr.spf_runs)
        state_frames['ls_control_messages'].append(lsr.control_message_count)
        state_frames['dv_control_messages'].append(dvr.control_message_count)
        state_frames['sapr_control_messages'].append(sapr.control_message_count)
        state_frames['ls_dropped'].append(lsr.dropped_message_count)
        state_frames['dv_dropped'].append(dvr.dropped_message_count)
        state_frames['sapr_dropped'].append(sapr.dropped_message_count)

    for event_t, (u, v, event_type, delay_s) in captured_events:
        key = tuple(sorted((node_index[u], node_index[v])))
        link_events.append({
            't': event_t, 'a': key[0], 'b': key[1], 'type': event_type,
            'delay_ms': delay_s * 1000.0 if delay_s is not None else None,
        })

    ordered_keys = list(edge_table)
    edge_ids = {key: index for index, key in enumerate(ordered_keys)}
    for record in link_events:
        record['link_id'] = edge_ids[(record['a'], record['b'])]
    initial_ids = [edge_ids[key] for key in initial_edge_keys]
    link_table = [
        {'a': edge_table[key]['a'], 'b': edge_table[key]['b'], 'type': edge_table[key]['type']}
        for key in ordered_keys
    ]
    delay_frames = [
        [frame.get(key) for key in ordered_keys] for frame in delay_frame_maps
    ]
    lifetime_frames = [
        [frame.get(key) for key in ordered_keys] for frame in lifetime_frame_maps
    ]

    summary = build_summary(router_metrics, pair_metrics, args.duration, routers, engine)
    meta = {
        'config': dict(flatten_config(config)),
        'seed': seed,
        'duration_s': args.duration,
        'step_s': float(config['simulation']['timestep_s']),
        'node_order': [node['id'] for node in nodes],
        'nodes': nodes,
        'routers': ['Oracle', *ROUTER_NAMES],
        'router_colours': ROUTER_COLOURS,
        'provisional': True,
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'not_measured': NOT_MEASURED,
    }
    positions_payload = {
        'times_s': positions_times,
        'frames_km': positions_frames,
        'note': 'Browser interpolates linearly between sampled position frames.',
    }
    links_payload = {
        'initial_link_ids': initial_ids,
        'links': link_table,
        'events': link_events,
        'delay_times_s': positions_times,
        'delay_ms': delay_frames,
        'remaining_lifetime': lifetime_frames,
        'look_ahead_cap_s': config['sapr']['look_ahead_window_s'],
    }
    routes_payload = {
        'pairs': [
            {'src': src, 'dst': dst, 'src_id': node_index[src], 'dst_id': node_index[dst]}
            for src, dst in pair_ids
        ],
        'routers': list(all_names),
        'times_s': list(range(1, args.duration + 1)),
        'samples': route_samples,
        'state': {
            'times_s': list(range(1, args.duration + 1)),
            **state_frames,
        },
    }
    # State snapshots are emitted at each second from final router counters
    # captured during the same simulation pass.
    events_payload = {'events': link_events + event_records}
    validate_export(meta, positions_payload, links_payload, routes_payload, events_payload)

    write_json(os.path.join(out_dir, 'meta.json'), meta)
    write_json(os.path.join(out_dir, 'positions.json'), positions_payload)
    write_json(os.path.join(out_dir, 'links.json'), links_payload)
    routes_path = os.path.join(out_dir, 'routes.json')
    write_json(routes_path, routes_payload)
    route_size = os.path.getsize(routes_path)
    if route_size > 40 * 1024 * 1024:
        with open(routes_path, 'rb') as source, gzip.open(
            routes_path + '.gz', 'wb', compresslevel=6
        ) as compressed:
            while chunk := source.read(1024 * 1024):
                compressed.write(chunk)
        os.remove(routes_path)
    write_json(os.path.join(out_dir, 'events.json'), events_payload)
    write_json(os.path.join(out_dir, 'summary.json'), summary)

    schema_path = os.path.join(out_dir, 'SCHEMA.md')
    with open(schema_path, 'w', encoding='utf-8', newline='') as schema:
        schema.write(
            '# Visualization export schema\n'
            '- `meta.json.config`: flattened configuration values used for this run.\n'
            '- `meta.json.seed`: deterministic simulation seed.\n'
            '- `meta.json.duration_s`: simulation duration in seconds.\n'
            '- `meta.json.step_s`: simulator timestep in seconds.\n'
            '- `meta.json.nodes`: fixed-order satellite and ground-station metadata.\n'
            '- `meta.json.node_order`: node IDs in the index order used by all arrays.\n'
            '- `meta.json.nodes[].id`: simulator node ID.\n'
            '- `meta.json.nodes[].type`: `satellite` or `ground_station`.\n'
            '- `meta.json.nodes[].plane`: zero-based satellite orbital plane.\n'
            '- `meta.json.nodes[].index_in_plane`: zero-based satellite slot in its plane.\n'
            '- `meta.json.nodes[].name`: ground-station display name.\n'
            '- `meta.json.nodes[].lat`: ground-station latitude in degrees.\n'
            '- `meta.json.nodes[].lon`: ground-station longitude in degrees.\n'
            '- `meta.json.routers`: router row names.\n'
            '- `meta.json.router_colours`: fixed router and oracle colours.\n'
            '- `meta.json.provisional`: marks results as provisional research output.\n'
            '- `meta.json.generated_at`: UTC export generation timestamp.\n'
            '- `meta.json.not_measured`: outputs not measured by this simulator.\n'
            '- `positions.json.times_s`: sampled position times in seconds.\n'
            '- `positions.json.frames_km`: flattened xyz positions in node order; interpolate between frames.\n'
            '- `positions.json.note`: browser interpolation instruction.\n'
            '- `links.json.initial_link_ids`: IDs of links present at t=0.\n'
            '- `links.json.links`: link table with endpoint node IDs and link type.\n'
            '- `links.json.links[].a`: lower endpoint node index.\n'
            '- `links.json.links[].b`: higher endpoint node index.\n'
            '- `links.json.links[].type`: `intra`, `inter`, or `gsl`.\n'
            '- `links.json.events`: timestamped up/down/cost changes with link IDs.\n'
            '- `links.json.events[].t`: event time in seconds.\n'
            '- `links.json.events[].a`: first endpoint node index.\n'
            '- `links.json.events[].b`: second endpoint node index.\n'
            '- `links.json.events[].type`: link event kind (`up`, `down`, or `cost`).\n'
            '- `links.json.events[].delay_ms`: advertised event delay in milliseconds, or null.\n'
            '- `links.json.events[].link_id`: link-table index.\n'
            '- `links.json.delay_times_s`: times corresponding to sampled delay frames.\n'
            '- `links.json.delay_ms`: per-frame delays aligned with `links` (null means down).\n'
            '- `links.json.remaining_lifetime`: capped seconds and cap flags per link/frame.\n'
            '- `links.json.look_ahead_cap_s`: lifetime cap used for export.\n'
            '- `routes.json.pairs`: all unordered ground-station pairs and node IDs.\n'
            '- `routes.json.routers`: router order used by each sample row.\n'
            '- `routes.json.times_s`: route sample timestamps.\n'
            '- `routes.json.samples`: per-second paths, failures, classes, and actual delays.\n'
            '- `routes.json.samples[t][pair].paths`: node-index paths in router order, or null.\n'
            '- `routes.json.samples[t][pair].last_failure`: per-router failure reason or null.\n'
            '- `routes.json.samples[t][pair].classes`: per-router outcome class.\n'
            '- `routes.json.samples[t][pair].delay_ms`: actual snapshot path delay per router, or null.\n'
            '- `routes.json.state`: per-second cumulative router counters.\n'
            '- `routes.json.state.times_s`: times for the counter arrays.\n'
            '- `routes.json.state.ls_spf_runs`: measured LS SPF runs.\n'
            '- `routes.json.state.ls_control_messages`: cumulative LS control messages.\n'
            '- `routes.json.state.dv_control_messages`: cumulative DV control messages.\n'
            '- `routes.json.state.sapr_control_messages`: cumulative SAPR control messages.\n'
            '- `routes.json.state.ls_dropped`: cumulative LS dropped messages.\n'
            '- `routes.json.state.dv_dropped`: cumulative DV dropped messages.\n'
            '- `routes.json.state.sapr_dropped`: cumulative SAPR dropped messages.\n'
            '- `events.json.events`: link, route-change, and class-transition events.\n'
            '- `events.json.events[].t`: event time in seconds.\n'
            '- `events.json.events[].type`: link event, route change, or class transition.\n'
            '- `events.json.events[].router`: router name for route/class events.\n'
            '- `events.json.events[].pair`: pair index for route/class events.\n'
            '- `events.json.events[].old_path`: previous node-index route, or null.\n'
            '- `events.json.events[].new_path`: new node-index route, or null.\n'
            '- `events.json.events[].old_class`: previous classification.\n'
            '- `events.json.events[].new_class`: new classification.\n'
            '- `summary.json.headers`: summary table column labels.\n'
            '- `summary.json.rows`: summary table rows from baseline aggregation helpers.\n'
            '- `summary.json.per_pair`: per-pair class totals, unreachable counts, and mean delay.\n'
            '- `summary.json.per_pair.<pair>.Oracle`: oracle metrics for the pair.\n'
            '- `summary.json.per_pair.<pair>.LinkState`: LinkState metrics for the pair.\n'
            '- `summary.json.per_pair.<pair>.DistanceVector`: DistanceVector metrics for the pair.\n'
            '- `summary.json.per_pair.<pair>.SAPR`: SAPR metrics for the pair.\n'
        )

    total_runtime = time.perf_counter() - started
    print(f'boot_runtime_seconds: {boot_seconds:.3f}')
    total_bytes = 0
    for filename in sorted(os.listdir(out_dir)):
        file_path = os.path.join(out_dir, filename)
        if os.path.isfile(file_path):
            size = os.path.getsize(file_path)
            total_bytes += size
            print(f'{filename}: {size} bytes')
    print(f'total_export_bytes: {total_bytes}')
    print(f'total_export_runtime_seconds: {total_runtime:.3f}')


if __name__ == '__main__':
    main()

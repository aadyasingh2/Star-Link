import argparse
import os
import random
import sys
import time

import numpy as np
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.constellation import Constellation, TopologySeries
from src.routing.distance_vector import DistanceVectorRouter
from src.routing.engine import RoutingEngine
from src.routing.evaluation import classify, oracle_path, path_delay_ms
from src.routing.link_state import LinkStateRouter


PAIRS = (
    ('gs_Chennai', 'gs_London'),
    ('gs_Chennai', 'gs_Sydney'),
    ('gs_New York', 'gs_Tokyo'),
)
CLASSES = ('correct', 'stale_working', 'loop', 'blackhole')


def flatten_config(value, prefix=''):
    flattened = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f'{prefix}.{key}' if prefix else str(key)
            flattened.extend(flatten_config(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            flattened.extend(flatten_config(child, f'{prefix}[{index}]'))
    else:
        flattened.append((prefix, value))
    return flattened


def new_metrics():
    return {
        'classes': {category: 0 for category in CLASSES},
        'oracle_unreachable': 0,
        'delays': [],
        'stretches': [],
        'route_changes': 0,
        'previous_paths': {},
        'samples': 0,
    }


def record_sample(metrics, key, path, category, actual_delay, oracle_exists, oracle_delay):
    metrics['samples'] += 1
    if not oracle_exists:
        metrics['oracle_unreachable'] += 1
    else:
        metrics['classes'][category] += 1
    if actual_delay is not None:
        metrics['delays'].append(actual_delay)
    if actual_delay is not None and oracle_exists and oracle_delay > 0:
        metrics['stretches'].append(actual_delay / oracle_delay)
    if key in metrics['previous_paths'] and metrics['previous_paths'][key] != path:
        metrics['route_changes'] += 1
    metrics['previous_paths'][key] = path


def percentile(values, percent):
    return float(np.percentile(values, percent)) if values else float('nan')


def mean(values):
    return float(np.mean(values)) if values else float('nan')


def format_metric(value):
    return 'n/a' if not np.isfinite(value) else f'{value:.3f}'


def make_row(name, metrics, duration_s, control_messages=0, control_bytes=0,
             dropped=0, spf_runs=None, cost_events=0, boot_messages=0,
             oracle_row=False):
    reachable_samples = sum(metrics['classes'].values())
    percentages = {}
    for category in CLASSES:
        if oracle_row:
            percentages[category] = 100.0 if category == 'correct' else 0.0
        elif reachable_samples:
            percentages[category] = (
                metrics['classes'][category] * 100.0 / reachable_samples
            )
        else:
            percentages[category] = 0.0
    return [
        name,
        *(f'{percentages[category]:.2f}' for category in CLASSES),
        format_metric(mean(metrics['delays'])),
        format_metric(percentile(metrics['delays'], 99)),
        format_metric(mean(metrics['stretches'])),
        format_metric(metrics['route_changes'] / (duration_s / 60.0)),
        str(control_messages),
        str(control_bytes),
        str(dropped),
        str(spf_runs) if spf_runs is not None else '-',
        str(cost_events),
        str(boot_messages),
    ]


def print_table(headers, rows):
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    print(' | '.join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print('-+-'.join('-' * width for width in widths))
    for row in rows:
        print(' | '.join(value.ljust(widths[index]) for index, value in enumerate(row)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=600.0)
    parser.add_argument('--step', type=float, default=None)
    args = parser.parse_args()

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    with open(os.path.join(root, 'config.yaml'), encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file)
    config['constellation']['isl_inter_plane_max_lat_deg'] = 45
    timestep = float(config['simulation']['timestep_s'])
    step = timestep if args.step is None else args.step
    if args.duration <= 0 or step <= 0:
        parser.error('--duration and --step must be positive')

    seed = int(config['simulation']['random_seed'])
    random.seed(seed)
    np.random.seed(seed)
    overall_started = time.perf_counter()
    print(f'seed={seed}')
    for key, value in flatten_config(config):
        print(f'{key}={value}')

    constellation = Constellation(config)
    topology = TopologySeries(config, constellation)
    lsr = LinkStateRouter(config)
    dvr = DistanceVectorRouter(config)
    engine = RoutingEngine(config, topology, [lsr, dvr])

    boot_started = time.perf_counter()
    engine.initialize()
    boot_seconds = time.perf_counter() - boot_started
    print(f'boot_seconds={boot_seconds:.3f}')

    router_metrics = {
        'Oracle': new_metrics(),
        'LinkState': new_metrics(),
        'DistanceVector': new_metrics(),
    }
    pair_metrics = {
        pair: {
            name: new_metrics()
            for name in ('Oracle', 'LinkState', 'DistanceVector')
        }
        for pair in PAIRS
    }
    routers = (('LinkState', lsr), ('DistanceVector', dvr))
    sample_time = step
    while sample_time <= args.duration + 1e-9:
        engine.step_to(sample_time)
        snapshot = topology.get_snapshot(sample_time)
        for src, dst in PAIRS:
            key = (src, dst)
            truth_path, truth_delay = oracle_path(snapshot, src, dst)
            oracle_exists = truth_path is not None
            oracle_actual_delay = (
                path_delay_ms(snapshot, truth_path) if oracle_exists else None
            )
            record_sample(
                router_metrics['Oracle'], key, truth_path, 'correct',
                oracle_actual_delay, oracle_exists, truth_delay,
            )
            record_sample(
                pair_metrics[key]['Oracle'], key, truth_path, 'correct',
                oracle_actual_delay, oracle_exists, truth_delay,
            )
            for name, router in routers:
                route = router.compute_route(src, dst, sample_time)
                failure = router.last_failure
                category = classify(
                    route, failure, snapshot, truth_path, truth_delay
                )
                actual_delay = (
                    path_delay_ms(snapshot, route) if route is not None else None
                )
                record_sample(
                    router_metrics[name], key, route, category, actual_delay,
                    oracle_exists, truth_delay,
                )
                record_sample(
                    pair_metrics[key][name], key, route, category, actual_delay,
                    oracle_exists, truth_delay,
                )
        sample_time += step

    headers = [
        'Router', '%correct', '%stale_working', '%loop', '%blackhole',
        'mean actual delay ms', 'p99 actual delay ms', 'mean stretch vs oracle',
        'route changes/min', 'measured control messages', 'control bytes',
        'dropped messages', 'SPF runs', 'cost events generated', 'boot messages',
    ]
    rows = [
        make_row('Oracle', router_metrics['Oracle'], args.duration, oracle_row=True),
        make_row(
            'LinkState', router_metrics['LinkState'], args.duration,
            lsr.control_message_count, lsr.control_bytes_count,
            lsr.dropped_message_count, lsr.spf_runs,
            engine.cost_events_generated, lsr.boot_message_count,
        ),
        make_row(
            'DistanceVector', router_metrics['DistanceVector'], args.duration,
            dvr.control_message_count, dvr.control_bytes_count,
            dvr.dropped_message_count, None,
            engine.cost_events_generated, dvr.boot_message_count,
        ),
    ]
    print_table(headers, rows)
    print('Per-pair breakdown (router: class counts; oracle_unreachable; mean actual delay ms)')
    for pair in PAIRS:
        print(f'{pair[0]} -> {pair[1]}')
        for name in ('Oracle', 'LinkState', 'DistanceVector'):
            metrics = pair_metrics[pair][name]
            class_counts = ', '.join(
                f'{category}={metrics["classes"][category]}'
                for category in CLASSES
            )
            print(
                f'  {name}: {class_counts}; '
                f'oracle_unreachable={metrics["oracle_unreachable"]}; '
                f'mean_actual_delay_ms={format_metric(mean(metrics["delays"]))}'
            )
    print(f'total_runtime_seconds={time.perf_counter() - overall_started:.3f}')


if __name__ == '__main__':
    main()

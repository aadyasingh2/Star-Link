import argparse
import cProfile
import csv
import io
import os
import pstats
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
from src.routing.sapr import SAPRRouter


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
        'near_optimal': 0,
        'delays': [],
        'stretches': [],
        'route_changes': 0,
        'previous_paths': {},
        'samples': 0,
    }


def record_sample(
    metrics, key, path, category, actual_delay, oracle_exists, oracle_delay,
    near_optimal_tolerance_ms,
):
    metrics['samples'] += 1
    if not oracle_exists:
        metrics['oracle_unreachable'] += 1
    else:
        metrics['classes'][category] += 1
        if (
            category in ('correct', 'stale_working')
            and actual_delay is not None
            and oracle_delay is not None
            and actual_delay <= oracle_delay + near_optimal_tolerance_ms
        ):
            metrics['near_optimal'] += 1
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
    near_optimal_percentage = (
        metrics['near_optimal'] * 100.0 / reachable_samples
        if reachable_samples else 0.0
    )
    return [
        name,
        *(f'{percentages[category]:.2f}' for category in CLASSES),
        f'{near_optimal_percentage:.2f}',
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


def format_table(headers, rows):
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    lines = [
        ' | '.join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        '-+-'.join('-' * width for width in widths),
    ]
    lines.extend(
        ' | '.join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return '\n'.join(line.rstrip() for line in lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=600.0)
    parser.add_argument('--step', type=float, default=None)
    parser.add_argument('--out', default='results/comparison_600s.txt')
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
    near_optimal_tolerance_ms = float(
        config['evaluation']['near_optimal_tolerance_ms']
    )
    random.seed(seed)
    np.random.seed(seed)
    overall_started = time.perf_counter()

    constellation = Constellation(config)
    topology = TopologySeries(config, constellation)
    lsr = LinkStateRouter(config)
    dvr = DistanceVectorRouter(config)
    sapr = SAPRRouter(config)
    engine = RoutingEngine(config, topology, [lsr, dvr, sapr])

    boot_started = time.perf_counter()
    engine.initialize()
    boot_seconds = time.perf_counter() - boot_started

    router_metrics = {
        'Oracle': new_metrics(),
        'LinkState': new_metrics(),
        'DistanceVector': new_metrics(),
        'SAPR': new_metrics(),
    }
    pair_metrics = {
        pair: {
            name: new_metrics()
            for name in ('Oracle', 'LinkState', 'DistanceVector', 'SAPR')
        }
        for pair in PAIRS
    }
    routers = (('LinkState', lsr), ('DistanceVector', dvr), ('SAPR', sapr))
    sample_rows = []
    ny_tokyo_diagnostics = []
    sapr_profiler = cProfile.Profile()
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
                near_optimal_tolerance_ms,
            )
            record_sample(
                pair_metrics[key]['Oracle'], key, truth_path, 'correct',
                oracle_actual_delay, oracle_exists, truth_delay,
                near_optimal_tolerance_ms,
            )
            sample_rows.append([
                sample_time, f'{src}->{dst}', 'Oracle',
                'correct' if oracle_exists else 'oracle_unreachable',
                oracle_actual_delay,
            ])
            for name, router in routers:
                if name == 'SAPR':
                    sapr_profiler.enable()
                    route = router.compute_route(src, dst, sample_time)
                    sapr_profiler.disable()
                else:
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
                    oracle_exists, truth_delay, near_optimal_tolerance_ms,
                )
                record_sample(
                    pair_metrics[key][name], key, route, category, actual_delay,
                    oracle_exists, truth_delay, near_optimal_tolerance_ms,
                )
                if (
                    name == 'LinkState'
                    and key == ('gs_New York', 'gs_Tokyo')
                    and category == 'stale_working'
                    and len(ny_tokyo_diagnostics) < 5
                ):
                    oracle_edges = {
                        tuple(sorted((u, v), key=str))
                        for u, v in zip(truth_path, truth_path[1:])
                    }
                    non_oracle_links = []
                    for u, v in zip(route, route[1:]):
                        if tuple(sorted((u, v), key=str)) in oracle_edges:
                            continue
                        non_oracle_links.append({
                            'u': u,
                            'v': v,
                            'true_delay_ms': path_delay_ms(snapshot, [u, v]),
                            'stored_cost_ms': (
                                lsr.global_lsdb[u].get(u, {}).get(v)
                            ),
                        })
                    ny_tokyo_diagnostics.append({
                        't': sample_time,
                        'oracle_path': truth_path,
                        'oracle_delay_ms': truth_delay,
                        'link_state_path': route,
                        'link_state_delay_ms': actual_delay,
                        'non_oracle_links': non_oracle_links,
                    })
                sample_rows.append([
                    sample_time, f'{src}->{dst}', name,
                    category if oracle_exists else 'oracle_unreachable',
                    actual_delay,
                ])
        if time.perf_counter() - overall_started > 480.0:
            sapr_stats = io.StringIO()
            pstats.Stats(sapr_profiler, stream=sapr_stats).sort_stats(
                'cumulative'
            ).print_stats(15)
            print('SAPR profiling output:')
            print(sapr_stats.getvalue(), end='')
            return
        sample_time += step

    headers = [
        'Router', '%correct', '%stale_working', '%loop', '%blackhole',
        '%near_optimal',
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
        make_row(
            'SAPR', router_metrics['SAPR'], args.duration,
            sapr.control_message_count, sapr.control_bytes_count,
            sapr.dropped_message_count, None,
            engine.cost_events_generated, sapr.boot_message_count,
        ),
    ]
    output_lines = [
        f'seed={seed}',
        *(f'{key}={value}' for key, value in flatten_config(config)),
        f'boot_seconds={boot_seconds:.3f}',
        format_table(headers, rows),
        'Per-pair breakdown (router: class counts; oracle_unreachable; '
        '%near_optimal; mean actual delay ms)',
    ]
    for pair in PAIRS:
        output_lines.append(f'{pair[0]} -> {pair[1]}')
        for name in ('Oracle', 'LinkState', 'DistanceVector', 'SAPR'):
            metrics = pair_metrics[pair][name]
            class_counts = ', '.join(
                f'{category}={metrics["classes"][category]}'
                for category in CLASSES
            )
            reachable_samples = sum(metrics['classes'].values())
            near_optimal_percentage = (
                metrics['near_optimal'] * 100.0 / reachable_samples
                if reachable_samples else 0.0
            )
            output_lines.append(
                f'  {name}: {class_counts}; '
                f'oracle_unreachable={metrics["oracle_unreachable"]}; '
                f'near_optimal={near_optimal_percentage:.2f}% '
                f'({metrics["near_optimal"]}/{reachable_samples}); '
                f'mean_actual_delay_ms={format_metric(mean(metrics["delays"]))}'
            )
    output_lines.append(
        'Diagnostic: gs_New York -> gs_Tokyo; first five LinkState '
        'stale_working samples'
    )
    if ny_tokyo_diagnostics:
        for sample in ny_tokyo_diagnostics:
            oracle_path_text = ' -> '.join(sample['oracle_path'])
            link_state_path_text = ' -> '.join(sample['link_state_path'])
            output_lines.append(
                f'  t={sample["t"]:.1f}s; oracle_path={oracle_path_text}; '
                f'oracle_delay_ms={sample["oracle_delay_ms"]:.3f}; '
                f'LinkState_path={link_state_path_text}; '
                f'LinkState_delay_ms={sample["link_state_delay_ms"]:.3f}'
            )
            for link in sample['non_oracle_links']:
                stored_cost = link['stored_cost_ms']
                output_lines.append(
                    f'    {link["u"]} -> {link["v"]}: '
                    f'true_delay_ms={link["true_delay_ms"]:.3f}; '
                    f'stored_cost_ms={format_metric(stored_cost)}'
                )
    else:
        output_lines.append('  No stale_working samples found.')
    runtime_s = time.perf_counter() - overall_started
    output_lines.append(f'total_runtime_seconds={runtime_s:.3f}')
    output_text = '\n'.join(output_lines) + '\n'

    output_path = args.out
    if not os.path.isabs(output_path):
        output_path = os.path.join(root, output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as output_file:
        output_file.write(output_text)

    csv_path = os.path.splitext(output_path)[0] + '.csv'
    samples_path = os.path.splitext(output_path)[0] + '_samples.csv'
    with open(csv_path, 'w', encoding='utf-8', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)
    with open(samples_path, 'w', encoding='utf-8', newline='') as samples_file:
        writer = csv.writer(samples_file)
        writer.writerow(['t', 'pair', 'router', 'class', 'delay_ms'])
        writer.writerows(sample_rows)
    print(output_text, end='')



if __name__ == '__main__':
    main()

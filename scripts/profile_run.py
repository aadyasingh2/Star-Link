import os
import sys
import time

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.constellation import Constellation, TopologySeries
from src.routing.distance_vector import DistanceVectorRouter
from src.routing.engine import RoutingEngine
from src.routing.link_state import LinkStateRouter


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    with open(os.path.join(root, 'config.yaml'), encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file)
    if config['constellation'].get('isl_inter_plane_max_lat_deg') != 45:
        raise ValueError('profile_run requires the 45-degree ISL latitude cutoff')

    constellation = Constellation(config)
    topology = TopologySeries(config, constellation)
    lsr = LinkStateRouter(config)
    dvr = DistanceVectorRouter(config)
    engine = RoutingEngine(config, topology, [lsr, dvr])

    boot_started = time.perf_counter()
    engine.initialize()
    boot_seconds = time.perf_counter() - boot_started

    pairs = (
        ('gs_Chennai', 'gs_London'),
        ('gs_Chennai', 'gs_Sydney'),
        ('gs_New York', 'gs_Tokyo'),
    )
    run_started = time.perf_counter()
    engine._wall_clock_deadline = time.monotonic() + 180.0
    try:
        for t in range(1, 61):
            engine.step_to(float(t))
            if t % 5 == 0:
                for src, dst in pairs:
                    lsr.compute_route(src, dst, float(t))
                    dvr.compute_route(src, dst, float(t))
    except TimeoutError:
        run_seconds = time.perf_counter() - run_started
        print(f'boot_seconds: {boot_seconds:.3f}')
        print(f'60_step_run_seconds: {run_seconds:.3f} (exceeded 180-second guard)')
        print(f'events_processed: {engine.events_processed}')
        print(f'LS_spf_runs: {lsr.spf_runs}')
        print(f'LS_control_message_count: {lsr.control_message_count}')
        print(f'DV_control_message_count: {dvr.control_message_count}')
        return
    finally:
        engine._wall_clock_deadline = None
    run_seconds = time.perf_counter() - run_started

    print(f'boot_seconds: {boot_seconds:.3f}')
    print(f'60_step_run_seconds: {run_seconds:.3f}')
    print(f'events_processed: {engine.events_processed}')
    print(f'LS_spf_runs: {lsr.spf_runs}')
    print(f'LS_control_message_count: {lsr.control_message_count}')
    print(f'DV_control_message_count: {dvr.control_message_count}')
    print(f'extrapolated_600_step_seconds: {run_seconds * 10:.3f}')


if __name__ == '__main__':
    main()

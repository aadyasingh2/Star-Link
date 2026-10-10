import cProfile
import io
import os
import pstats
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

    constellation = Constellation(config)
    topology = TopologySeries(config, constellation)
    router_types = (LinkStateRouter, DistanceVectorRouter)

    for router_type in router_types:
        router = router_type(config)
        engine = RoutingEngine(config, topology, [router])
        profiler = cProfile.Profile()
        started = time.perf_counter()
        engine._wall_clock_deadline = time.monotonic() + 240.0
        timed_out = False
        profiler.enable()
        try:
            engine.initialize()
        except TimeoutError:
            timed_out = True
        finally:
            profiler.disable()
            engine._wall_clock_deadline = None
        elapsed = time.perf_counter() - started

        print(f'{router_type.__name__}:')
        print(f'  elapsed_seconds: {elapsed:.3f}')
        print(f'  events_processed: {engine.events_processed}')
        print(f'  spf_runs: {getattr(router, "spf_runs", "n/a")}')
        print(f'  boot_message_count: {router.boot_message_count}')
        print(f'  status: {"TIMED OUT (240-second guard)" if timed_out else "completed"}')
        print('  top_12_functions_by_cumulative_time:')
        stats_output = io.StringIO()
        pstats.Stats(profiler, stream=stats_output).sort_stats('cumulative').print_stats(12)
        print(stats_output.getvalue(), end='')


if __name__ == '__main__':
    main()

from scripts.baseline_report import make_row, new_metrics, record_sample


def test_near_optimal_counts_only_valid_paths_within_tolerance():
    metrics = new_metrics()
    samples = [
        ('correct', 100.0),
        ('stale_working', 101.0),
        ('stale_working', 101.001),
        ('blackhole', 10.0),
    ]
    for index, (category, actual_delay) in enumerate(samples):
        record_sample(
            metrics, (str(index), 'dst'), [str(index), 'dst'], category,
            actual_delay, True, 100.0, 1.0,
        )

    row = make_row('Router', metrics, 1.0)

    assert metrics['near_optimal'] == 2
    assert row[5] == '50.00'

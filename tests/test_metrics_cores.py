from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import cores


def test_cores_off_by_default(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    cores.collect(check, stats, [])
    assert not aggregator.metrics("uwsgi.worker.core.requests")


def test_cores_emitted_when_enabled(aggregator, stats):
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    cores.collect(check, stats, [])
    # All 7 metrics: 6 counters (including zero values) + 1 gauge
    aggregator.assert_metric(
        "uwsgi.worker.core.requests", value=1400,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.static_requests", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.routed_requests", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.offloaded_requests", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.write_errors", value=3,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.read_errors", value=1,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.in_request", value=1,
        metric_type=aggregator.GAUGE, tags=["worker_id:2", "core_id:0"],
    )

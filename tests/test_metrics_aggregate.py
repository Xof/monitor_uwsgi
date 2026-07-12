from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import aggregate


def _run(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    aggregate.collect(check, stats, ["env:test"])
    return check


def test_global_gauges(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric("uwsgi.listen_queue", value=3, tags=["env:test"], metric_type=aggregator.GAUGE)
    aggregator.assert_metric("uwsgi.signal_queue", value=0, tags=["env:test"], metric_type=aggregator.GAUGE)
    aggregator.assert_metric("uwsgi.workers.total", value=3, tags=["env:test"], metric_type=aggregator.GAUGE)


def test_listen_queue_errors_is_monotonic_count(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.listen_queue_errors",
        value=1,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["env:test"],
    )


def test_workers_by_status_buckets(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.workers.by_status", value=1, tags=["env:test", "status:idle"], metric_type=aggregator.GAUGE
    )
    aggregator.assert_metric(
        "uwsgi.workers.by_status", value=2, tags=["env:test", "status:busy"], metric_type=aggregator.GAUGE
    )
    aggregator.assert_metric(
        "uwsgi.workers.by_status", value=0, tags=["env:test", "status:cheap"], metric_type=aggregator.GAUGE
    )
    aggregator.assert_metric(
        "uwsgi.workers.by_status", value=0, tags=["env:test", "status:pause"], metric_type=aggregator.GAUGE
    )
    aggregator.assert_metric(
        "uwsgi.workers.by_status", value=0, tags=["env:test", "status:sig"], metric_type=aggregator.GAUGE
    )

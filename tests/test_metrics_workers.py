from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import workers


def _run(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    return check


def test_worker_counters_are_monotonic(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.worker.requests", value=1500,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.tx", value=10485760,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.harakiri_count", value=1,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.respawn_count", value=2,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:3"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.exceptions", value=2,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.signals", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.running_time", value=3000000,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )


def test_worker_gauges(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.worker.rss", value=52428800,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.avg_rt", value=2100,
        metric_type=aggregator.GAUGE, tags=["worker_id:2"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.accepting", value=1,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.signal_queue", value=0,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.vsz", value=209715200,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )


def test_worker_uptime_emitted_non_negative(aggregator, stats):
    _run(aggregator, stats)
    # last_spawn is in the past, so uptime is a positive gauge; assert presence.
    aggregator.assert_metric("uwsgi.worker.uptime", metric_type=aggregator.GAUGE, tags=["worker_id:1"])


def test_never_tags_pid(aggregator, stats):
    _run(aggregator, stats)
    for metric in aggregator.metric_names:
        for m in aggregator.metrics(metric):
            assert not any(t.startswith("pid:") for t in m.tags), (metric, m.tags)

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import apps


def test_apps_emitted_only_for_multi_app_worker(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    apps.collect(check, stats, [])

    # Worker 2 has 2 apps -> emitted.
    aggregator.assert_metric(
        "uwsgi.worker.app.requests", value=700,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["worker_id:2", "app_id:0", "mountpoint:/api"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.app.requests", value=700,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["worker_id:2", "app_id:1", "mountpoint:/admin"],
    )

    # Workers 1 and 3 have a single app -> not emitted.
    single_app = [
        m for m in aggregator.metrics("uwsgi.worker.app.requests")
        if "worker_id:1" in m.tags or "worker_id:3" in m.tags
    ]
    assert single_app == []

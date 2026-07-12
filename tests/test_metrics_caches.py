from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import caches


def test_cache_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    caches.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.cache.items", value=250, metric_type=aggregator.GAUGE, tags=["cache:default"]
    )
    aggregator.assert_metric(
        "uwsgi.cache.hits", value=8000,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["cache:default"],
    )
    aggregator.assert_metric(
        "uwsgi.cache.miss", value=1200,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["cache:default"],
    )
    aggregator.assert_metric(
        "uwsgi.cache.full", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["cache:default"],
    )


def test_no_caches_section_emits_nothing(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    stats_no_cache = dict(stats)
    stats_no_cache.pop("caches", None)
    caches.collect(check, stats_no_cache, [])
    assert not aggregator.metrics("uwsgi.cache.items")

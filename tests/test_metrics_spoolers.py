from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import spoolers


def test_spooler_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    spoolers.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.spooler.tasks", value=42,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["spooler:/var/spool/uwsgi"],
    )
    aggregator.assert_metric(
        "uwsgi.spooler.respawns", value=0,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["spooler:/var/spool/uwsgi"],
    )
    aggregator.assert_metric(
        "uwsgi.spooler.running", value=0,
        metric_type=aggregator.GAUGE, tags=["spooler:/var/spool/uwsgi"],
    )


def test_no_spoolers_section_emits_nothing(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    stats_no_spool = dict(stats)
    stats_no_spool.pop("spoolers", None)
    spoolers.collect(check, stats_no_spool, [])
    assert not aggregator.metrics("uwsgi.spooler.tasks")

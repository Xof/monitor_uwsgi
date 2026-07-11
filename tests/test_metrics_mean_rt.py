import copy

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import workers


def test_mean_rt_skipped_first_scrape_then_computed(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])

    # First scrape: no previous sample -> no mean_rt.
    workers.collect(check, stats, [])
    assert not aggregator.metrics("uwsgi.worker.mean_rt")

    # Second scrape: worker 1 advanced by 100 requests / 200000 us -> mean 2000 us.
    aggregator.reset()
    later = copy.deepcopy(stats)
    later["workers"][0]["requests"] = 1600
    later["workers"][0]["running_time"] = 3200000
    workers.collect(check, later, [])
    aggregator.assert_metric(
        "uwsgi.worker.mean_rt", value=2000.0,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )


def test_mean_rt_skipped_on_respawn(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    aggregator.reset()
    # Worker 1 respawned: counters reset below the previous sample.
    respawned = copy.deepcopy(stats)
    respawned["workers"][0]["requests"] = 5
    respawned["workers"][0]["running_time"] = 1000
    workers.collect(check, respawned, [])
    mean_rt_w1 = [m for m in aggregator.metrics("uwsgi.worker.mean_rt") if "worker_id:1" in m.tags]
    assert mean_rt_w1 == []

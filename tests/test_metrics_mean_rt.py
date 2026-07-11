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


def test_mean_rt_skipped_when_only_running_time_regresses(aggregator, stats):
    # d_req > 0 but d_rt < 0 (counter anomaly) -> the d_rt >= 0 half of the guard must skip.
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    aggregator.reset()
    weird = copy.deepcopy(stats)
    weird["workers"][0]["requests"] = 1600          # +100 (up)
    weird["workers"][0]["running_time"] = 2000000   # below prior 3000000 (down)
    workers.collect(check, weird, [])
    assert [m for m in aggregator.metrics("uwsgi.worker.mean_rt") if "worker_id:1" in m.tags] == []


def test_mean_rt_skipped_when_no_new_requests(aggregator, stats):
    # d_req == 0 (flat scrape) -> skip, and must not raise ZeroDivisionError.
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    aggregator.reset()
    flat = copy.deepcopy(stats)  # identical counters -> d_req == 0
    workers.collect(check, flat, [])
    assert [m for m in aggregator.metrics("uwsgi.worker.mean_rt") if "worker_id:1" in m.tags] == []


def test_mean_rt_recovers_after_respawn(aggregator, stats):
    # After a respawn (scrape 2 skipped), the baseline must advance to the respawn values
    # so scrape 3 computes correctly from them instead of wedging on the pre-respawn baseline.
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])                    # scrape 1: baseline 1500 / 3000000
    respawned = copy.deepcopy(stats)
    respawned["workers"][0]["requests"] = 5
    respawned["workers"][0]["running_time"] = 1000
    workers.collect(check, respawned, [])                # scrape 2: respawn -> skip; baseline now 5 / 1000
    aggregator.reset()
    after = copy.deepcopy(stats)
    after["workers"][0]["requests"] = 15                 # +10 from 5
    after["workers"][0]["running_time"] = 3000           # +2000 from 1000 -> 2000/10 = 200.0
    workers.collect(check, after, [])
    aggregator.assert_metric(
        "uwsgi.worker.mean_rt", value=200.0,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )

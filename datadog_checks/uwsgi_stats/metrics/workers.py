"""Per-worker uWSGI metrics.

Counters (requests, tx, ...) are cumulative since the worker first started and
reset to 0 on respawn; monotonic_count handles the reset. Every metric is tagged
worker_id (bounded) -- never pid (churns on respawn). running_time and avg_rt are
microseconds; rss/vsz are bytes and stay 0 unless uWSGI runs with --memory-report.

mean_rt is the honest per-scrape latency (delta running_time / delta requests, us);
it needs the previous scrape's sample, kept on check._prev_worker keyed by worker id.
"""

import time

_COUNTERS = (
    "requests",
    "tx",
    "exceptions",
    "harakiri_count",
    "signals",
    "running_time",
    "respawn_count",
)
_GAUGES = (
    "signal_queue",
    "avg_rt",
    "rss",
    "vsz",
    "accepting",
)


def collect(check, stats, base_tags):
    now = time.time()
    for worker in stats.get("workers", []):
        wid = worker.get("id")
        tags = base_tags + ["worker_id:%s" % wid]
        for name in _COUNTERS:
            if name in worker:
                check.monotonic_count("worker.%s" % name, worker[name], tags=tags)
        for name in _GAUGES:
            if name in worker:
                check.gauge("worker.%s" % name, worker[name], tags=tags)
        if "last_spawn" in worker:
            check.gauge("worker.uptime", now - worker["last_spawn"], tags=tags)

        _emit_mean_rt(check, worker, wid, tags)


def _emit_mean_rt(check, worker, wid, tags):
    requests = worker.get("requests")
    running_time = worker.get("running_time")
    if requests is None or running_time is None:
        return
    prev = check._prev_worker.get(wid)
    check._prev_worker[wid] = (requests, running_time)
    if prev is None:
        return
    d_req = requests - prev[0]
    d_rt = running_time - prev[1]
    if d_req > 0 and d_rt >= 0:
        check.gauge("worker.mean_rt", d_rt / d_req, tags=tags)

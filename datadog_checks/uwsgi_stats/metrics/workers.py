"""Per-worker uWSGI metrics.

Counters (requests, tx, ...) are cumulative since the worker first started and
reset to 0 on respawn; monotonic_count handles the reset. Every metric is tagged
worker_id (bounded) -- never pid (churns on respawn). running_time and avg_rt are
microseconds; rss/vsz are bytes and stay 0 unless uWSGI runs with --memory-report.
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
        tags = base_tags + ["worker_id:%s" % worker.get("id")]
        for name in _COUNTERS:
            if name in worker:
                check.monotonic_count("worker.%s" % name, worker[name], tags=tags)
        for name in _GAUGES:
            if name in worker:
                check.gauge("worker.%s" % name, worker[name], tags=tags)
        if "last_spawn" in worker:
            check.gauge("worker.uptime", now - worker["last_spawn"], tags=tags)

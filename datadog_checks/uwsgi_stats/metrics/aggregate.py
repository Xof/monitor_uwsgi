"""Global / aggregate uWSGI metrics: listen queue, signal queue, worker counts.

top-level `load` is intentionally not emitted: the uWSGI source sets it to a copy
of the backlog with a TODO, so it duplicates `listen_queue`.
"""

from collections import Counter

_STATUS_BUCKETS = ("idle", "busy", "cheap", "pause", "sig")


def collect(check, stats, base_tags):
    if "listen_queue" in stats:
        check.gauge("listen_queue", stats["listen_queue"], tags=base_tags)
    if "listen_queue_errors" in stats:
        check.monotonic_count("listen_queue_errors", stats["listen_queue_errors"], tags=base_tags)
    if "signal_queue" in stats:
        check.gauge("signal_queue", stats["signal_queue"], tags=base_tags)

    workers = stats.get("workers", [])
    check.gauge("workers.total", len(workers), tags=base_tags)

    counts = Counter()
    for worker in workers:
        status = worker.get("status", "idle")
        counts["sig" if status.startswith("sig") else status] += 1
    for bucket in _STATUS_BUCKETS:
        check.gauge(
            "workers.by_status", counts.get(bucket, 0), tags=base_tags + ["status:%s" % bucket]
        )

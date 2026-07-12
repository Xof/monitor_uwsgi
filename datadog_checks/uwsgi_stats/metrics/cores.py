"""Per-core uWSGI metrics; opt-in via the instance flag `collect_per_core`.

High cardinality and ~duplicates per-worker request counts on the common
single-core (sync) setup, so it is off by default. Absent under --stats-no-cores.
"""

_COUNTERS = (
    "requests",
    "static_requests",
    "routed_requests",
    "offloaded_requests",
    "write_errors",
    "read_errors",
)


def collect(check, stats, base_tags):
    if not check.instance.get("collect_per_core", False):
        return
    for worker in stats.get("workers", []):
        wid = worker.get("id")
        for core in worker.get("cores", []):
            tags = base_tags + ["worker_id:%s" % wid, "core_id:%s" % core.get("id")]
            for name in _COUNTERS:
                if name in core:
                    check.monotonic_count("worker.core.%s" % name, core[name], tags=tags)
            if "in_request" in core:
                check.gauge("worker.core.in_request", core["in_request"], tags=tags)

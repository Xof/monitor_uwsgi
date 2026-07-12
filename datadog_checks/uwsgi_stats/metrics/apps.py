"""Per-app uWSGI metrics, emitted only when a worker mounts more than one app.

Single-app workers duplicate the worker-level request/exception counters, so
gating on len(apps) > 1 avoids doubled series in the common single-app case.
"""


def collect(check, stats, base_tags):
    for worker in stats.get("workers", []):
        apps = worker.get("apps", [])
        if len(apps) <= 1:
            continue
        wid = worker.get("id")
        for app in apps:
            tags = base_tags + [
                "worker_id:%s" % wid,
                "app_id:%s" % app.get("id"),
                "mountpoint:%s" % app.get("mountpoint", ""),
            ]
            if "requests" in app:
                check.monotonic_count("worker.app.requests", app["requests"], tags=tags)
            if "exceptions" in app:
                check.monotonic_count("worker.app.exceptions", app["exceptions"], tags=tags)

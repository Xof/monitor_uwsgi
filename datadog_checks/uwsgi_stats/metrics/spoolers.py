"""Per-spooler uWSGI metrics; present only when spoolers are configured."""

_COUNTERS = ("tasks", "respawns")


def collect(check, stats, base_tags):
    for spooler in stats.get("spoolers", []):
        tags = base_tags + ["spooler:%s" % spooler.get("dir", "")]
        for name in _COUNTERS:
            if name in spooler:
                check.monotonic_count("spooler.%s" % name, spooler[name], tags=tags)
        if "running" in spooler:
            check.gauge("spooler.running", spooler["running"], tags=tags)

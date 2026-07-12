"""Per-cache uWSGI metrics; present only when caches are configured."""

_COUNTERS = ("hits", "miss", "full")


def collect(check, stats, base_tags):
    for cache in stats.get("caches", []):
        tags = base_tags + ["cache:%s" % cache.get("name", "")]
        if "items" in cache:
            check.gauge("cache.items", cache["items"], tags=tags)
        for name in _COUNTERS:
            if name in cache:
                check.monotonic_count("cache.%s" % name, cache[name], tags=tags)

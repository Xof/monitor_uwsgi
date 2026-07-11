"""uWSGI stats Agent check.

Reads the uWSGI stats server once per collection cycle and dispatches the parsed
snapshot to a registry of section collectors. Cumulative counters are submitted
via monotonic_count (the Agent diffs samples and swallows the reset when a worker
respawns); instantaneous values via gauge.
"""

from datadog_checks.base import AgentCheck, ConfigurationError

from .metrics import COLLECTORS
from .stats import read_stats


class UwsgiStatsCheck(AgentCheck):
    __NAMESPACE__ = "uwsgi"

    def __init__(self, name, init_config, instances):
        super().__init__(name, init_config, instances)
        self._prev_worker = {}

    def check(self, instance):
        stats_url = instance.get("stats_url")
        if not stats_url:
            raise ConfigurationError("uwsgi_stats: 'stats_url' is required")

        base_tags = list(instance.get("tags") or [])
        timeout = float(instance.get("timeout", 5))

        try:
            stats = read_stats(stats_url, timeout)
        except ConfigurationError:
            raise
        except Exception as exc:
            self.service_check(
                "can_connect", AgentCheck.CRITICAL, tags=base_tags, message=str(exc)
            )
            raise

        self.service_check("can_connect", AgentCheck.OK, tags=base_tags)

        for collect in COLLECTORS:
            collect(self, stats, base_tags)

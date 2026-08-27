"""uWSGI stats Agent check.

Reads the uWSGI stats server once per collection cycle and dispatches the parsed
snapshot to a registry of section collectors. Cumulative counters are submitted
via monotonic_count (the Agent diffs samples and swallows the reset when a worker
respawns); instantaneous values via gauge.
"""

from datadog_checks.base import AgentCheck, ConfigurationError

from .health import evaluate_saturation
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
            # LOAD-BEARING; do not "tidy" this into a `return`. The Agent
            # collector marks an instance [ERROR] only when check() RAISES, and
            # that marker is the only machine-readable signal that a scrape
            # failed -- `datadog-agent check` exits 0 whether the check
            # succeeded or errored, so its exit status answers "did the check
            # RUN", not "did it SUCCEED". An out-of-repo deploy consumer reads
            # the marker to decide whether a host's uWSGI vassal is answering,
            # so the common catch-CRITICAL-and-return idiom would report a
            # refused stats socket as [OK] and converge a dead vassal green.
            # See ADR 0005; test_can_connect_critical_and_reraise_on_failure
            # is the guard.
            raise

        self.service_check("can_connect", AgentCheck.OK, tags=base_tags)

        for collect in COLLECTORS:
            collect(self, stats, base_tags)

        status, message = evaluate_saturation(stats, instance)
        # Convention (matches can_connect above): the Agent drops the message on
        # OK checks, and the test stub enforces that by raising if one is sent.
        self.service_check(
            "worker_saturation",
            status,
            tags=base_tags,
            message=message if status != AgentCheck.OK else None,
        )

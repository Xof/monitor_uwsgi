from datadog_checks.base import AgentCheck


class UwsgiStatsCheck(AgentCheck):
    __NAMESPACE__ = "uwsgi"

    def check(self, instance):
        pass

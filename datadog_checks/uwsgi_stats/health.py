"""Derived capacity/health check for uWSGI.

Keys on the classic 'add workers' signal: the socket listen queue filling because
workers cannot keep up. Ratio thresholds are configurable per instance so alerting
is tunable without redeploying the check.
"""

from datadog_checks.base import AgentCheck


def evaluate_saturation(stats, instance):
    warn = float(instance.get("worker_saturation_warning", 0.5))
    crit = float(instance.get("worker_saturation_critical", 0.9))

    ratios = []
    for sock in stats.get("sockets", []):
        max_queue = sock.get("max_queue")
        queue = sock.get("queue")
        if max_queue and queue is not None and max_queue > 0:
            ratios.append(queue / max_queue)

    workers = stats.get("workers", [])
    non_cheap = [w for w in workers if w.get("status") != "cheap"]
    all_busy = bool(non_cheap) and all(w.get("status") == "busy" for w in non_cheap)
    listen_queue = stats.get("listen_queue", 0)
    busy = sum(1 for w in workers if w.get("status") == "busy")
    total = len(workers)

    if ratios:
        sat = max(ratios)
        message = "queue fill %.2f; workers busy %d/%d" % (sat, busy, total)
        if sat >= crit:
            return AgentCheck.CRITICAL, message
        if sat >= warn or (all_busy and listen_queue > 0):
            return AgentCheck.WARNING, message
        return AgentCheck.OK, message

    if all_busy and listen_queue > 0:
        return AgentCheck.WARNING, "all %d workers busy, listen_queue=%d" % (total, listen_queue)
    return AgentCheck.OK, "workers busy %d/%d" % (busy, total)

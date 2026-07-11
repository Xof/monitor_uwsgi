from datadog_checks.base import AgentCheck
from datadog_checks.uwsgi_stats.health import evaluate_saturation


def _stats(queue, max_queue, statuses, listen_queue=0):
    return {
        "listen_queue": listen_queue,
        "sockets": [{"name": "s", "proto": "uwsgi", "queue": queue, "max_queue": max_queue}],
        "workers": [{"id": i + 1, "status": s} for i, s in enumerate(statuses)],
    }


def test_ok_when_queue_low():
    status, _ = evaluate_saturation(_stats(10, 100, ["idle", "busy"]), {})
    assert status == AgentCheck.OK


def test_warning_at_half_full():
    status, msg = evaluate_saturation(_stats(50, 100, ["busy", "busy"]), {})
    assert status == AgentCheck.WARNING
    assert "0.50" in msg


def test_critical_at_ninety_percent():
    status, _ = evaluate_saturation(_stats(90, 100, ["busy", "busy"]), {})
    assert status == AgentCheck.CRITICAL


def test_warning_all_busy_with_listen_queue_and_no_max_queue():
    stats = {
        "listen_queue": 4,
        "sockets": [{"name": "s", "proto": "uwsgi", "queue": 4, "max_queue": 0}],
        "workers": [{"id": 1, "status": "busy"}, {"id": 2, "status": "busy"}],
    }
    status, _ = evaluate_saturation(stats, {})
    assert status == AgentCheck.WARNING


def test_ok_no_max_queue_and_not_all_busy():
    stats = {
        "listen_queue": 0,
        "sockets": [{"name": "s", "proto": "uwsgi", "queue": 0, "max_queue": 0}],
        "workers": [{"id": 1, "status": "idle"}, {"id": 2, "status": "busy"}],
    }
    status, _ = evaluate_saturation(stats, {})
    assert status == AgentCheck.OK


def test_custom_thresholds():
    instance = {"worker_saturation_warning": 0.2, "worker_saturation_critical": 0.4}
    status, _ = evaluate_saturation(_stats(30, 100, ["idle"]), instance)
    assert status == AgentCheck.WARNING


def test_wired_into_check(aggregator, instance, stats, monkeypatch):
    from datadog_checks.uwsgi_stats import UwsgiStatsCheck
    from datadog_checks.uwsgi_stats import check as check_module

    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)
    # fixture: socket queue 3/100 -> sat 0.03, workers idle+busy+busy -> OK
    aggregator.assert_service_check("uwsgi.worker_saturation", status=UwsgiStatsCheck.OK)

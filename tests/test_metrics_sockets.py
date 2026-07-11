from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import sockets


def test_socket_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    sockets.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.socket.queue",
        value=3,
        tags=["socket_name:127.0.0.1:8000", "proto:uwsgi"],
    )
    aggregator.assert_metric(
        "uwsgi.socket.max_queue",
        value=100,
        tags=["socket_name:/tmp/app.sock", "proto:http"],
    )

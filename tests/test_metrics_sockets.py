from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import sockets


def test_socket_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    sockets.collect(check, stats, [])
    # socket 1 (127.0.0.1:8000, uwsgi)
    aggregator.assert_metric(
        "uwsgi.socket.queue", value=3, metric_type=aggregator.GAUGE,
        tags=["socket_name:127.0.0.1:8000", "proto:uwsgi"],
    )
    aggregator.assert_metric(
        "uwsgi.socket.max_queue", value=100, metric_type=aggregator.GAUGE,
        tags=["socket_name:127.0.0.1:8000", "proto:uwsgi"],
    )
    # socket 2 (/tmp/app.sock, http) -- queue is 0, exercising the "in sock" guard's falsy-zero handling
    aggregator.assert_metric(
        "uwsgi.socket.queue", value=0, metric_type=aggregator.GAUGE,
        tags=["socket_name:/tmp/app.sock", "proto:http"],
    )
    aggregator.assert_metric(
        "uwsgi.socket.max_queue", value=100, metric_type=aggregator.GAUGE,
        tags=["socket_name:/tmp/app.sock", "proto:http"],
    )

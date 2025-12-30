from datadog import statsd

def total_queue_depth(structure):
    queue_depth = 0

    for uwsgi_socket in structure["sockets"]:
        queue_depth += uwsgi_socket["queue"]

    statsd.gauge(f"uwsgi.total_queue_depth", queue_depth)
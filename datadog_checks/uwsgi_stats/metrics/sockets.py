"""Per-socket uWSGI metrics: current and max listen-queue depth."""


def collect(check, stats, base_tags):
    for sock in stats.get("sockets", []):
        tags = base_tags + [
            "socket_name:%s" % sock.get("name", ""),
            "proto:%s" % sock.get("proto", ""),
        ]
        if "queue" in sock:
            check.gauge("socket.queue", sock["queue"], tags=tags)
        if "max_queue" in sock:
            check.gauge("socket.max_queue", sock["max_queue"], tags=tags)

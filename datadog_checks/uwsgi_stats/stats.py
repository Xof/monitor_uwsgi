"""Transport + parsing for the uWSGI stats server.

The uWSGI stats server writes a single JSON object and closes the connection --
there is no length prefix or framing, so a single recv() truncates non-trivial
payloads. We always read to EOF, then parse. Transport is chosen from the URL
scheme so the same check works whether uWSGI exposes --stats over TCP, a UNIX
socket, or --stats-http.
"""

import json
import socket
from urllib.parse import urlparse

from datadog_checks.base import ConfigurationError

_BUFSIZE = 4096


def read_stats(stats_url, timeout=5):
    if not stats_url:
        raise ConfigurationError("uwsgi_stats: 'stats_url' is required")

    parsed = urlparse(stats_url)
    scheme = parsed.scheme

    if scheme == "tcp":
        raw = _read_socket(socket.AF_INET, (parsed.hostname, parsed.port), timeout)
    elif scheme == "unix":
        raw = _read_socket(socket.AF_UNIX, parsed.path, timeout)
    elif scheme in ("http", "https"):
        raw = _read_http(stats_url, timeout)
    else:
        raise ConfigurationError(
            "uwsgi_stats: unsupported stats_url scheme %r "
            "(expected tcp://, unix://, or http://)" % scheme
        )

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "uwsgi_stats: invalid or truncated JSON from %s: %s" % (stats_url, exc)
        ) from exc


def _read_socket(family, address, timeout):
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(address)
        chunks = []
        while True:
            block = sock.recv(_BUFSIZE)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks).decode("utf-8")
    finally:
        sock.close()


def _read_http(url, timeout):
    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text

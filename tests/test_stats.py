import os
import shutil
import socket
import tempfile
import threading

import pytest

from datadog_checks.base import ConfigurationError
from datadog_checks.uwsgi_stats.stats import read_stats

HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "fixtures", "stats.json"), "rb") as _fh:
    PAYLOAD = _fh.read()


def _serve_once_tcp(payload):
    """Bind a loopback TCP server that writes payload then closes. Returns (host, port, thread)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()

    def run():
        conn, _ = srv.accept()
        with conn:
            conn.sendall(payload)
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return host, port, t


def test_read_tcp_reassembles_full_payload():
    host, port, t = _serve_once_tcp(PAYLOAD)
    result = read_stats(f"tcp://{host}:{port}", timeout=5)
    t.join(timeout=5)
    assert result["version"] == "2.0.24"
    assert len(result["workers"]) == 3


def test_read_unix_socket():
    # ponytail: pytest's tmp_path nests under a long macOS temp prefix that
    # blows AF_UNIX's ~104-byte sun_path limit; bind directly under /tmp
    # (short, and not subject to a deep TMPDIR) instead.
    sock_dir = tempfile.mkdtemp(dir="/tmp")
    try:
        sock_path = os.path.join(sock_dir, "stats.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)

        def run():
            conn, _ = srv.accept()
            with conn:
                conn.sendall(PAYLOAD)
            srv.close()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        result = read_stats(f"unix://{sock_path}", timeout=5)
        t.join(timeout=5)
        assert result["workers"][0]["id"] == 1
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_truncated_json_raises_valueerror():
    host, port, t = _serve_once_tcp(PAYLOAD[: len(PAYLOAD) // 2])
    with pytest.raises(ValueError):
        read_stats(f"tcp://{host}:{port}", timeout=5)
    t.join(timeout=5)


def test_missing_url_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        read_stats("", timeout=5)


def test_unsupported_scheme_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        read_stats("ftp://127.0.0.1:21", timeout=5)

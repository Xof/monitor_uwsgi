# uWSGI Datadog Agent Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cron + DogStatsD single-metric script with a packaged Datadog Agent custom check (`uwsgi_stats`) that reads the uWSGI stats server and ships the full, correctly-typed metric set (global, per-worker, per-socket, with conditional per-app/cache/spooler and opt-in per-core), plus `can_connect` and `worker_saturation` service checks.

**Architecture:** A `UwsgiStatsCheck(AgentCheck)` with `__NAMESPACE__ = 'uwsgi'`. `check()` reads the stats JSON via `stats.read_stats(url, timeout)` (transport dispatch + read-to-EOF + parse), submits `can_connect`, iterates a `COLLECTORS` registry of small per-section `collect(check, stats, base_tags)` functions, then submits `worker_saturation`. Counters use `monotonic_count` (respawn-reset-safe); gauges use `gauge`. Packaged as an installable wheel under the `datadog_checks` namespace.

**Tech Stack:** Python (Agent embedded interpreter, target `>=3.8`), `datadog-checks-base` (only runtime dep), stdlib `socket`/`json`/`urllib`, `datadog-checks-dev` + `pytest` for tests, `ruff` + `mypy` for lint/type, GitHub Actions for CI.

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec):

- **Python floor:** `requires-python = ">=3.8"` — the check runs in the Datadog Agent's embedded Python, **not** system 3.13. Do not use 3.9+-only syntax.
- **Runtime dependencies:** only `datadog-checks-base`. Everything else is stdlib (`socket`, `json`, `urllib`). `requests` may be used for the `http://` transport only (it is bundled in the Agent). Do **not** add new third-party runtime deps.
- **Integration / check name:** `uwsgi_stats`. **Metric namespace:** `uwsgi.` (via `__NAMESPACE__ = 'uwsgi'`; pass metric/service-check names *without* the `uwsgi.` prefix — the base class prepends it).
- **Counter vs gauge:** cumulative counters (`requests`, `tx`, `exceptions`, `harakiri_count`, `signals`, `running_time`, `respawn_count`, `listen_queue_errors`, cache `hits`/`miss`/`full`, spooler `tasks`/`respawns`, app/core `requests`/`exceptions`/`*_requests`/`*_errors`) → `monotonic_count`. Instantaneous values → `gauge`.
- **Tagging:** worker metrics tagged `worker_id:<id>` — **never** `pid` (unbounded across respawns). Collectors must not mutate `base_tags`; build new lists.
- **Defensive parsing:** `.get()` for every optional section/field; skip absent sections (`cores`/`caches`/`spoolers`/`listen_queue*` are feature- or OS-gated).
- **Units:** `running_time`/`avg_rt` are microseconds; `rss`/`vsz`/`tx` are bytes; `last_spawn` is epoch seconds. Document, never convert silently.
- **Commit messages:** no Claude/AI references, no `Co-Authored-By` trailer (project convention).

---

## File Structure

| File | Responsibility | Introduced in |
|---|---|---|
| `pyproject.toml` | Build config, dep pin, ruff/mypy config | Task 1 |
| `datadog_checks/uwsgi_stats/__about__.py` | `__version__` | Task 1 |
| `datadog_checks/uwsgi_stats/__init__.py` | Export `UwsgiStatsCheck`, `__version__` | Task 1 |
| `datadog_checks/uwsgi_stats/check.py` | `UwsgiStatsCheck`: orchestration + service checks | Task 1 (skeleton), Task 3 (logic), Task 7 (state), Task 12 (health) |
| `datadog_checks/uwsgi_stats/stats.py` | `read_stats(url, timeout)` transport + parse | Task 2 |
| `datadog_checks/uwsgi_stats/metrics/__init__.py` | `COLLECTORS` registry | Task 3, appended Tasks 4–11 |
| `datadog_checks/uwsgi_stats/metrics/aggregate.py` | Global rollups + `workers.by_status`/`total` | Task 4 |
| `datadog_checks/uwsgi_stats/metrics/sockets.py` | Per-socket metrics | Task 5 |
| `datadog_checks/uwsgi_stats/metrics/workers.py` | Per-worker metrics (+ `mean_rt`) | Task 6, Task 7 |
| `datadog_checks/uwsgi_stats/metrics/apps.py` | Per-app metrics (conditional) | Task 8 |
| `datadog_checks/uwsgi_stats/metrics/caches.py` | Per-cache metrics (conditional) | Task 9 |
| `datadog_checks/uwsgi_stats/metrics/spoolers.py` | Per-spooler metrics (conditional) | Task 10 |
| `datadog_checks/uwsgi_stats/metrics/cores.py` | Per-core metrics (opt-in) | Task 11 |
| `datadog_checks/uwsgi_stats/health.py` | `evaluate_saturation(stats, instance)` | Task 12 |
| `datadog_checks/uwsgi_stats/data/conf.yaml.example` | Config template | Task 13 |
| `metadata.csv`, `manifest.json`, `README.md`, `CHANGELOG.md` | Packaging metadata / docs | Task 13 |
| `.github/workflows/ci.yml` | CI (lint, type, test) | Task 14 |
| `tests/conftest.py`, `tests/fixtures/stats.json`, `tests/test_*.py` | Test suite | Tasks 1–14 |

**Removed:** `src/monitor_uwsgi/` (whole package), `monitor_uwsgi.sh`, `requirements.txt`, and the `datadog`/`click`/`pip-tools` deps (Task 1).

---

## Task 1: Project scaffolding, packaging, dev environment

**Files:**
- Create: `pyproject.toml`, `datadog_checks/uwsgi_stats/__about__.py`, `datadog_checks/uwsgi_stats/__init__.py`, `datadog_checks/uwsgi_stats/check.py`, `datadog_checks/uwsgi_stats/metrics/__init__.py`, `datadog_checks/uwsgi_stats/data/.gitkeep`
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/fixtures/stats.json`, `tests/test_smoke.py`
- Delete: `src/monitor_uwsgi/` (recursively), `monitor_uwsgi.sh`, `requirements.txt`, old root `pyproject.toml` content (overwritten)

**Interfaces:**
- Produces: `datadog_checks.uwsgi_stats.UwsgiStatsCheck` (class, `__NAMESPACE__ == 'uwsgi'`), `datadog_checks.uwsgi_stats.__version__` (str). `tests/conftest.py` provides fixtures `stats` (parsed `stats.json` dict) and `instance` (dict). `tests/fixtures/stats.json` is the canonical stats snapshot used by all later metric tests.

- [ ] **Step 1: Remove the old implementation and write the new `pyproject.toml`**

```bash
git rm -r src/monitor_uwsgi monitor_uwsgi.sh requirements.txt uv.lock
```

Overwrite `pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "datadog-uwsgi-stats"
dynamic = ["version"]
description = "Datadog Agent check for the uWSGI stats server"
readme = "README.md"
requires-python = ">=3.8"
license = { text = "BSD-3-Clause" }
authors = [{ name = "Christophe Pettus", email = "cpettus@pgexperts.com" }]
dependencies = ["datadog-checks-base"]

[project.optional-dependencies]
dev = ["datadog-checks-dev", "pytest", "ruff", "mypy"]

[tool.setuptools.dynamic]
version = { attr = "datadog_checks.uwsgi_stats.__about__.__version__" }

[tool.setuptools.packages.find]
include = ["datadog_checks*"]
namespaces = true

[tool.setuptools.package-data]
"datadog_checks.uwsgi_stats" = ["data/*"]

[tool.ruff]
line-length = 120
target-version = "py38"

[tool.mypy]
python_version = "3.8"
ignore_missing_imports = true
```

- [ ] **Step 2: Create the package skeleton**

`datadog_checks/uwsgi_stats/__about__.py`:

```python
__version__ = "0.1.0"
```

`datadog_checks/uwsgi_stats/__init__.py`:

```python
from .__about__ import __version__
from .check import UwsgiStatsCheck

__all__ = ["UwsgiStatsCheck", "__version__"]
```

`datadog_checks/uwsgi_stats/check.py` (skeleton — fleshed out in Task 3):

```python
from datadog_checks.base import AgentCheck


class UwsgiStatsCheck(AgentCheck):
    __NAMESPACE__ = "uwsgi"

    def check(self, instance):
        pass
```

`datadog_checks/uwsgi_stats/metrics/__init__.py`:

```python
COLLECTORS = []
```

Create the data dir placeholder so it is tracked: `datadog_checks/uwsgi_stats/data/.gitkeep` (empty file).

> Note: there is **no** `datadog_checks/__init__.py` — `datadog_checks` is a PEP 420 namespace package shared with `datadog_checks_base`. Do not create one.

- [ ] **Step 3: Create the canonical test fixture**

`tests/fixtures/stats.json`:

```json
{
  "version": "2.0.24",
  "listen_queue": 3,
  "listen_queue_errors": 1,
  "signal_queue": 0,
  "load": 3,
  "pid": 1000,
  "sockets": [
    {"name": "127.0.0.1:8000", "proto": "uwsgi", "queue": 3, "max_queue": 100, "shared": 0, "can_offload": 0},
    {"name": "/tmp/app.sock", "proto": "http", "queue": 0, "max_queue": 100, "shared": 0, "can_offload": 0}
  ],
  "workers": [
    {
      "id": 1, "pid": 1001, "accepting": 1, "requests": 1500, "delta_requests": 500,
      "exceptions": 2, "harakiri_count": 0, "signals": 0, "signal_queue": 0,
      "status": "idle", "rss": 52428800, "vsz": 209715200, "running_time": 3000000,
      "last_spawn": 1783800000, "respawn_count": 1, "tx": 10485760, "avg_rt": 2000,
      "apps": [
        {"id": 0, "modifier1": 0, "mountpoint": "", "startup_time": 1, "requests": 1500, "exceptions": 2, "chdir": ""}
      ],
      "cores": [
        {"id": 0, "requests": 1500, "static_requests": 0, "routed_requests": 0, "offloaded_requests": 0, "write_errors": 0, "read_errors": 0, "in_request": 0}
      ]
    },
    {
      "id": 2, "pid": 1002, "accepting": 1, "requests": 1400, "delta_requests": 400,
      "exceptions": 0, "harakiri_count": 1, "signals": 0, "signal_queue": 0,
      "status": "busy", "rss": 53477376, "vsz": 209715200, "running_time": 2800000,
      "last_spawn": 1783800000, "respawn_count": 1, "tx": 9437184, "avg_rt": 2100,
      "apps": [
        {"id": 0, "modifier1": 0, "mountpoint": "/api", "startup_time": 1, "requests": 700, "exceptions": 0, "chdir": ""},
        {"id": 1, "modifier1": 0, "mountpoint": "/admin", "startup_time": 1, "requests": 700, "exceptions": 0, "chdir": ""}
      ],
      "cores": [
        {"id": 0, "requests": 1400, "static_requests": 0, "routed_requests": 0, "offloaded_requests": 0, "write_errors": 3, "read_errors": 1, "in_request": 1}
      ]
    },
    {
      "id": 3, "pid": 1003, "accepting": 1, "requests": 10, "delta_requests": 10,
      "exceptions": 0, "harakiri_count": 0, "signals": 0, "signal_queue": 0,
      "status": "busy", "rss": 20971520, "vsz": 104857600, "running_time": 15000,
      "last_spawn": 1783800300, "respawn_count": 2, "tx": 65536, "avg_rt": 1500,
      "apps": [
        {"id": 0, "modifier1": 0, "mountpoint": "", "startup_time": 1, "requests": 10, "exceptions": 0, "chdir": ""}
      ],
      "cores": [
        {"id": 0, "requests": 10, "static_requests": 0, "routed_requests": 0, "offloaded_requests": 0, "write_errors": 0, "read_errors": 0, "in_request": 1}
      ]
    }
  ],
  "caches": [
    {"name": "default", "hash": "djb33x", "hashsize": 65536, "keysize": 2048, "max_items": 1000, "blocks": 1000, "blocksize": 65536, "items": 250, "hits": 8000, "miss": 1200, "full": 0, "last_modified_at": 1783800250}
  ],
  "spoolers": [
    {"dir": "/var/spool/uwsgi", "pid": 1004, "tasks": 42, "respawns": 0, "running": 0}
  ]
}
```

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
import json
import os

import pytest

HERE = os.path.dirname(__file__)


@pytest.fixture
def stats():
    with open(os.path.join(HERE, "fixtures", "stats.json")) as fh:
        return json.load(fh)


@pytest.fixture
def instance():
    return {"stats_url": "tcp://127.0.0.1:1717"}
```

> The `aggregator` and `dd_run_check` fixtures come from the `datadog_checks.dev` pytest plugin (installed via the `dev` extra); no manual registration needed.

- [ ] **Step 4: Write the smoke test**

`tests/test_smoke.py`:

```python
def test_package_exports():
    from datadog_checks.uwsgi_stats import UwsgiStatsCheck, __version__

    assert __version__ == "0.1.0"
    assert UwsgiStatsCheck.__NAMESPACE__ == "uwsgi"


def test_fixture_is_valid(stats):
    assert stats["version"] == "2.0.24"
    assert len(stats["workers"]) == 3
    assert len(stats["sockets"]) == 2
```

- [ ] **Step 5: Create the dev environment and run the smoke test to verify it fails, then passes**

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest tests/test_smoke.py -v
```

Expected: PASS (both tests). If the editable install cannot import `datadog_checks.uwsgi_stats`, confirm there is no stray `datadog_checks/__init__.py` and that `namespaces = true` is set.

- [ ] **Step 6: Add a `.gitignore` entry for the venv and commit**

Ensure `.gitignore` contains `.venv/` (append if missing). Then:

```bash
git add pyproject.toml datadog_checks tests .gitignore
git commit -m "Scaffold uwsgi_stats integration package and test harness"
```

---

## Task 2: Stats transport & parsing (`stats.py`)

**Files:**
- Create: `datadog_checks/uwsgi_stats/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces: `read_stats(stats_url: str, timeout: float = 5) -> dict`. Dispatches on the URL scheme (`tcp://`, `unix://`, `http(s)://`), reads the entire response to EOF, and returns the parsed JSON object. Raises `datadog_checks.base.ConfigurationError` for a missing/unsupported scheme; raises `ValueError` for truncated/invalid JSON; propagates `OSError` for connect/read failures.

- [ ] **Step 1: Write the failing tests**

`tests/test_stats.py`:

```python
import json
import os
import socket
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


def test_read_unix_socket(tmp_path):
    sock_path = str(tmp_path / "stats.sock")
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_stats.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: datadog_checks.uwsgi_stats.stats`.

- [ ] **Step 3: Implement `stats.py`**

`datadog_checks/uwsgi_stats/stats.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_stats.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/stats.py tests/test_stats.py
git commit -m "Add uWSGI stats transport (tcp/unix/http) with read-to-EOF and parse"
```

---

## Task 3: Check orchestration + `can_connect` service check

**Files:**
- Modify: `datadog_checks/uwsgi_stats/check.py`
- Test: `tests/test_check.py`

**Interfaces:**
- Consumes: `read_stats` (Task 2), `COLLECTORS` (Task 1, currently `[]`).
- Produces: `UwsgiStatsCheck.check(instance)` that reads stats, submits `uwsgi.can_connect` (`OK`/`CRITICAL`), iterates `COLLECTORS`, and (from Task 12) `worker_saturation`. Collectors have the contract `collect(check, stats, base_tags)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_check.py`:

```python
import pytest

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats import check as check_module


def test_can_connect_ok_on_clean_read(aggregator, instance, stats, monkeypatch):
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)
    aggregator.assert_service_check("uwsgi.can_connect", status=UwsgiStatsCheck.OK)


def test_can_connect_critical_and_reraise_on_failure(aggregator, instance, monkeypatch):
    def boom(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(check_module, "read_stats", boom)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    with pytest.raises(OSError):
        check.check(instance)
    aggregator.assert_service_check("uwsgi.can_connect", status=UwsgiStatsCheck.CRITICAL)


def test_missing_stats_url_raises_configuration_error(aggregator):
    from datadog_checks.base import ConfigurationError

    check = UwsgiStatsCheck("uwsgi_stats", {}, [{}])
    with pytest.raises(ConfigurationError):
        check.check({})
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_check.py -v`
Expected: FAIL — `check()` currently does nothing, so no service check is emitted.

- [ ] **Step 3: Implement the check body**

Overwrite `datadog_checks/uwsgi_stats/check.py`:

```python
"""uWSGI stats Agent check.

Reads the uWSGI stats server once per collection cycle and dispatches the parsed
snapshot to a registry of section collectors. Cumulative counters are submitted
via monotonic_count (the Agent diffs samples and swallows the reset when a worker
respawns); instantaneous values via gauge.
"""

from datadog_checks.base import AgentCheck, ConfigurationError

from .metrics import COLLECTORS
from .stats import read_stats


class UwsgiStatsCheck(AgentCheck):
    __NAMESPACE__ = "uwsgi"

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
            raise

        self.service_check("can_connect", AgentCheck.OK, tags=base_tags)

        for collect in COLLECTORS:
            collect(self, stats, base_tags)
```

> Note: `service_check("can_connect", ...)` is submitted **without** the `uwsgi.` prefix — `AgentCheck` prepends `__NAMESPACE__` to service-check names just as it does for metrics, so the external name is `uwsgi.can_connect`. That is why the tests assert `"uwsgi.can_connect"`. If a `datadog-checks-base` version in use does not namespace service checks, the test will fail on the name — switch to passing the full `"uwsgi.can_connect"` with `raw=True`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_check.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/check.py tests/test_check.py
git commit -m "Add check orchestration and uwsgi.can_connect service check"
```

---

## Task 4: Aggregate collector (global rollups + worker status)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/aggregate.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_aggregate.py`

**Interfaces:**
- Produces: `aggregate.collect(check, stats, base_tags)`. Emits `uwsgi.listen_queue` (gauge), `uwsgi.listen_queue_errors` (monotonic_count), `uwsgi.signal_queue` (gauge), `uwsgi.workers.total` (gauge), `uwsgi.workers.by_status` (gauge, tag `status:<bucket>` for each of idle/busy/cheap/pause/sig). Registered first in `COLLECTORS`.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_aggregate.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import aggregate


def _run(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    aggregate.collect(check, stats, ["env:test"])
    return check


def test_global_gauges(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric("uwsgi.listen_queue", value=3, tags=["env:test"])
    aggregator.assert_metric("uwsgi.signal_queue", value=0, tags=["env:test"])
    aggregator.assert_metric("uwsgi.workers.total", value=3, tags=["env:test"])


def test_listen_queue_errors_is_monotonic_count(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.listen_queue_errors",
        value=1,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["env:test"],
    )


def test_workers_by_status_buckets(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric("uwsgi.workers.by_status", value=1, tags=["env:test", "status:idle"])
    aggregator.assert_metric("uwsgi.workers.by_status", value=2, tags=["env:test", "status:busy"])
    aggregator.assert_metric("uwsgi.workers.by_status", value=0, tags=["env:test", "status:cheap"])
    aggregator.assert_metric("uwsgi.workers.by_status", value=0, tags=["env:test", "status:pause"])
    aggregator.assert_metric("uwsgi.workers.by_status", value=0, tags=["env:test", "status:sig"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_aggregate.py -v`
Expected: FAIL — `ModuleNotFoundError: ...metrics.aggregate`.

- [ ] **Step 3: Implement `aggregate.py` and register it**

`datadog_checks/uwsgi_stats/metrics/aggregate.py`:

```python
"""Global / aggregate uWSGI metrics: listen queue, signal queue, worker counts.

top-level `load` is intentionally not emitted: the uWSGI source sets it to a copy
of the backlog with a TODO, so it duplicates `listen_queue`.
"""

from collections import Counter

_STATUS_BUCKETS = ("idle", "busy", "cheap", "pause", "sig")


def collect(check, stats, base_tags):
    if "listen_queue" in stats:
        check.gauge("listen_queue", stats["listen_queue"], tags=base_tags)
    if "listen_queue_errors" in stats:
        check.monotonic_count("listen_queue_errors", stats["listen_queue_errors"], tags=base_tags)
    if "signal_queue" in stats:
        check.gauge("signal_queue", stats["signal_queue"], tags=base_tags)

    workers = stats.get("workers", [])
    check.gauge("workers.total", len(workers), tags=base_tags)

    counts = Counter()
    for worker in workers:
        status = worker.get("status", "idle")
        counts["sig" if status.startswith("sig") else status] += 1
    for bucket in _STATUS_BUCKETS:
        check.gauge(
            "workers.by_status", counts.get(bucket, 0), tags=base_tags + ["status:%s" % bucket]
        )
```

Update `datadog_checks/uwsgi_stats/metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate

COLLECTORS = [
    collect_aggregate,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_aggregate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/aggregate.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_aggregate.py
git commit -m "Add aggregate collector (listen/signal queue, worker status counts)"
```

---

## Task 5: Sockets collector

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/sockets.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_sockets.py`

**Interfaces:**
- Produces: `sockets.collect(check, stats, base_tags)`. Emits `uwsgi.socket.queue` and `uwsgi.socket.max_queue` (both gauge), tagged `socket_name:<name>`, `proto:<proto>`.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_sockets.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_sockets.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `sockets.py` and register**

`datadog_checks/uwsgi_stats/metrics/sockets.py`:

```python
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
```

Update `metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate
from .sockets import collect as collect_sockets

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_sockets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/sockets.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_sockets.py
git commit -m "Add sockets collector (per-socket queue/max_queue)"
```

---

## Task 6: Workers collector (counters + gauges + uptime)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/workers.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_workers.py`

**Interfaces:**
- Produces: `workers.collect(check, stats, base_tags)`. For each worker (tag `worker_id:<id>`): monotonic_count for `worker.requests`, `worker.tx`, `worker.exceptions`, `worker.harakiri_count`, `worker.signals`, `worker.running_time`, `worker.respawn_count`; gauge for `worker.signal_queue`, `worker.avg_rt`, `worker.rss`, `worker.vsz`, `worker.accepting`, `worker.uptime` (`now - last_spawn`). `worker.mean_rt` is added in Task 7.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_workers.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import workers


def _run(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    return check


def test_worker_counters_are_monotonic(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.worker.requests", value=1500,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.tx", value=10485760,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.harakiri_count", value=1,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.respawn_count", value=2,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:3"],
    )


def test_worker_gauges(aggregator, stats):
    _run(aggregator, stats)
    aggregator.assert_metric(
        "uwsgi.worker.rss", value=52428800,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.avg_rt", value=2100,
        metric_type=aggregator.GAUGE, tags=["worker_id:2"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.accepting", value=1,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )


def test_worker_uptime_emitted_non_negative(aggregator, stats):
    _run(aggregator, stats)
    # last_spawn is in the past, so uptime is a positive gauge; assert presence.
    aggregator.assert_metric("uwsgi.worker.uptime", metric_type=aggregator.GAUGE, tags=["worker_id:1"])


def test_never_tags_pid(aggregator, stats):
    _run(aggregator, stats)
    for metric in aggregator.metric_names:
        for m in aggregator.metrics(metric):
            assert not any(t.startswith("pid:") for t in m.tags), (metric, m.tags)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_workers.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `workers.py` and register**

`datadog_checks/uwsgi_stats/metrics/workers.py`:

```python
"""Per-worker uWSGI metrics.

Counters (requests, tx, ...) are cumulative since the worker first started and
reset to 0 on respawn; monotonic_count handles the reset. Every metric is tagged
worker_id (bounded) -- never pid (churns on respawn). running_time and avg_rt are
microseconds; rss/vsz are bytes and stay 0 unless uWSGI runs with --memory-report.
"""

import time

_COUNTERS = (
    "requests",
    "tx",
    "exceptions",
    "harakiri_count",
    "signals",
    "running_time",
    "respawn_count",
)
_GAUGES = (
    "signal_queue",
    "avg_rt",
    "rss",
    "vsz",
    "accepting",
)


def collect(check, stats, base_tags):
    now = time.time()
    for worker in stats.get("workers", []):
        tags = base_tags + ["worker_id:%s" % worker.get("id")]
        for name in _COUNTERS:
            if name in worker:
                check.monotonic_count("worker.%s" % name, worker[name], tags=tags)
        for name in _GAUGES:
            if name in worker:
                check.gauge("worker.%s" % name, worker[name], tags=tags)
        if "last_spawn" in worker:
            check.gauge("worker.uptime", now - worker["last_spawn"], tags=tags)
```

Update `metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate
from .sockets import collect as collect_sockets
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_workers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/workers.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_workers.py
git commit -m "Add workers collector (per-worker counters, gauges, uptime)"
```

---

## Task 7: Computed `worker.mean_rt` (cross-scrape state)

**Files:**
- Modify: `datadog_checks/uwsgi_stats/check.py`, `datadog_checks/uwsgi_stats/metrics/workers.py`
- Test: `tests/test_metrics_mean_rt.py`

**Interfaces:**
- Consumes: `check._prev_worker` — a dict `{worker_id: (requests, running_time)}` held on the check instance across collection cycles.
- Produces: `uwsgi.worker.mean_rt` (gauge, µs) = `Δrunning_time / Δrequests`, emitted only when a previous sample exists and `Δrequests > 0` and `Δrunning_time >= 0` (a respawn drops the counters, so the negative delta is skipped, not emitted).

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_mean_rt.py`:

```python
import copy

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import workers


def test_mean_rt_skipped_first_scrape_then_computed(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])

    # First scrape: no previous sample -> no mean_rt.
    workers.collect(check, stats, [])
    assert not aggregator.metrics("uwsgi.worker.mean_rt")

    # Second scrape: worker 1 advanced by 100 requests / 200000 us -> mean 2000 us.
    aggregator.reset()
    later = copy.deepcopy(stats)
    later["workers"][0]["requests"] = 1600
    later["workers"][0]["running_time"] = 3200000
    workers.collect(check, later, [])
    aggregator.assert_metric(
        "uwsgi.worker.mean_rt", value=2000.0,
        metric_type=aggregator.GAUGE, tags=["worker_id:1"],
    )


def test_mean_rt_skipped_on_respawn(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    workers.collect(check, stats, [])
    aggregator.reset()
    # Worker 1 respawned: counters reset below the previous sample.
    respawned = copy.deepcopy(stats)
    respawned["workers"][0]["requests"] = 5
    respawned["workers"][0]["running_time"] = 1000
    workers.collect(check, respawned, [])
    mean_rt_w1 = [m for m in aggregator.metrics("uwsgi.worker.mean_rt") if "worker_id:1" in m.tags]
    assert mean_rt_w1 == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_mean_rt.py -v`
Expected: FAIL — `mean_rt` never emitted; also `AttributeError: _prev_worker` until implemented.

- [ ] **Step 3: Add per-instance state and compute `mean_rt`**

In `check.py`, add an `__init__` that initializes the state dict (place it above `check`):

```python
    def __init__(self, name, init_config, instances):
        super().__init__(name, init_config, instances)
        self._prev_worker = {}
```

In `workers.py`, add the computation at the end of the per-worker loop body (after the gauges/uptime), and add a module docstring note. The full updated file:

```python
"""Per-worker uWSGI metrics.

Counters (requests, tx, ...) are cumulative since the worker first started and
reset to 0 on respawn; monotonic_count handles the reset. Every metric is tagged
worker_id (bounded) -- never pid (churns on respawn). running_time and avg_rt are
microseconds; rss/vsz are bytes and stay 0 unless uWSGI runs with --memory-report.

mean_rt is the honest per-scrape latency (delta running_time / delta requests, us);
it needs the previous scrape's sample, kept on check._prev_worker keyed by worker id.
"""

import time

_COUNTERS = (
    "requests",
    "tx",
    "exceptions",
    "harakiri_count",
    "signals",
    "running_time",
    "respawn_count",
)
_GAUGES = (
    "signal_queue",
    "avg_rt",
    "rss",
    "vsz",
    "accepting",
)


def collect(check, stats, base_tags):
    now = time.time()
    for worker in stats.get("workers", []):
        wid = worker.get("id")
        tags = base_tags + ["worker_id:%s" % wid]
        for name in _COUNTERS:
            if name in worker:
                check.monotonic_count("worker.%s" % name, worker[name], tags=tags)
        for name in _GAUGES:
            if name in worker:
                check.gauge("worker.%s" % name, worker[name], tags=tags)
        if "last_spawn" in worker:
            check.gauge("worker.uptime", now - worker["last_spawn"], tags=tags)

        _emit_mean_rt(check, worker, wid, tags)


def _emit_mean_rt(check, worker, wid, tags):
    requests = worker.get("requests")
    running_time = worker.get("running_time")
    if requests is None or running_time is None:
        return
    prev = check._prev_worker.get(wid)
    check._prev_worker[wid] = (requests, running_time)
    if prev is None:
        return
    d_req = requests - prev[0]
    d_rt = running_time - prev[1]
    if d_req > 0 and d_rt >= 0:
        check.gauge("worker.mean_rt", d_rt / d_req, tags=tags)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_mean_rt.py tests/test_metrics_workers.py -v`
Expected: PASS (both files — the Task 6 tests still pass).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/check.py datadog_checks/uwsgi_stats/metrics/workers.py tests/test_metrics_mean_rt.py
git commit -m "Add computed worker.mean_rt with respawn-safe cross-scrape state"
```

---

## Task 8: Apps collector (conditional on multi-app workers)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/apps.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_apps.py`

**Interfaces:**
- Produces: `apps.collect(check, stats, base_tags)`. For each worker with `len(apps) > 1`, emits `uwsgi.worker.app.requests` and `uwsgi.worker.app.exceptions` (monotonic_count) tagged `worker_id`, `app_id`, `mountpoint`. Single-app workers are skipped (they duplicate worker-level counters).

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_apps.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import apps


def test_apps_emitted_only_for_multi_app_worker(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    apps.collect(check, stats, [])

    # Worker 2 has 2 apps -> emitted.
    aggregator.assert_metric(
        "uwsgi.worker.app.requests", value=700,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["worker_id:2", "app_id:0", "mountpoint:/api"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.app.requests", value=700,
        metric_type=aggregator.MONOTONIC_COUNT,
        tags=["worker_id:2", "app_id:1", "mountpoint:/admin"],
    )

    # Workers 1 and 3 have a single app -> not emitted.
    single_app = [
        m for m in aggregator.metrics("uwsgi.worker.app.requests")
        if "worker_id:1" in m.tags or "worker_id:3" in m.tags
    ]
    assert single_app == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_apps.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `apps.py` and register**

`datadog_checks/uwsgi_stats/metrics/apps.py`:

```python
"""Per-app uWSGI metrics, emitted only when a worker mounts more than one app.

Single-app workers duplicate the worker-level request/exception counters, so
gating on len(apps) > 1 avoids doubled series in the common single-app case.
"""


def collect(check, stats, base_tags):
    for worker in stats.get("workers", []):
        apps = worker.get("apps", [])
        if len(apps) <= 1:
            continue
        wid = worker.get("id")
        for app in apps:
            tags = base_tags + [
                "worker_id:%s" % wid,
                "app_id:%s" % app.get("id"),
                "mountpoint:%s" % app.get("mountpoint", ""),
            ]
            if "requests" in app:
                check.monotonic_count("worker.app.requests", app["requests"], tags=tags)
            if "exceptions" in app:
                check.monotonic_count("worker.app.exceptions", app["exceptions"], tags=tags)
```

Update `metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .sockets import collect as collect_sockets
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_apps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/apps.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_apps.py
git commit -m "Add apps collector (per-app metrics for multi-app workers)"
```

---

## Task 9: Caches collector (conditional)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/caches.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_caches.py`

**Interfaces:**
- Produces: `caches.collect(check, stats, base_tags)`. For each entry in `caches` (tag `cache:<name>`): `uwsgi.cache.items` (gauge); `uwsgi.cache.hits`, `uwsgi.cache.miss`, `uwsgi.cache.full` (monotonic_count). Absent `caches` section → nothing emitted.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_caches.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import caches


def test_cache_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    caches.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.cache.items", value=250, metric_type=aggregator.GAUGE, tags=["cache:default"]
    )
    aggregator.assert_metric(
        "uwsgi.cache.hits", value=8000,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["cache:default"],
    )
    aggregator.assert_metric(
        "uwsgi.cache.miss", value=1200,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["cache:default"],
    )


def test_no_caches_section_emits_nothing(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    stats_no_cache = dict(stats)
    stats_no_cache.pop("caches", None)
    caches.collect(check, stats_no_cache, [])
    assert not aggregator.metrics("uwsgi.cache.items")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_caches.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `caches.py` and register**

`datadog_checks/uwsgi_stats/metrics/caches.py`:

```python
"""Per-cache uWSGI metrics; present only when caches are configured."""

_COUNTERS = ("hits", "miss", "full")


def collect(check, stats, base_tags):
    for cache in stats.get("caches", []):
        tags = base_tags + ["cache:%s" % cache.get("name", "")]
        if "items" in cache:
            check.gauge("cache.items", cache["items"], tags=tags)
        for name in _COUNTERS:
            if name in cache:
                check.monotonic_count("cache.%s" % name, cache[name], tags=tags)
```

Update `metrics/__init__.py` (add import + registry entry after `collect_apps`):

```python
from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .caches import collect as collect_caches
from .sockets import collect as collect_sockets
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
    collect_caches,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_caches.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/caches.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_caches.py
git commit -m "Add caches collector (per-cache items/hits/miss/full)"
```

---

## Task 10: Spoolers collector (conditional)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/spoolers.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_spoolers.py`

**Interfaces:**
- Produces: `spoolers.collect(check, stats, base_tags)`. For each entry in `spoolers` (tag `spooler:<dir>`): `uwsgi.spooler.tasks`, `uwsgi.spooler.respawns` (monotonic_count); `uwsgi.spooler.running` (gauge). Absent section → nothing emitted.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_spoolers.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import spoolers


def test_spooler_metrics(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    spoolers.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.spooler.tasks", value=42,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["spooler:/var/spool/uwsgi"],
    )
    aggregator.assert_metric(
        "uwsgi.spooler.running", value=0,
        metric_type=aggregator.GAUGE, tags=["spooler:/var/spool/uwsgi"],
    )


def test_no_spoolers_section_emits_nothing(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    stats_no_spool = dict(stats)
    stats_no_spool.pop("spoolers", None)
    spoolers.collect(check, stats_no_spool, [])
    assert not aggregator.metrics("uwsgi.spooler.tasks")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_spoolers.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `spoolers.py` and register**

`datadog_checks/uwsgi_stats/metrics/spoolers.py`:

```python
"""Per-spooler uWSGI metrics; present only when spoolers are configured."""

_COUNTERS = ("tasks", "respawns")


def collect(check, stats, base_tags):
    for spooler in stats.get("spoolers", []):
        tags = base_tags + ["spooler:%s" % spooler.get("dir", "")]
        for name in _COUNTERS:
            if name in spooler:
                check.monotonic_count("spooler.%s" % name, spooler[name], tags=tags)
        if "running" in spooler:
            check.gauge("spooler.running", spooler["running"], tags=tags)
```

Update `metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .caches import collect as collect_caches
from .sockets import collect as collect_sockets
from .spoolers import collect as collect_spoolers
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
    collect_caches,
    collect_spoolers,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_spoolers.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/spoolers.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_spoolers.py
git commit -m "Add spoolers collector (per-spooler tasks/respawns/running)"
```

---

## Task 11: Cores collector (opt-in via `collect_per_core`)

**Files:**
- Create: `datadog_checks/uwsgi_stats/metrics/cores.py`
- Modify: `datadog_checks/uwsgi_stats/metrics/__init__.py`
- Test: `tests/test_metrics_cores.py`

**Interfaces:**
- Consumes: `check.instance` (the current instance dict; `collect_per_core` bool, default `False`).
- Produces: `cores.collect(check, stats, base_tags)`. When `collect_per_core` is true, for each worker core (tags `worker_id`, `core_id`): monotonic_count for `worker.core.requests`, `worker.core.static_requests`, `worker.core.routed_requests`, `worker.core.offloaded_requests`, `worker.core.write_errors`, `worker.core.read_errors`; gauge for `worker.core.in_request`. When false (default), emits nothing.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics_cores.py`:

```python
from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats.metrics import cores


def test_cores_off_by_default(aggregator, stats):
    check = UwsgiStatsCheck("uwsgi_stats", {}, [{"stats_url": "tcp://x:1"}])
    cores.collect(check, stats, [])
    assert not aggregator.metrics("uwsgi.worker.core.requests")


def test_cores_emitted_when_enabled(aggregator, stats):
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    cores.collect(check, stats, [])
    aggregator.assert_metric(
        "uwsgi.worker.core.requests", value=1400,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.write_errors", value=3,
        metric_type=aggregator.MONOTONIC_COUNT, tags=["worker_id:2", "core_id:0"],
    )
    aggregator.assert_metric(
        "uwsgi.worker.core.in_request", value=1,
        metric_type=aggregator.GAUGE, tags=["worker_id:2", "core_id:0"],
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics_cores.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `cores.py` and register**

`datadog_checks/uwsgi_stats/metrics/cores.py`:

```python
"""Per-core uWSGI metrics; opt-in via the instance flag `collect_per_core`.

High cardinality and ~duplicates per-worker request counts on the common
single-core (sync) setup, so it is off by default. Absent under --stats-no-cores.
"""

_COUNTERS = (
    "requests",
    "static_requests",
    "routed_requests",
    "offloaded_requests",
    "write_errors",
    "read_errors",
)


def collect(check, stats, base_tags):
    if not check.instance.get("collect_per_core", False):
        return
    for worker in stats.get("workers", []):
        wid = worker.get("id")
        for core in worker.get("cores", []):
            tags = base_tags + ["worker_id:%s" % wid, "core_id:%s" % core.get("id")]
            for name in _COUNTERS:
                if name in core:
                    check.monotonic_count("worker.core.%s" % name, core[name], tags=tags)
            if "in_request" in core:
                check.gauge("worker.core.in_request", core["in_request"], tags=tags)
```

Update `metrics/__init__.py`:

```python
from .aggregate import collect as collect_aggregate
from .apps import collect as collect_apps
from .caches import collect as collect_caches
from .cores import collect as collect_cores
from .sockets import collect as collect_sockets
from .spoolers import collect as collect_spoolers
from .workers import collect as collect_workers

COLLECTORS = [
    collect_aggregate,
    collect_sockets,
    collect_workers,
    collect_apps,
    collect_caches,
    collect_spoolers,
    collect_cores,
]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_metrics_cores.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/metrics/cores.py datadog_checks/uwsgi_stats/metrics/__init__.py tests/test_metrics_cores.py
git commit -m "Add opt-in cores collector (per-core requests/errors/in_request)"
```

---

## Task 12: `worker_saturation` derived health service check

**Files:**
- Create: `datadog_checks/uwsgi_stats/health.py`
- Modify: `datadog_checks/uwsgi_stats/check.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Produces: `evaluate_saturation(stats, instance) -> (status, message)` where `status` is one of `AgentCheck.OK/WARNING/CRITICAL`. Wired into `check()` after the collectors as `self.service_check("worker_saturation", status, tags=base_tags, message=message)`.
- Thresholds from instance: `worker_saturation_warning` (default 0.5), `worker_saturation_critical` (default 0.9). Logic: `sat = max(queue/max_queue over sockets with max_queue>0)`. CRITICAL if `sat >= crit`; WARNING if `sat >= warn` or (all non-cheap workers busy and `listen_queue > 0`); else OK. If no usable `max_queue`: WARNING only on the all-busy-with-queue rule, else OK.

- [ ] **Step 1: Write the failing tests**

`tests/test_health.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: ...health`.

- [ ] **Step 3: Implement `health.py` and wire it into `check()`**

`datadog_checks/uwsgi_stats/health.py`:

```python
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
```

In `check.py`, add the import at the top:

```python
from .health import evaluate_saturation
```

and append to the end of `check()` (after the `for collect in COLLECTORS:` loop):

```python
        status, message = evaluate_saturation(stats, instance)
        self.service_check("worker_saturation", status, tags=base_tags, message=message)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_health.py tests/test_check.py -v`
Expected: PASS (all — `test_check.py` still green).

- [ ] **Step 5: Commit**

```bash
git add datadog_checks/uwsgi_stats/health.py datadog_checks/uwsgi_stats/check.py tests/test_health.py
git commit -m "Add worker_saturation derived health service check"
```

---

## Task 13: Packaging metadata, config template, README + coverage guard

**Files:**
- Create: `datadog_checks/uwsgi_stats/data/conf.yaml.example`, `manifest.json`, `metadata.csv`, `README.md`, `CHANGELOG.md`
- Delete: `datadog_checks/uwsgi_stats/data/.gitkeep`
- Test: `tests/test_metadata.py`

**Interfaces:**
- Produces: the shippable packaging artifacts. `metadata.csv` documents every metric; `test_metadata.py` guards that the metrics the check actually emits for the fixture are all documented (and that every documented metric name is real).

- [ ] **Step 1: Write the failing test**

`tests/test_metadata.py`:

```python
import csv
import os

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats import check as check_module

ROOT = os.path.dirname(os.path.dirname(__file__))


def _documented_metrics():
    path = os.path.join(ROOT, "metadata.csv")
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "metadata.csv is empty"
    names = set()
    for row in rows:
        assert row["metric_name"].startswith("uwsgi."), row
        assert row["metric_type"] in {"gauge", "count", "monotonic_count", "rate"}, row
        names.add(row["metric_name"])
    return names


def test_every_emitted_metric_is_documented(aggregator, stats, monkeypatch):
    # Enable per-core so every metric family is exercised.
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)

    documented = _documented_metrics()
    emitted = set(aggregator.metric_names)
    missing = emitted - documented
    assert not missing, "metrics emitted but not in metadata.csv: %s" % sorted(missing)


def test_no_phantom_metrics_documented(aggregator, stats, monkeypatch):
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)

    documented = _documented_metrics()
    emitted = set(aggregator.metric_names)
    # mean_rt needs two scrapes; it legitimately won't appear in a single run.
    phantom = documented - emitted - {"uwsgi.worker.mean_rt"}
    assert not phantom, "documented metrics never emitted: %s" % sorted(phantom)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metadata.py -v`
Expected: FAIL — `metadata.csv` does not exist.

- [ ] **Step 3: Write `metadata.csv`**

`metadata.csv` (header + one row per metric; columns are the Datadog standard set):

```csv
metric_name,metric_type,interval,unit_name,per_unit_name,description,orientation,integration,short_name,curated_metric,sample_tags
uwsgi.listen_queue,gauge,,connection,,Current listen-queue backlog of the primary socket (Linux only),-1,uwsgi_stats,listen queue,,
uwsgi.listen_queue_errors,monotonic_count,,error,,Cumulative listen-queue overflow errors (Linux only),-1,uwsgi_stats,listen queue errors,,
uwsgi.signal_queue,gauge,,item,,Items pending in the master signal pipe,-1,uwsgi_stats,signal queue,,
uwsgi.workers.total,gauge,,worker,,Total number of workers,0,uwsgi_stats,workers total,,
uwsgi.workers.by_status,gauge,,worker,,Worker count per status bucket,0,uwsgi_stats,workers by status,,status
uwsgi.socket.queue,gauge,,connection,,Current per-socket listen-queue depth,-1,uwsgi_stats,socket queue,,socket_name proto
uwsgi.socket.max_queue,gauge,,connection,,Configured max listen backlog for the socket,0,uwsgi_stats,socket max queue,,socket_name proto
uwsgi.worker.requests,monotonic_count,,request,,Requests handled by the worker since start,0,uwsgi_stats,worker requests,,worker_id
uwsgi.worker.tx,monotonic_count,,byte,,Bytes transmitted by the worker since start,0,uwsgi_stats,worker tx,,worker_id
uwsgi.worker.exceptions,monotonic_count,,error,,Exceptions raised in the worker since start,-1,uwsgi_stats,worker exceptions,,worker_id
uwsgi.worker.harakiri_count,monotonic_count,,event,,Times the worker was killed by harakiri (request timeout),-1,uwsgi_stats,worker harakiri,,worker_id
uwsgi.worker.signals,monotonic_count,,event,,uWSGI signals handled by the worker since start,0,uwsgi_stats,worker signals,,worker_id
uwsgi.worker.running_time,monotonic_count,,microsecond,,Cumulative time the worker spent in requests,0,uwsgi_stats,worker running time,,worker_id
uwsgi.worker.respawn_count,monotonic_count,,event,,Times the worker slot has spawned (starts at 1),0,uwsgi_stats,worker respawns,,worker_id
uwsgi.worker.signal_queue,gauge,,item,,Items pending in the worker signal pipe,-1,uwsgi_stats,worker signal queue,,worker_id
uwsgi.worker.avg_rt,gauge,,microsecond,,uWSGI smoothed average response time (not a true mean),-1,uwsgi_stats,worker avg rt,,worker_id
uwsgi.worker.mean_rt,gauge,,microsecond,,Mean response time from running_time/requests deltas,-1,uwsgi_stats,worker mean rt,,worker_id
uwsgi.worker.rss,gauge,,byte,,Worker resident set size (0 unless --memory-report),-1,uwsgi_stats,worker rss,,worker_id
uwsgi.worker.vsz,gauge,,byte,,Worker virtual size (0 unless --memory-report),-1,uwsgi_stats,worker vsz,,worker_id
uwsgi.worker.accepting,gauge,,,,1 if the worker is in the accept loop,0,uwsgi_stats,worker accepting,,worker_id
uwsgi.worker.uptime,gauge,,second,,Worker uptime (now - last_spawn),0,uwsgi_stats,worker uptime,,worker_id
uwsgi.worker.app.requests,monotonic_count,,request,,Requests served by a specific mounted app,0,uwsgi_stats,app requests,,worker_id app_id mountpoint
uwsgi.worker.app.exceptions,monotonic_count,,error,,Exceptions raised in a specific mounted app,-1,uwsgi_stats,app exceptions,,worker_id app_id mountpoint
uwsgi.cache.items,gauge,,item,,Items currently stored in the cache,0,uwsgi_stats,cache items,,cache
uwsgi.cache.hits,monotonic_count,,hit,,Cumulative cache hits,1,uwsgi_stats,cache hits,,cache
uwsgi.cache.miss,monotonic_count,,miss,,Cumulative cache misses,-1,uwsgi_stats,cache miss,,cache
uwsgi.cache.full,monotonic_count,,event,,Cumulative writes rejected because the cache was full,-1,uwsgi_stats,cache full,,cache
uwsgi.spooler.tasks,monotonic_count,,task,,Cumulative spooler tasks processed,0,uwsgi_stats,spooler tasks,,spooler
uwsgi.spooler.respawns,monotonic_count,,event,,Cumulative spooler respawns,-1,uwsgi_stats,spooler respawns,,spooler
uwsgi.spooler.running,gauge,,,,1 if the spooler is processing a task now,0,uwsgi_stats,spooler running,,spooler
uwsgi.worker.core.requests,monotonic_count,,request,,Requests handled on a core since worker start,0,uwsgi_stats,core requests,,worker_id core_id
uwsgi.worker.core.static_requests,monotonic_count,,request,,Static-file requests handled on a core,0,uwsgi_stats,core static requests,,worker_id core_id
uwsgi.worker.core.routed_requests,monotonic_count,,request,,Internally routed requests handled on a core,0,uwsgi_stats,core routed requests,,worker_id core_id
uwsgi.worker.core.offloaded_requests,monotonic_count,,request,,Requests offloaded to offload threads on a core,0,uwsgi_stats,core offloaded requests,,worker_id core_id
uwsgi.worker.core.write_errors,monotonic_count,,error,,Response write errors on a core,-1,uwsgi_stats,core write errors,,worker_id core_id
uwsgi.worker.core.read_errors,monotonic_count,,error,,Request read errors on a core,-1,uwsgi_stats,core read errors,,worker_id core_id
uwsgi.worker.core.in_request,gauge,,,,1 if the core is servicing a request now,0,uwsgi_stats,core in request,,worker_id core_id
```

- [ ] **Step 4: Write the config template, manifest, README, changelog**

`datadog_checks/uwsgi_stats/data/conf.yaml.example`:

```yaml
init_config:

instances:
    ## @param stats_url - string - required
    ## uWSGI stats server address. Scheme selects transport:
    ##   tcp://HOST:PORT       - uWSGI --stats HOST:PORT
    ##   unix:///path/to.sock  - uWSGI --stats /path/to.sock
    ##   http://HOST:PORT      - uWSGI --stats-http HOST:PORT
  - stats_url: tcp://127.0.0.1:1717

    ## @param tags - list of strings - optional
    ## Tags applied to every metric and service check from this instance.
    # tags:
    #   - service:myapp
    #   - env:prod

    ## @param collect_per_core - boolean - optional - default: false
    ## Emit per-core metrics (high cardinality; ~duplicates per-worker on sync setups).
    # collect_per_core: false

    ## @param worker_saturation_warning - float - optional - default: 0.5
    ## @param worker_saturation_critical - float - optional - default: 0.9
    ## Socket listen-queue fill ratio (queue/max_queue) thresholds for uwsgi.worker_saturation.
    # worker_saturation_warning: 0.5
    # worker_saturation_critical: 0.9

    ## @param timeout - number - optional - default: 5
    ## Seconds to wait connecting to / reading from the stats server.
    # timeout: 5

    ## @param min_collection_interval - number - optional - default: 15
    # min_collection_interval: 15
```

`manifest.json`:

```json
{
  "manifest_version": "2.0.0",
  "app_uuid": "d0b2f7a4-3c2e-4c9a-9f2e-1a2b3c4d5e6f",
  "app_id": "uwsgi-stats",
  "display_on_public_website": false,
  "tile": {
    "overview": "README.md#Overview",
    "configuration": "README.md#Setup",
    "changelog": "CHANGELOG.md",
    "description": "Collect the full uWSGI stats server metric set as a Datadog Agent check.",
    "title": "uWSGI Stats",
    "media": [],
    "classifier_tags": ["Category::Metrics", "Supported OS::Linux", "Offering::Integration"]
  },
  "author": {
    "name": "Christophe Pettus",
    "homepage": "https://github.com/",
    "support_email": "cpettus@pgexperts.com"
  },
  "assets": {
    "integration": {
      "source_type_name": "uwsgi_stats",
      "configuration": {"spec": "assets/configuration/spec.yaml"},
      "events": {"creates_events": false},
      "metrics": {
        "prefix": "uwsgi.",
        "check": "uwsgi.workers.total",
        "metadata_path": "metadata.csv"
      },
      "service_checks": {"metadata_path": "assets/service_checks.json"}
    }
  }
}
```

> `app_uuid` above is a placeholder; regenerate a real UUID (`python -c "import uuid; print(uuid.uuid4())"`) before any public submission. The `assets/configuration/spec.yaml` and `assets/service_checks.json` files are only required for an actual `integrations-extras` submission — deferred (spec §12); the check installs and runs without them.

`README.md`:

```markdown
# uWSGI Stats

## Overview

A Datadog Agent check that reads the uWSGI stats server and ships the full metric
set: global rollups, per-worker, and per-socket metrics, with auto-detected
per-app / cache / spooler sections and an opt-in per-core mode. Cumulative
counters are submitted as `monotonic_count`, so worker respawns do not corrupt
rate graphs.

## Setup

1. Enable the uWSGI stats server: `--stats 127.0.0.1:1717` (TCP), `--stats /path.sock`
   (UNIX), or `--stats-http 127.0.0.1:1717` (HTTP).
2. Build and install the wheel:
   `python -m build && datadog-agent integration install -w dist/datadog_uwsgi_stats-*.whl`
3. Copy `datadog_checks/uwsgi_stats/data/conf.yaml.example` to
   `conf.d/uwsgi_stats.d/conf.yaml` and set `stats_url`.
4. `datadog-agent reload`, then verify: `datadog-agent check uwsgi_stats`.

## Data Collected

### Metrics

See `metadata.csv` for the full list. All metrics are namespaced `uwsgi.`.
Times are microseconds; sizes are bytes.

### Service Checks

- `uwsgi.can_connect` - CRITICAL if the stats server is unreachable or returns
  invalid JSON, OK otherwise.
- `uwsgi.worker_saturation` - WARNING/CRITICAL as the socket listen queue fills
  (`queue / max_queue`) or all workers are busy with a growing listen queue.
```

`CHANGELOG.md`:

```markdown
# Changelog

## 0.1.0 / Unreleased

- Initial release: uWSGI stats server Agent check (`uwsgi_stats`).
```

Delete the placeholder: `git rm datadog_checks/uwsgi_stats/data/.gitkeep`.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_metadata.py -v`
Expected: PASS (2 tests). If `test_every_emitted_metric_is_documented` fails, reconcile the emitted metric name with `metadata.csv` (they must match exactly).

- [ ] **Step 6: Commit**

```bash
git add metadata.csv manifest.json README.md CHANGELOG.md datadog_checks/uwsgi_stats/data tests/test_metadata.py
git rm --cached datadog_checks/uwsgi_stats/data/.gitkeep 2>/dev/null || true
git commit -m "Add packaging metadata, conf.yaml.example, README, and metadata coverage test"
```

---

## Task 14: CI, lint, type-check, and full-suite green

**Files:**
- Create: `.github/workflows/ci.yml`
- Test: the entire `tests/` suite plus `ruff` and `mypy`

**Interfaces:**
- Produces: a GitHub Actions workflow running `ruff check`, `mypy`, and `pytest` on push and PR (correctness gates only; no benchmarks).

- [ ] **Step 1: Run the full suite, ruff, and mypy locally; fix any findings**

```bash
ruff check datadog_checks tests
mypy datadog_checks/uwsgi_stats
pytest -v
```

Expected: `ruff` clean, `mypy` clean (with `ignore_missing_imports` for `datadog_checks.base`), all tests PASS. Fix any lint/type issues before proceeding (e.g. unused imports, line length).

- [ ] **Step 2: Write the CI workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.8", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Lint
        run: ruff check datadog_checks tests
      - name: Type check
        run: mypy datadog_checks/uwsgi_stats
      - name: Test
        run: pytest -v
```

- [ ] **Step 3: Verify the workflow file is valid YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Add CI workflow (ruff, mypy, pytest)"
```

- [ ] **Step 5: Push the branch and open a PR**

```bash
git push -u origin datadog-agent-check
gh pr create --base main --title "uWSGI Datadog Agent check (uwsgi_stats)" --body "Implements the uWSGI stats Agent check per docs/superpowers/specs/2026-07-11-uwsgi-datadog-plugin-design.md."
```

Expected: PR opened; CI runs green. Per project convention, any post-PR changes go in a **new** PR against `main` (do not force-push or amend the merged branch).

---

## Post-implementation (per project standards)

- [ ] Update the ADR graph (codebase-memory `manage_adr`) and project docs (codebase-doc) for this change **before** requesting merge.
- [ ] Run adversarial review on the new code (superpowers:requesting-code-review or /code-review) and confirm the suite passes.
```

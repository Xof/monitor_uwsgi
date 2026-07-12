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

## Development

The check runs on the Agent's embedded Python (>=3.8), but the test toolchain
(`datadog-checks-dev`) requires Python >=3.10 — use 3.11/3.12 for development.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

pytest                            # test suite
ruff check datadog_checks tests   # lint
mypy datadog_checks/uwsgi_stats   # type check
python -m build                   # build the wheel into dist/
```

CI (GitHub Actions) runs ruff, mypy, and pytest on Python 3.11 and 3.12, plus a
3.8 byte-compile job that guards the Agent-runtime floor.

## Design

- Architecture map, invariants, and landmines: [ARCHITECTURE.md](ARCHITECTURE.md)
- Decision records: [docs/adr/](docs/adr/README.md)
- Full design spec: [docs/superpowers/specs/2026-07-11-uwsgi-datadog-plugin-design.md](docs/superpowers/specs/2026-07-11-uwsgi-datadog-plugin-design.md)

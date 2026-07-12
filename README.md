# uWSGI Stats

## Overview

A Datadog Agent check that reads the uWSGI stats server and ships the full metric
set: global rollups, per-worker, and per-socket metrics, with auto-detected
per-app / cache / spooler sections and an opt-in per-core mode. Cumulative
counters are submitted as `monotonic_count`, so worker respawns do not corrupt
rate graphs.

## Setup

Install the packaged integration into the Datadog Agent's embedded Python.

1. **Enable the uWSGI stats server** — `--stats 127.0.0.1:1717` (TCP),
   `--stats /path.sock` (UNIX), or `--stats-http 127.0.0.1:1717` (HTTP).

2. **Build the wheel** (see [Development](#development) for the dev environment):

   ```bash
   python -m build   # -> dist/datadog_uwsgi_stats-<version>-py3-none-any.whl
   ```

3. **Install the wheel.** `datadog-agent integration install` runs as the
   `dd-agent` user, which usually cannot read files under your home directory —
   so stage the wheel somewhere world-readable (e.g. `/tmp`) and pass an
   **absolute** path, not a `dist/…` path that resolves back into `$HOME`:

   ```bash
   cp dist/datadog_uwsgi_stats-*.whl /tmp/
   chmod 644 /tmp/datadog_uwsgi_stats-*.whl
   sudo -u dd-agent datadog-agent integration install -w /tmp/datadog_uwsgi_stats-*.whl
   ```

   This installs the check into the Agent's embedded Python and creates
   `/etc/datadog-agent/conf.d/uwsgi_stats.d/`, into which it copies the packaged
   `conf.yaml.example`. It does **not** activate the check and does **not**
   `chown` anything — the config dir is owned by whoever ran the command
   (`dd-agent` above, which is what you want).

4. **Configure.** Activate the copied template, set `stats_url`, and make sure
   the Agent user can read it:

   ```bash
   cd /etc/datadog-agent/conf.d/uwsgi_stats.d
   sudo -u dd-agent cp conf.yaml.example conf.yaml   # then edit conf.yaml: set stats_url
   sudo chown -R dd-agent:dd-agent /etc/datadog-agent/conf.d/uwsgi_stats.d
   sudo chmod 640 conf.yaml
   ```

5. **Verify, then restart:**

   ```bash
   sudo -u dd-agent datadog-agent check uwsgi_stats   # runs the check once, as the Agent user
   sudo systemctl restart datadog-agent               # or: datadog-agent reload
   ```

> `install -w` skips the version/compatibility checks the registry (`-t`) path
> performs and cannot verify a local wheel — only install wheels you built or trust.

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

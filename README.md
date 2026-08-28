# uWSGI Stats

## Overview

A Datadog Agent check that reads the uWSGI stats server and ships the full metric
set: global rollups, per-worker, and per-socket metrics, with auto-detected
per-app / cache / spooler sections and an opt-in per-core mode. Cumulative
counters are submitted as `monotonic_count`, so worker respawns do not corrupt
rate graphs.

## Setup

Install the packaged integration into the Datadog Agent's embedded Python. First
**enable the uWSGI stats server** — `--stats 127.0.0.1:1717` (TCP),
`--stats /path.sock` (UNIX), or `--stats-http 127.0.0.1:1717` (HTTP); whichever
address you pick becomes the check's `stats_url`.

### Quick install (recommended)

`scripts/build-and-install.sh` runs the whole build → install → configure flow
(the [manual steps](#manual-install) below) as a single command:

```bash
./scripts/build-and-install.sh
```

Run it from an operator account that may `sudo -u dd-agent`. It needs **no
general root access**: the wheel is built as you, then installed and configured
as the Agent user. It stages the wheel through `/tmp` so `dd-agent` can read it
(see step 2 for why), creates `conf.d/uwsgi_stats.d/conf.yaml` from the packaged
example — an existing `conf.yaml` is **never overwritten**, so re-running the
script to upgrade the check is safe — and prints the configure/verify/restart
commands, including the single step that needs root (the Agent restart), at the
end rather than performing them.

For a non-standard host, override the defaults via environment variables:

| Variable        | Default                     | Purpose                            |
|-----------------|-----------------------------|------------------------------------|
| `PYTHON`        | `python3`                   | Python used to build the wheel     |
| `DD_AGENT_USER` | `dd-agent`                  | The Datadog Agent user             |
| `DD_CONFD`      | `/etc/datadog-agent/conf.d` | Agent `conf.d` directory           |
| `DATADOG_AGENT` | first on `PATH`             | Path to the `datadog-agent` binary |

```bash
# e.g. an Agent that runs as 'datadog' with a non-PATH binary
DD_AGENT_USER=datadog DATADOG_AGENT=/opt/datadog-agent/bin/agent/agent \
  ./scripts/build-and-install.sh
```

`./scripts/build-and-install.sh --help` prints a summary of all of the above.

### Manual install

The steps the script automates, if you would rather run them yourself or its
assumptions don't fit your host:

1. **Build the wheel** (see [Development](#development) for the dev environment):

   ```bash
   python -m build   # -> dist/uwsgi_stats-<version>-py3-none-any.whl
   ```

2. **Install the wheel.** `datadog-agent integration install` runs as the
   `dd-agent` user, which usually cannot read files under your home directory —
   so stage the wheel somewhere world-readable (e.g. `/tmp`) and pass an
   **absolute** path, not a `dist/…` path that resolves back into `$HOME`:

   ```bash
   cp dist/uwsgi_stats-*.whl /tmp/
   chmod 644 /tmp/uwsgi_stats-*.whl
   sudo -u dd-agent datadog-agent integration install -w /tmp/uwsgi_stats-*.whl
   ```

   This installs the check into the Agent's embedded Python and creates
   `/etc/datadog-agent/conf.d/uwsgi_stats.d/`, into which it copies the packaged
   `conf.yaml.example`. It does **not** activate the check and does **not**
   `chown` anything — the config dir is owned by whoever ran the command
   (`dd-agent` above, which is what you want).

3. **Configure.** Activate the copied template, set `stats_url`, and make sure
   the Agent user can read it:

   ```bash
   cd /etc/datadog-agent/conf.d/uwsgi_stats.d
   sudo -u dd-agent cp conf.yaml.example conf.yaml   # then edit conf.yaml: set stats_url
   sudo chown -R dd-agent:dd-agent /etc/datadog-agent/conf.d/uwsgi_stats.d
   sudo chmod 640 conf.yaml
   ```

4. **Verify, then restart:**

   ```bash
   sudo -u dd-agent datadog-agent check uwsgi_stats   # runs the check once, as the Agent user
   sudo systemctl restart datadog-agent               # or: datadog-agent reload
   ```

> `install -w` skips the version/compatibility checks the registry (`-t`) path
> performs and cannot verify a local wheel — only install wheels you built or trust.

### Upgrading from `datadog-uwsgi-stats` 1.0.0

Version 1.1.0 renamed the PyPI distribution from `datadog-uwsgi-stats` to
**`uwsgi-stats`** ([ADR 0004](docs/adr/0004-drop-datadog-prefix-from-distribution-name.md)).
Nothing you configure changed — the check is still `uwsgi_stats`, the config is
still `conf.d/uwsgi_stats.d/conf.yaml`, and every metric name is the same. Only
the name the wheel is published under changed.

**Remove the old distribution before installing the new one.** Both own the same
`datadog_checks/uwsgi_stats/` files, so uninstalling `datadog-uwsgi-stats`
*after* installing `uwsgi-stats` deletes the files you just installed:

```bash
sudo -u dd-agent datadog-agent integration remove datadog-uwsgi-stats  # if present
```

`scripts/build-and-install.sh` does this for you, in the right order. Removing
the distribution does not touch `conf.d`, so your `conf.yaml` survives.

Because the new name has no `datadog-` prefix, two Agent subcommands no longer
apply to it — they reject any package name that isn't `datadog-*`, and
`integration freeze` filters its output the same way. Use the Agent's embedded
pip instead:

| Instead of | Use |
|---|---|
| `datadog-agent integration show uwsgi-stats` | `sudo -u dd-agent /opt/datadog-agent/embedded/bin/pip show uwsgi-stats` |
| `datadog-agent integration remove uwsgi-stats` | `sudo -u dd-agent /opt/datadog-agent/embedded/bin/pip uninstall uwsgi-stats` |
| `datadog-agent integration freeze` (won't list it) | `sudo -u dd-agent /opt/datadog-agent/embedded/bin/pip list` |

Installing with `integration install -w` is unaffected: it validates a local
wheel by its `datadog-checks-base` dependency, not by its name.

### Agent upgrades

Upgrading the Datadog Agent replaces its embedded Python, which wipes every
third-party integration. The Agent handles this itself: before the upgrade it
records which distributions you added, and afterwards it reinstalls them. That
restore routes each package by name — anything starting with `datadog-` is
fetched from Datadog's signed integration repository, anything else from PyPI
via the embedded pip.

That is the whole reason this distribution is named `uwsgi-stats` and not
`datadog-uwsgi-stats`. Under the old name the Agent looked for the check in
Datadog's repository, which has never hosted it, and the restore failed — taking
the entire `apt upgrade` down with it:

```
datadog_checks.downloader.exceptions.NoSuchDatadogPackage: datadog-uwsgi-stats
ERROR: post failed to restore custom integrations
```

Under the new name the restore resolves against PyPI and succeeds unattended, so
**no action is needed after an Agent upgrade** — provided the host can reach
PyPI at upgrade time and the release is published there. If it cannot, re-run
`scripts/build-and-install.sh` and restart the Agent.

If you are still on 1.0.0 and hit the failure above, this unblocks `dpkg`:

```bash
sudo touch /etc/datadog-agent/.skip_install_python_third_party_deps
sudo dpkg --configure -a
```

That flag makes the Agent skip the restore entirely, on this and every future
upgrade. It stops the failure but not the wipe, so you must reinstall the check
yourself afterwards — upgrading to 1.1.0 and removing the flag is the fix.

### "no such file or directory" on install

If an install ends like this, pip believes the check is installed and its files
are not there:

```
uwsgi-stats is already installed with the same version as the provided wheel.
Use --force-reinstall to force an installation of the wheel.
Error: Some errors prevented moving uwsgi-stats configuration files:
open .../site-packages/datadog_checks/uwsgi_stats/data: no such file or directory
```

Pip decides "already installed" from the `.dist-info` metadata alone and never
checks that the files still exist, so it skips the install; the Agent's
post-install step then opens a package directory that was never written. Left
alone this repeats forever — the Agent has no `--force-reinstall` (that line is
pip's advice about a flag the Agent does not expose) and `integration remove`
rejects this package's name.

`scripts/build-and-install.sh` detects and clears this before installing, so
re-running it is the fix. With an older copy of the script, clear the metadata
by hand first:

```bash
sudo -u dd-agent /opt/datadog-agent/embedded/bin/pip uninstall -y uwsgi-stats
./scripts/build-and-install.sh
```

`sudo -u dd-agent datadog-agent check uwsgi_stats` reports `No module named
'datadog_checks.uwsgi_stats'` while the check is missing, and an `[OK]` instance
once it is back.

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

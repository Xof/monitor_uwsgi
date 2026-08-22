---
id: 0004
title: Drop the datadog- prefix from the PyPI distribution name
date: 2026-08-22
status: Accepted
summary: Rename the distribution from datadog-uwsgi-stats to uwsgi-stats so the Agent's upgrade restore reinstalls it from PyPI via embedded pip instead of Datadog's TUF repo, which does not host it and made every Agent apt upgrade fail.
---

# 0004. Drop the datadog- prefix from the PyPI distribution name

## Context

An `apt upgrade` of the Datadog Agent (to 1:7.82.2-1) failed in `postinst` with
`NoSuchDatadogPackage: datadog-uwsgi-stats`, leaving `dpkg` in a failed-config
state and this check uninstalled.

The Agent `.deb` preserves custom integrations across upgrades with a
save/restore pass in `omnibus/python-scripts/`. `pre.py` snapshots the embedded
Python's distributions and diffs them against the baseline written by the
previous install, producing `/opt/datadog-agent/.diff_python_installed_packages.txt`.
The upgrade then replaces the embedded Python — wiping every custom integration —
and `post.py` reinstalls whatever the diff lists.

The restore routes each package by **name alone** (`packages.py`,
`install_diff_packages_file`):

```python
if install_package_line.startswith('datadog-') and dep_name not in DEPS_STARTING_WITH_DATADOG:
    install_datadog_package(...)          # datadog-agent integration install -t -> Datadog's TUF repo
else:
    install_dependency_package(pip, ...)  # embedded pip -> PyPI
```

`DEPS_STARTING_WITH_DATADOG` is a hand-maintained allowlist of PyPI packages
that begin with `datadog-` but are not Datadog integrations. `datadog-uwsgi-stats`
matched the prefix and was absent from the allowlist, so the restore was sent to
Datadog's TUF-signed integration repository — which only carries integrations
Datadog itself publishes, and has never carried this one. Both attempts failed,
`post.py` raised `IntegrationsRestoreError`, and the package transaction failed.

Nothing about this is transient: it would recur on **every** Agent upgrade, on
every host running the check. The name we chose to look like an official
integration is precisely what makes the Agent treat it as one.

There is a supported escape hatch — `/etc/datadog-agent/.skip_install_python_third_party_deps`,
which makes `post.py` return 0 without attempting any restore — but it disables
restore for *all* third-party integrations and, worse, converts a loud `apt`
failure into a silent one: the check is still wiped by each upgrade and simply
stops reporting until someone notices.

## Decision

We will rename the PyPI distribution from `datadog-uwsgi-stats` to
**`uwsgi-stats`**, released as 1.1.0. The importable package path
(`datadog_checks/uwsgi_stats/`), the check name (`uwsgi_stats`), the config
directory (`conf.d/uwsgi_stats.d/`), and every metric name are unchanged.

This works because the Agent derives the check name from the distribution name
by stripping a leading `datadog-` and mapping `-` to `_`
(`getIntegrationName` in `cmd/agent/subcommands/integrations/command.go`):
`uwsgi-stats` has no prefix to strip, so it still yields `uwsgi_stats` and the
same `conf.d` destination the old name produced. Installation via
`datadog-agent integration install -w` continues to work because that path
validates a local wheel by checking it declares `Requires-Dist: datadog-checks-base`,
not by inspecting its name (`validateArgs` skips the name check when `local` is
true).

After the rename, `post.py` routes the restore through
`/opt/datadog-agent/embedded/bin/pip install uwsgi-stats==<version>`, which
resolves against PyPI, where we publish. Agent upgrades restore the check
unattended.

Separately and in parallel, we will submit a one-line upstream PR adding
`datadog-uwsgi-stats` to `DEPS_STARTING_WITH_DATADOG`, so that hosts still
running 1.0.0 recover on Agent versions that ship the fix. That is a
compatibility courtesy for the installed base, not the mechanism this project
relies on.

## Alternatives considered

- **Rely solely on the upstream allowlist PR, keep the name.** Rejected as the
  primary fix. It is the smallest change and preserves the descriptive name, but
  it puts this project's ability to survive an Agent upgrade behind a
  third-party merge, release, and fleet-wide Agent rollout, with no recourse if
  Datadog declines. It also only ever helps on Agent versions ≥ whatever ships
  it. Submitted anyway, for the benefit of existing 1.0.0 installs.

- **Set `.skip_install_python_third_party_deps` and re-install after every
  upgrade.** Rejected. It does stop `apt` failing, and needs no code change, but
  it trades a loud failure for a silent monitoring gap: the check is still
  wiped each upgrade, and nothing surfaces that fact. Anyone driving Agent
  upgrades from config management could bolt a re-install step on afterwards —
  a legitimate deployment choice, but not something the project should require.

- **Rename to something else non-prefixed, e.g. `uwsgi-stats-check`.**
  Rejected. Any name works for the restore routing, but `getIntegrationName`
  maps the distribution name to the check name, so `uwsgi-stats-check` would
  make the Agent look for `datadog_checks/uwsgi_stats_check/data` and install
  config into `conf.d/uwsgi_stats_check.d/`. That is a breaking change to the
  check name and every existing `conf.yaml` location, for no benefit.
  `uwsgi-stats` is the unique name that changes the distribution identity while
  leaving the check identity untouched. It was available on PyPI.

- **Revert to a `checks.d/` drop-in.** Rejected. A drop-in lives in
  `/etc/datadog-agent/checks.d/`, outside the embedded Python, so it is immune
  to this failure entirely. But it reverses ADR 0002 — losing the multi-module
  collector layout, the versioned install story, and the packaging metadata —
  to solve a problem a rename solves outright.

## Consequences

- Agent upgrades restore the check without operator action, and `apt upgrade`
  stops failing. This is the whole point.

- **The check becomes invisible to `datadog-agent integration freeze`**, which
  filters its output to lines beginning with `datadog-` (`list` in
  `integrations/command.go`). Use
  `sudo -u dd-agent /opt/datadog-agent/embedded/bin/pip show uwsgi-stats`
  instead.

- **`datadog-agent integration remove uwsgi-stats` and `integration show
  uwsgi-stats` are rejected**, because both call `validateArgs(args, local=false)`,
  which enforces the `datadog-` prefix. Removal must go through embedded pip.
  Installation via `-w` is unaffected.

- **Upgrading an existing 1.0.0 host requires removing the old distribution
  first.** Both distributions own the same `datadog_checks/uwsgi_stats/` files;
  leaving `datadog-uwsgi-stats` installed means a later
  `pip uninstall datadog-uwsgi-stats` deletes the *new* install's files.
  `scripts/build-and-install.sh` now probes for the legacy distribution and
  removes it before installing, in that order.

- The restore path now pulls from PyPI over plain TLS rather than Datadog's
  TUF-signed repository. This is the same trust level the project already
  operated at — `integration install -w` performs no signature verification
  either, and the README already says so — but it is now the *automatic*
  path, exercised unattended on every Agent upgrade, and it requires the host
  to have outbound PyPI access at upgrade time. A host without it fails the
  restore exactly as before.

- Two distribution names now exist on PyPI. `datadog-uwsgi-stats` stays
  published at 1.0.0 rather than being yanked, so the upstream allowlist fix
  has something to resolve against for existing installs; it receives no further
  releases.

- The PyPI trusted-publishing configuration must be re-created for the new
  project name before 1.1.0 can be released — a pending publisher for
  `datadog-uwsgi-stats` does not carry over to `uwsgi-stats`.

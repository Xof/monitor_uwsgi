# Changelog

## 1.1.1 / 2026-08-27

- `scripts/build-and-install.sh` now repairs an installation whose pip metadata
  outlived its files. Pip reports such a distribution as already installed and
  skips, after which the Agent's post-install step fails on the missing package
  directory — a state no re-run could clear, because the Agent exposes no
  `--force-reinstall` and `integration remove` refuses this package's name. The
  script probes for it with the Agent's embedded interpreter and clears the
  stale metadata before installing. The probe runs after the
  legacy-distribution retire, which is one of the ways the state arises, and
  only a positive identification removes anything: an unreadable interpreter is
  a no-op. Adds `DD_EMBEDDED_PYTHON` (default
  `/opt/datadog-agent/embedded/bin/python`) and
  `tests/test_build_and_install.py`, the script's first coverage. See ADR 0006
  and issue #10.
- Documented that the re-raise in `check()` after a failed stats read is
  load-bearing, not an implementation detail: the Agent collector marks an
  instance `[ERROR]` only when `check()` raises, and deploy tooling reads that
  marker because `datadog-agent check` exits 0 for an instance error. Replacing
  it with the more common catch-CRITICAL-and-`return` idiom would report a
  refused stats socket as `[OK]`. No behavior change; see ADR 0005 and issue #7.

## 1.1.0 / 2026-08-22

- **Renamed the PyPI distribution from `datadog-uwsgi-stats` to `uwsgi-stats`.**
  The check name (`uwsgi_stats`), config path (`conf.d/uwsgi_stats.d/`), and all
  metric names are unchanged. The old name made the Datadog Agent's upgrade
  restore look for the check in Datadog's integration repository, which does not
  host it, failing every `apt upgrade` of the Agent in `postinst`. See ADR 0004
  and the "Upgrading from `datadog-uwsgi-stats` 1.0.0" section of the README.
- `scripts/build-and-install.sh` now removes a previously installed
  `datadog-uwsgi-stats` before installing, and `--help` no longer truncates when
  the script header grows.

## 1.0.0 / 2026-07-11

- Initial release: uWSGI stats server Agent check (`uwsgi_stats`).

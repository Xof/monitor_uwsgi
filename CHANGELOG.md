# Changelog

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

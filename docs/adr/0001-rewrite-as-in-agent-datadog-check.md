---
id: 0001
title: Rewrite the uWSGI monitor as an in-Agent Datadog Agent check
date: 2026-07-11
status: Accepted
summary: Ship the full uWSGI stats set from a custom AgentCheck (uwsgi_stats) so cumulative counters can be submitted as monotonic_count, replacing the cron + DogStatsD single-metric script.
---

# 0001. Rewrite the uWSGI monitor as an in-Agent Datadog Agent check

## Context

The project began as a cron-driven Python script that connected to the uWSGI
stats server, parsed the JSON snapshot, and emitted a single metric
(`uwsgi.total_queue_depth`) to DogStatsD. The goal was to ship the *full* uWSGI
stats set (per-worker, per-socket, and aggregate) to Datadog.

The stats server's own C emitter (`uwsgi_master_generate_stats()` in
`core/master_utils.c`, verified against uWSGI 2.0.x source) exposes most
throughput fields — `requests`, `tx`, `exceptions`, `harakiri_count`, `signals`,
`running_time`, `respawn_count`, cache `hits`/`miss`/`full`, spooler
`tasks`/`respawns` — as **cumulative counters that reset to 0 when the emitting
worker or process respawns**. The only correct Datadog primitive for an
externally-read cumulative total is `monotonic_count`, which diffs successive
samples and drops the negative delta on a reset. Neither the DogStatsD client
nor uWSGI's own stats pushers can produce that semantic. Getting the full set of
metrics into Datadog *correctly* therefore forced an architecture decision, not
just more `gauge()` calls.

## Decision

We will implement a custom Datadog **Agent check** — a `datadog_checks.base.AgentCheck`
subclass, `UwsgiStatsCheck`, packaged as the `uwsgi_stats` integration (named to
avoid colliding with Datadog's existing logs-only `uwsgi` tile; metric namespace
stays `uwsgi.`). The Agent schedules it, so cron is retired and the direct
DogStatsD/`datadog` client is dropped. `check()` reads the stats server (tcp,
unix, or http transport, read-to-EOF), submits `uwsgi.can_connect`, iterates a
registry of small per-section collectors, then submits a derived
`uwsgi.worker_saturation` health service check.

Cumulative counter fields are submitted as `monotonic_count`; instantaneous
fields (queue depths, rss/vsz, avg_rt, worker status counts) as `gauge`. Default
coverage is aggregate + per-worker + per-socket, with per-app/cache/spooler
sections auto-detected and per-core metrics opt-in (`collect_per_core`) because
they are high-cardinality and near-duplicate per-worker counts on the common
single-core setup.

## Alternatives considered

- **Expand the standalone cron + DogStatsD script** — Rejected. The DogStatsD
  client has no `monotonic_count`; submitting cumulative counters as gauges
  produces sawtooth garbage on every worker respawn, and it is not a "Datadog
  plugin" in the sense the project wanted.
- **uWSGI's native `dogstatsd` stats-pusher** (`--stats-push dogstatsd:...`) —
  Rejected. It sends the cumulative totals as StatsD `|c` increments, re-adding
  the entire running total on every push and inflating the counter; it also
  offers no control over metric selection, naming, or tagging.
- **Do nothing / keep only queue depth** — Rejected; the explicit goal was full
  visibility (per-worker throughput, memory, exceptions, saturation).

## Consequences

- The check runs inside the **Datadog Agent's embedded Python** (Agent 7,
  ~3.11/3.12), not the host's Python 3.13. Runtime code targets `>=3.8`; the
  `datadog`, `click`, and `pip-tools` dependencies and the old cron wrapper are
  removed.
- Counter correctness is now structural (`monotonic_count`), so worker respawns
  no longer corrupt rate graphs — the central reason for the rewrite.
- One piece of cross-scrape state lives on the check instance (`_prev_worker`)
  to compute `worker.mean_rt = Δrunning_time/Δrequests`; it is safe under the
  Agent's one-check-object-per-instance model.
- Deployment now requires the Datadog Agent on the uWSGI hosts and a
  `conf.d/uwsgi_stats.d/conf.yaml`; there is no longer a self-contained cron
  process. Packaging is addressed separately in ADR 0002.
- Full design detail: `docs/superpowers/specs/2026-07-11-uwsgi-datadog-plugin-design.md`.

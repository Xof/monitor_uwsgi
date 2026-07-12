# Design: uWSGI → Datadog Agent Check (`uwsgi_stats`)

- **Date:** 2026-07-11
- **Status:** Approved for implementation planning
- **Author:** Christophe Pettus
- **Supersedes:** the cron + DogStatsD queue-depth script (`src/monitor_uwsgi/`, `monitor_uwsgi.sh`)

## 1. Goal

Replace the current cron-driven script — which emits a single metric (`uwsgi.total_queue_depth`) to DogStatsD — with a proper **Datadog Agent custom integration** (`AgentCheck` subclass) that reads the uWSGI stats server on each collection cycle and ships the full, correctly-typed metric set for the whole uWSGI instance: global rollups, per-worker, and per-socket, with auto-detected per-app / cache / spooler sections and an opt-in per-core mode.

Non-goals for v1: dashboards, monitors (these live in Datadog, tuned separately), events, and the `legions[]`/`crons[]`/`daemons[]`/`metrics{}` stats sections.

## 2. Why an Agent Check (and not the alternatives)

Research (source-verified against uWSGI 2.0.x `core/master_utils.c`, `core/utils.c`, and Datadog developer docs) confirmed:

- **Datadog ships no uWSGI *metrics* integration** — only a logs-only tile at `integrations/uwsgi`. There is nothing to reuse; a custom check is the right build. To avoid colliding with that existing `uwsgi` tile/`integration_id` on a host, **our integration is named `uwsgi_stats`** (the metric namespace remains `uwsgi.`).
- Most uWSGI stats fields (`requests`, `tx`, `exceptions`, `running_time`, …) are **cumulative counters that reset to 0 when a worker respawns**. The only correct Datadog primitive for an externally-read cumulative total is **`monotonic_count`**, which diffs successive samples and drops the negative delta on reset. This is available *only* inside the Agent check model.
  - The current script's DogStatsD client has **no `monotonic_count`** — so expanding it could not be made correct.
  - uWSGI's native `dogstatsd` stats-pusher sends those totals as StatsD `|c` increments, **re-adding the entire running total every push** → inflated counters. Also wrong.
- The Agent schedules the check (default `min_collection_interval` 15s), attaches hostname/tags, batches submission, and **never overlaps a run with itself** — unlike cron.

## 3. Deployment model & naming

| Item | Value |
|---|---|
| Integration / check name (`integration_id`) | `uwsgi_stats` |
| Metric namespace (`AgentCheck.__NAMESPACE__`) | `uwsgi` (every metric auto-prefixed `uwsgi.`) |
| Check class | `UwsgiStatsCheck(AgentCheck)` |
| Config file | `conf.d/uwsgi_stats.d/conf.yaml` |
| Service checks | `uwsgi.can_connect`, `uwsgi.worker_saturation` |
| Runs in | the Datadog Agent's embedded Python (Agent 7) — **not** system Python 3.13 |

**Multi-instance is native:** the Agent runs `check()` once per entry in `instances:`, each an isolated execution. Monitoring several uWSGI processes on one host is purely a config concern — no code for it.

## 4. Packaging

**Decision: a properly packaged integration wheel** (not a `checks.d/` drop-in), because this may become a **public `integrations-extras` / PyPI release**, for which the packaging metadata (`manifest.json`, `metadata.csv`, README, versioning, tests) is expected infrastructure, not ceremony.

The repository root *is* the integration (single-integration repo — no nested `uwsgi_stats/` wrapper dir).

```
monitor_uwsgi/                      (repo root)
  datadog_checks/                   PEP 420 namespace pkg (NO __init__.py here)
    uwsgi_stats/
      __init__.py                   exports UwsgiStatsCheck, __version__
      __about__.py                  __version__ = "0.1.0"
      check.py                      UwsgiStatsCheck: orchestration + service checks
      stats.py                      read_stats(url, timeout) -> dict  (transport + parse)
      health.py                     evaluate_saturation(stats, instance) -> (status, message)
      metrics/                      one focused collector module per section
        __init__.py                 COLLECTORS registry (ordered list of collect() callables)
        aggregate.py                global rollups + workers.by_status/total
        workers.py                  per-worker metrics
        sockets.py                  per-socket metrics
        apps.py                     per-app (only when a worker has >1 app)
        caches.py                   per-cache (only when present)
        spoolers.py                 per-spooler (only when present)
        cores.py                    per-core (only when collect_per_core: true)
      data/
        conf.yaml.example           documented config template
  tests/
    conftest.py                     fixtures (parsed stats, an instance)
    fixtures/stats.json             realistic full stats snapshot (drives metric tests)
    test_stats.py                   transport: read-until-EOF, tcp/unix/http, short read, parse error
    test_metrics.py                 asserts every metric name + type + tags via aggregator
    test_health.py                  saturation service-check thresholds
    test_check.py                   end-to-end check() incl. can_connect OK/CRITICAL
  metadata.csv                      full metric catalog (feeds the public catalog)
  manifest.json                     integration_id, categories, min agent version, etc.
  README.md                         setup + metric reference
  CHANGELOG.md
  pyproject.toml                    build config; dep: datadog-checks-base; targets Agent Python
  .github/workflows/ci.yml          lint + type-check + tests on push/PR
```

**Build & install:** `python -m build` (or `hatch build`) → wheel → `datadog-agent integration install -w dist/datadog_uwsgi_stats-<ver>-py3-none-any.whl`. Also `pip`-installable into the Agent's env because it depends only on `datadog-checks-base` (published on PyPI).

**Removed from the current project:** `src/monitor_uwsgi/` (entire package), `monitor_uwsgi.sh`, `initialize.py`, and the `datadog`, `click`, `pip-tools` dependencies plus the `requires-python = ">=3.13"` pin (the check must target the Agent's embedded Python; pin to `>=3.8`).

**Code organization** preserves the project's "small, single-purpose files" instinct from the old `metrics/` package: `check.py` reads stats once, then iterates the `COLLECTORS` registry; each collector `collect(check, stats, base_tags)` owns exactly one stats section and calls `check.gauge(...)` / `check.monotonic_count(...)`. No monolithic check file.

## 5. Configuration (`conf.yaml.example`)

```yaml
init_config:

instances:
    ## @param stats_url - string - required
    ## uWSGI stats server address. Scheme selects transport:
    ##   tcp://HOST:PORT       — uWSGI --stats HOST:PORT
    ##   unix:///path/to.sock  — uWSGI --stats /path/to.sock
    ##   http://HOST:PORT      — uWSGI --stats-http HOST:PORT
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

`stats_url` default (`tcp://127.0.0.1:1717`) matches the current script's `127.0.0.1:1717`.

## 6. Data flow / check lifecycle

```
Agent scheduler (per instance, every min_collection_interval)
        │
        ▼
UwsgiStatsCheck.check(instance)
        │  1. base_tags = list(instance['tags'])           # explicit; no auto-merge in DD
        │  2. stats = read_stats(stats_url, timeout)        # transport dispatch + read-to-EOF + json.loads
        │        └─ on failure: service_check uwsgi.can_connect CRITICAL(msg); re-raise
        │  3. service_check uwsgi.can_connect OK
        │  4. for collect in COLLECTORS: collect(self, stats, base_tags)
        │        gauge / monotonic_count with per-section tags (worker_id, socket_name, …)
        │  5. status,msg = evaluate_saturation(stats, instance)
        │     service_check uwsgi.worker_saturation status(msg)
        ▼
Agent forwarder → Datadog (hostname + tags attached by Agent)
```

## 7. Metric catalog

Source-of-truth: `uwsgi_master_generate_stats()`. **`monotonic_count`** = cumulative counter (respawn-reset-safe). **`gauge`** = instantaneous. Units are called out because uWSGI mixes microseconds, bytes, and epoch seconds.

### 7a. Global / aggregate — no per-entity tags
| Metric | Type | Source | Unit / notes |
|---|---|---|---|
| `uwsgi.listen_queue` | gauge | `listen_queue` | connections; **Linux only** |
| `uwsgi.listen_queue_errors` | monotonic_count | `listen_queue_errors` | **Linux only** |
| `uwsgi.signal_queue` | gauge | `signal_queue` | master signal-pipe backlog |
| `uwsgi.workers.total` | gauge | `len(workers)` | computed |
| `uwsgi.workers.by_status` | gauge | bucket `workers[].status` | tag `status:idle\|busy\|cheap\|pause\|sig`; one datapoint per bucket |

`load` (top-level) is intentionally **not** emitted — the uWSGI source sets it to a copy of the backlog with a `// TODO` and it duplicates `listen_queue`.

### 7b. Per-socket — tags `socket_name:<name>`, `proto:<proto>`
| Metric | Type | Source |
|---|---|---|
| `uwsgi.socket.queue` | gauge | `sockets[].queue` |
| `uwsgi.socket.max_queue` | gauge | `sockets[].max_queue` |

### 7c. Per-worker — tag `worker_id:<id>` (never `pid` — unbounded across respawns)
| Metric | Type | Source | Unit / notes |
|---|---|---|---|
| `uwsgi.worker.requests` | monotonic_count | `workers[].requests` | primary throughput |
| `uwsgi.worker.tx` | monotonic_count | `workers[].tx` | bytes (body+headers) |
| `uwsgi.worker.exceptions` | monotonic_count | `workers[].exceptions` | already Σ of per-core; do not also sum cores |
| `uwsgi.worker.harakiri_count` | monotonic_count | `workers[].harakiri_count` | request-timeout kills |
| `uwsgi.worker.signals` | monotonic_count | `workers[].signals` | |
| `uwsgi.worker.running_time` | monotonic_count | `workers[].running_time` | **microseconds** |
| `uwsgi.worker.respawn_count` | monotonic_count | `workers[].respawn_count` | starts at 1 |
| `uwsgi.worker.signal_queue` | gauge | `workers[].signal_queue` | |
| `uwsgi.worker.avg_rt` | gauge | `workers[].avg_rt` | **µs**; a `(a+b)/2` smoother, not a mean |
| `uwsgi.worker.mean_rt` | gauge | Δ`running_time`/Δ`requests` (computed) | **µs**; the honest latency signal |
| `uwsgi.worker.rss` | gauge | `workers[].rss` | bytes; **0 unless `--memory-report`** |
| `uwsgi.worker.vsz` | gauge | `workers[].vsz` | bytes; same caveat |
| `uwsgi.worker.accepting` | gauge | `workers[].accepting` | 1 = in accept loop |
| `uwsgi.worker.uptime` | gauge | `now - workers[].last_spawn` (computed) | seconds |

`mean_rt` requires remembering the previous scrape's `running_time`/`requests` per `worker_id`. Implementation: keep a small per-instance in-memory dict on the check object keyed by `worker_id`; on the first scrape (or after a respawn where the counter dropped) skip emitting `mean_rt`. This is the one piece of cross-scrape state; everything else is stateless (the Agent handles counter deltas).

`delta_requests` is intentionally skipped — it's a `--max-requests`-window subset of `requests`.

### 7d. Per-app — **only when a worker reports >1 app** — tags `worker_id`, `app_id`, `mountpoint`
| Metric | Type | Source |
|---|---|---|
| `uwsgi.worker.app.requests` | monotonic_count | `workers[].apps[].requests` |
| `uwsgi.worker.app.exceptions` | monotonic_count | `workers[].apps[].exceptions` |

(Gated on `len(apps) > 1` so single-app deployments don't duplicate the worker-level counters.)

### 7e. Per-cache — when `caches[]` present — tag `cache:<name>`
| Metric | Type | Source |
|---|---|---|
| `uwsgi.cache.items` | gauge | `caches[].items` |
| `uwsgi.cache.hits` | monotonic_count | `caches[].hits` |
| `uwsgi.cache.miss` | monotonic_count | `caches[].miss` |
| `uwsgi.cache.full` | monotonic_count | `caches[].full` |

### 7f. Per-spooler — when `spoolers[]` present — tag `spooler:<dir>`
| Metric | Type | Source |
|---|---|---|
| `uwsgi.spooler.tasks` | monotonic_count | `spoolers[].tasks` |
| `uwsgi.spooler.respawns` | monotonic_count | `spoolers[].respawns` |
| `uwsgi.spooler.running` | gauge | `spoolers[].running` |

### 7g. Per-core — **off by default**, enabled via `collect_per_core` — tags `worker_id`, `core_id`
`uwsgi.worker.core.{requests,static_requests,routed_requests,offloaded_requests,write_errors,read_errors}` (monotonic_count) and `uwsgi.worker.core.in_request` (gauge). High cardinality; ≈ duplicates per-worker on the common single-core (sync) setup, and vanishes under `--stats-no-cores`.

## 8. Service checks

### `uwsgi.can_connect`
`OK` on a clean connect + read + parse. `CRITICAL` (with the exception message) on any connect/read/`JSONDecodeError` failure. Tagged with `base_tags`. On failure the check submits CRITICAL and re-raises so the Agent's check status reflects the error; because instances are isolated executions, one unreachable stats server cannot blank out another instance.

### `uwsgi.worker_saturation` (the opt-in derived health check)
Computed in `health.py` from the parsed stats + instance thresholds. Keys on the classic uWSGI capacity signal — the **listen queue filling because workers can't keep up**:

- `sat = max over sockets of (queue / max_queue)` for sockets with `max_queue > 0`.
- `CRITICAL` if `sat >= worker_saturation_critical` (default 0.9) — backlog nearly full, connection refusals imminent.
- `WARNING` if `sat >= worker_saturation_warning` (default 0.5), **or** if every non-cheap worker is `busy` **and** `listen_queue > 0` (all workers busy with requests waiting — "add workers").
- `OK` otherwise.
- If no socket exposes a usable `max_queue` (e.g. non-Linux), fall back to the all-busy-with-queue rule only; if even `listen_queue` is absent, report `OK` (nothing to assess) rather than guessing.

Message always includes the numbers (`sat=0.72 (queue 36/50), workers busy 8/8`) so an alert is self-explanatory. Thresholds live in `conf.yaml` so they're tunable without a plugin redeploy; sharper alerting still belongs in Datadog monitors on the raw metrics.

> Implementation note: confirm whether `datadog-checks-base` namespaces `service_check` names under `__NAMESPACE__`. If it does, pass `can_connect`/`worker_saturation`; if not, pass the full `uwsgi.` names. Either way the external names are `uwsgi.can_connect` / `uwsgi.worker_saturation`.

## 9. Stats acquisition (`stats.py`) & correctness guarantees

The stats server **writes one JSON object and closes the connection** — no length prefix, no framing. A single `recv()` truncates any non-trivial payload. Guarantees baked in:

1. **Read to EOF, then parse.** `sock.makefile().read()` (or loop `recv()` until empty) to get the whole buffer, then `json.loads`. A truncated buffer surfaces as `JSONDecodeError` → CRITICAL `can_connect`, never a stack trace that kills the cycle.
2. **Transport dispatch off the `stats_url` scheme:** `tcp://` → `AF_INET` socket; `unix://` → `AF_UNIX` socket; `http(s)://` → HTTP GET (Agent's bundled `requests`, or stdlib `urllib`). Missing/unparseable `stats_url` → `ConfigurationError`.
3. **`timeout`** applied to connect and read (default 5s) so a wedged stats server can't stall the Agent's collector.
4. **Defensive parsing everywhere.** `.get()` for every optional section — `cores`/`caches`/`spoolers`/`listen_queue*` are feature- or OS-gated and legitimately absent. Skip absent sections; never `KeyError`.
5. **Counters via `monotonic_count` only** — never hand-roll a diff, never emit a counter as a gauge (loses the rate) or via StatsD `|c` (inflates). `respawn_count` starts at 1 (subtract 1 only if ever displaying "number of respawns").
6. **`worker_id` (bounded) is the stable worker identity; `pid` is never a tag** (it churns on every respawn → unbounded cardinality).

## 10. Testing

Uses `datadog_checks.dev`'s `aggregator` fixture; the check is instantiated and run with **no live Agent**: `UwsgiStatsCheck('uwsgi_stats', {}, [instance])`.

- `tests/fixtures/stats.json` — a realistic, source-schema-accurate snapshot: several workers (mixed `idle`/`busy`, one recently respawned), 2 sockets, one worker with 2 apps, one cache. Drives the metric assertions.
- `test_metrics.py` — for every catalog metric, `aggregator.assert_metric(name, metric_type=aggregator.MONOTONIC_COUNT|GAUGE, tags=[...])`; assert per-worker `worker_id` tags and `workers.by_status` buckets; `aggregator.assert_all_metrics_covered()`.
- `test_stats.py` — spin a throwaway loopback TCP server (and an `AF_UNIX` one) that writes the fixture JSON then closes; assert `read_stats` reassembles the full payload across multiple `recv()`s (short-read regression); assert a truncated write raises the parse error path.
- `test_health.py` — table of `(sockets, workers, thresholds) → expected status` covering OK / WARNING (both triggers) / CRITICAL / no-max_queue fallback.
- `test_check.py` — end-to-end `check()` with `read_stats` monkeypatched to the fixture (asserts `can_connect` OK); and with it raising (asserts `can_connect` CRITICAL + re-raise).

Target near-100% coverage. **CI** (`.github/workflows/ci.yml`) runs lint (`ruff`), type-check (`mypy`), and `pytest` on every push and PR; benchmarks are not relevant here.

## 11. Migration & rollout

1. Land the new integration; delete the old package/script/deps (§4).
2. Enable the uWSGI stats server if not already (`--stats 127.0.0.1:1717` or `--stats-http`).
3. Build the wheel, `datadog-agent integration install -w …`, drop `conf.yaml`, `datadog-agent reload`.
4. Verify with `datadog-agent check uwsgi_stats` (shows metrics + service checks without waiting for a scheduled run).
5. Old cron entry and `monitor_uwsgi.sh` removed. The single old metric `uwsgi.total_queue_depth` is retired (confirmed nothing depends on it); its successor is `uwsgi.socket.queue` / `uwsgi.listen_queue`.

## 12. Open items deferred to v1 implementation review
- Exact `manifest.json` fields / min agent version and whether to pursue `integrations-extras` submission now or later.
- Whether `avg_rt` is worth emitting at all given `mean_rt` supersedes it (leaning: emit both; `avg_rt` is free and familiar).
- Confirm `service_check` namespacing behavior (§8 note) during implementation.

# Architecture

A Datadog Agent custom check (`uwsgi_stats`) that reads the uWSGI stats server
once per collection cycle and submits the full metric set. The one fact to hold
first: **uWSGI's cumulative counter fields reset to 0 on worker respawn, so they
must be submitted as `monotonic_count`, never `gauge`** — everything else is
shaped around routing each stats section through a collector that makes that
counter-vs-gauge choice correctly.

## Component map

| Component | Responsibility | Entry points |
|---|---|---|
| `datadog_checks/uwsgi_stats/check.py` | Orchestration: read stats, submit `can_connect`, run collectors, submit `worker_saturation`; holds cross-scrape `_prev_worker` state | `UwsgiStatsCheck.check(instance)` |
| `datadog_checks/uwsgi_stats/stats.py` | Transport (tcp/unix/http) + read-to-EOF + JSON parse | `read_stats(stats_url, timeout)` |
| `datadog_checks/uwsgi_stats/metrics/__init__.py` | Ordered `COLLECTORS` registry the check iterates | `COLLECTORS` |
| `metrics/aggregate.py` | Global rollups (listen/signal queue) + `workers.total`/`workers.by_status` | `collect(check, stats, base_tags)` |
| `metrics/sockets.py`, `workers.py`, `apps.py`, `caches.py`, `spoolers.py`, `cores.py` | One stats section each; every collector is `collect(check, stats, base_tags)` | same signature |
| `datadog_checks/uwsgi_stats/health.py` | Derived saturation status for the `worker_saturation` service check | `evaluate_saturation(stats, instance)` |
| `data/conf.yaml.example`, `metadata.csv`, `manifest.json`, `assets/` | Packaging: config template, metric catalog, tile manifest, service-check/config-spec assets | — |
| `scripts/build-and-install.sh` | Operator install helper: build wheel → stage under `/tmp` → install as `dd-agent` → stage `conf.yaml` → print verify/restart (ADR 0003) | `./scripts/build-and-install.sh` |
| `tests/` | pytest suite; `fixtures/stats.json` is the canonical stats snapshot | `tests/conftest.py` |

## Invariants

- **Cumulative counters → `monotonic_count`; instantaneous values → `gauge`.** Breaks: a counter as a gauge produces sawtooth garbage on every worker respawn; a gauge as a counter mis-aggregates in Datadog. Each field's classification is fixed against uWSGI's C stats emitter and recorded in `metadata.csv`.
- **Metric and service-check names are passed WITHOUT the `uwsgi.` prefix.** `AgentCheck.__NAMESPACE__ = "uwsgi"` prepends it (for both metrics and service checks). Breaks: double-prefix `uwsgi.uwsgi.*`, or an un-namespaced metric.
- **Worker metrics are tagged `worker_id`, never `pid`.** Breaks: `pid` churns on respawn → unbounded tag cardinality.
- **Collectors never mutate `base_tags`; they build new lists (`base_tags + [...]`).** Breaks: tags leak across workers/sockets/scrapes and corrupt the caller's list.
- **`read_stats` reads to EOF, then parses.** The stats server writes one JSON object and closes with no framing. Breaks: a single `recv()` truncates any non-trivial payload → `JSONDecodeError`.
- **Optional stats sections are accessed via `.get()`.** `cores`/`caches`/`spoolers`/`listen_queue*` are feature- or OS-gated. Breaks: `KeyError` against a uWSGI built/configured without them.
- **`ConfigurationError` propagates un-wrapped from `check()`; a connect/read/parse failure submits `uwsgi.can_connect` CRITICAL then re-raises.** Breaks: a misconfiguration reported as a spurious connectivity alert (the `except ConfigurationError: raise` clause must stay ordered before the broad `except`).
- **That re-raise is an external contract, not an implementation detail.** The Agent collector marks an instance `[ERROR]` only when `check()` raises, and deploy tooling outside this repository reads that marker to decide whether a host's uWSGI vassal is answering — it cannot read the exit status, because `datadog-agent check` exits 0 for an instance error. That tooling tracks this repo at branch head, so the coupling is live. Breaks: swapping the `raise` for the more idiomatic catch-CRITICAL-and-`return` reports a refused stats socket as `[OK]`, and a dead vassal converges green fleet-wide, with nothing in either repo's tests able to see it (ADR 0005).
- **Service checks carry `message=None` when status is OK.** Breaks: the `datadog-checks-base` aggregator rejects a message on an OK service check.
- **`_prev_worker` (for `worker.mean_rt`) is keyed by `worker_id` only** and is updated every scrape before the delta guard. Safe under the Agent's one-check-object-per-instance model. Breaks: if one check object ever served multiple instances, `mean_rt` baselines cross-contaminate.
- **Runtime code targets Python `>=3.8`** (the Agent's embedded interpreter), not the host's 3.13. Breaks: the integration becomes un-installable in the Agent.
- **`datadog_checks/` is a PEP 420 namespace package — no top-level `datadog_checks/__init__.py`.** Breaks: shadows `datadog-checks-base`'s namespace.
- **The PyPI distribution is named `uwsgi-stats` and must NOT start with `datadog-`.** The Agent's upgrade restore (`omnibus/python-scripts/packages.py`) routes by name: a `datadog-`-prefixed package not on Datadog's hardcoded `DEPS_STARTING_WITH_DATADOG` allowlist is fetched from Datadog's TUF repo, everything else from PyPI via embedded pip. Breaks: every `apt upgrade` of the Agent fails in `postinst` with `NoSuchDatadogPackage` and leaves the check uninstalled (ADR 0004). The importable path stays `datadog_checks/uwsgi_stats/` — only the distribution name is constrained.
- **The distribution name determines the check name.** The Agent computes it as `strip_prefix("datadog-")` then `-`→`_` (`getIntegrationName`). `uwsgi-stats` → `uwsgi_stats` → `conf.d/uwsgi_stats.d/`. Breaks: renaming the distribution to anything else (e.g. `uwsgi-stats-check`) silently relocates the config directory and makes the Agent look for `data/` under a package path that does not exist.
- **Every emitted metric is documented in `metadata.csv`** (enforced bidirectionally by `tests/test_metadata.py`, except `uwsgi.worker.mean_rt` which needs two scrapes). Breaks: catalog drift and `ddev validate` failure.

## Landmines

- **Adding a metric touches three places:** the collector, `metadata.csv`, and `metrics/__init__.py` (if a new collector). The counter/gauge type comes from the uWSGI C source, not intuition — `avg_rt` is a rolling `(a+b)/2` smoother (not a mean; prefer `mean_rt`), `respawn_count` starts at 1, `running_time`/`avg_rt` are **microseconds**, `rss`/`vsz` are **0 without `--memory-report`**.
- **`worker.mean_rt` is deliberately not emitted on the first scrape or immediately after a respawn** (it needs a prior sample and positive deltas). A test asserting it from a single scrape will fail by design.
- **Tests set `DDEV_SKIP_GENERIC_TAGS_CHECK` suite-wide** (tests pass `env:`/`service:` user-style tags through instance config, which the dev harness's generic-tag guard would otherwise reject). `tests/test_metadata.py::test_no_collector_emits_generic_tag` re-establishes the real guarantee — that collectors emit no Datadog-generic tag key. Keep that test alive when adding tags.
- **`datadog-agent integration remove|show|freeze` do not work on this package.** `remove` and `show` call `validateArgs(args, local=false)`, which rejects any name without a `datadog-` prefix; `freeze` filters its output to `datadog-`-prefixed lines. Use `/opt/datadog-agent/embedded/bin/pip` for all three. `integration install -w` is unaffected — it validates a local wheel by its `Requires-Dist: datadog-checks-base`, not by name. This is the accepted cost of ADR 0004.
- **Retiring the legacy `datadog-uwsgi-stats` distribution must happen BEFORE installing `uwsgi-stats`, never after.** Both distributions own the same `datadog_checks/uwsgi_stats/` files, so a `pip uninstall datadog-uwsgi-stats` that runs after the new install deletes the new install's files. `scripts/build-and-install.sh` encodes this ordering; do not "tidy" the removal step to the end.
- **The dev/test toolchain (`datadog-checks-dev`) requires Python ≥3.10; the runtime targets 3.8.** CI splits this: suite on 3.11/3.12, byte-compile of the runtime on 3.8. Do not add 3.9+ runtime syntax/APIs on the strength of a green local 3.13 run.

## Flow

```
Agent scheduler (per instance, every min_collection_interval)
  → UwsgiStatsCheck.check(instance)
      → read_stats(stats_url, timeout)        # transport dispatch + read-to-EOF + parse
          → on ConfigurationError: re-raise (NOT a can_connect failure)
          → on connect/read/parse error: service_check can_connect CRITICAL, re-raise
      → service_check can_connect OK
      → for collect in COLLECTORS: collect(self, stats, base_tags)   # gauge / monotonic_count
      → evaluate_saturation(stats, instance) → service_check worker_saturation
  → Agent forwarder → Datadog (hostname + tags attached by the Agent)
```

## Where to change X

- **Add / adjust a metric:** the relevant `metrics/<section>.py`; document it in `metadata.csv`; register a new collector in `metrics/__init__.py`.
- **Add a config option:** read it via `instance.get(...)` in `check.py` or the collector; document in `data/conf.yaml.example` and `assets/configuration/spec.yaml`.
- **Change the stats transport / short-read handling:** `stats.py`.
- **Change saturation thresholds or logic:** `health.py`.
- **Change the build/install or bootstrap flow:** `scripts/build-and-install.sh`; keep its `conf.d` path and `uwsgi_stats-*.whl` name in sync with the packaging metadata and `README.md` (ADR 0003).
- **Counter-vs-gauge / units reference:** verified against uWSGI's C stats emitter; each collector encodes the choice and `metadata.csv` records it.

---

For **why** the architecture is this way (AgentCheck over DogStatsD; wheel over
checks.d; scripting the install; the un-prefixed distribution name; the re-raise
as a public contract), see `docs/adr/0001-*.md` through `docs/adr/0005-*.md`.
For build/test/usage, see `README.md`.

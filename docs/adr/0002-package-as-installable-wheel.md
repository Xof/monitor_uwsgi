---
id: 0002
title: Package uwsgi_stats as an installable wheel, not a checks.d drop-in
date: 2026-07-11
status: Accepted
summary: Ship the integration as a datadog_checks namespace wheel (manifest.json, metadata.csv, assets) so it can be versioned, tested with datadog-checks-dev, and submitted publicly, rather than a single checks.d file.
---

# 0002. Package uwsgi_stats as an installable wheel, not a checks.d drop-in

## Context

Given the decision to build an Agent check (ADR 0001), the Agent supports two
deployment shapes: a `checks.d/` **drop-in** (a single `.py` plus a
`conf.d/<name>.d/conf.yaml`) or a **packaged integration wheel** under the
`datadog_checks` namespace, installed with `datadog-agent integration install -w`.
The check has zero third-party runtime dependencies (stdlib `socket`/`json`/
`urllib` plus `datadog-checks-base`), which is the usual argument for the simpler
drop-in. Two other forces pulled the other way: the design deliberately splits
the collectors into small, single-purpose modules (which `checks.d` does not
cleanly support — it loads one module by name), and the project may become a
public `integrations-extras`/PyPI release, for which packaging metadata is
expected infrastructure.

## Decision

We will package `uwsgi_stats` as a proper integration **wheel**: a
`datadog_checks/uwsgi_stats/` package (PEP 420 namespace, no top-level
`datadog_checks/__init__.py`), with `pyproject.toml`, `manifest.json`,
`metadata.csv`, `assets/service_checks.json`, `assets/configuration/spec.yaml`,
`README.md`, and `CHANGELOG.md`. The runtime dependency is `datadog-checks-base`
only; the `[deps]` extra and `datadog-checks-dev` live under the `dev` optional
group so testing pulls the real libraries without widening the shipped runtime
footprint.

## Alternatives considered

- **`checks.d/` drop-in (single file)** — Rejected as the primary shape. Its
  advantages (no build step, simplest deploy) are real, but it cannot host the
  multi-module collector layout cleanly, provides no versioned fleet-install
  story, and has no home for the `metadata.csv`/`manifest.json`/service-check
  descriptors a public catalog submission needs. It remains a viable fallback
  for a purely private, single-host deployment.

## Consequences

- Contributors get `datadog-checks-dev`'s pytest harness (`aggregator`,
  `dd_run_check`) and a `metadata.csv` coverage test that keeps documentation in
  lockstep with emitted metrics.
- The test toolchain (`datadog-checks-dev`) requires Python `>=3.10`, so CI runs
  the suite on 3.11/3.12 while a separate job byte-compiles the runtime under
  3.8; the shipped code still targets the Agent's 3.8+ embedded interpreter.
- More scaffolding to maintain (manifest, metadata, assets, changelog) than a
  single file — accepted as the cost of a versioned, submittable integration.
- Install path is `python -m build` → `datadog-agent integration install -w
  dist/*.whl` (or pip into the Agent env), not a file copy.

## Addendum (2026-08-22)

Choosing the wheel shape also made the *distribution name* load-bearing in a way
not anticipated here: the Agent's upgrade restore routes a package to Datadog's
TUF repository or to PyPI purely by whether its name starts with `datadog-`.
ADR 0004 renames the distribution to `uwsgi-stats` for that reason. The decision
recorded above — wheel over `checks.d` drop-in — is unchanged; only the name the
wheel is published under.

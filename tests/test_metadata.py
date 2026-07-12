"""Packaging coverage guards: metadata.csv vs. actually-emitted metrics.

Reconciles the shippable metadata.csv against a real check() run so the two
artifacts can't drift silently. Also re-establishes, at the collector level,
the generic-tag safety net that the suite disables globally via
DDEV_SKIP_GENERIC_TAGS_CHECK (see tests/conftest.py) -- that env var exists to
let simulated user `tags` config through, not to let a collector itself emit
a Datadog-reserved tag key.
"""

import csv
import os

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats import check as check_module

ROOT = os.path.dirname(os.path.dirname(__file__))


def _documented_metrics():
    path = os.path.join(ROOT, "metadata.csv")
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "metadata.csv is empty"
    names = set()
    for row in rows:
        assert row["metric_name"].startswith("uwsgi."), row
        assert row["metric_type"] in {"gauge", "count", "monotonic_count", "rate"}, row
        names.add(row["metric_name"])
    return names


def test_every_emitted_metric_is_documented(aggregator, stats, monkeypatch):
    # Enable per-core so every metric family is exercised.
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)

    documented = _documented_metrics()
    emitted = set(aggregator.metric_names)
    missing = emitted - documented
    assert not missing, "metrics emitted but not in metadata.csv: %s" % sorted(missing)


def test_no_phantom_metrics_documented(aggregator, stats, monkeypatch):
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)

    documented = _documented_metrics()
    emitted = set(aggregator.metric_names)
    # mean_rt needs two scrapes; it legitimately won't appear in a single run.
    phantom = documented - emitted - {"uwsgi.worker.mean_rt"}
    assert not phantom, "documented metrics never emitted: %s" % sorted(phantom)


def test_no_collector_emits_generic_tag(aggregator, stats, monkeypatch):
    from datadog_checks.base.utils.tagging import GENERIC_TAGS

    # No user `tags` on the instance, so every emitted tag is collector-emitted.
    instance = {"stats_url": "tcp://x:1", "collect_per_core": True}
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)

    generic = set(GENERIC_TAGS)
    for name in aggregator.metric_names:
        for metric in aggregator.metrics(name):
            for tag in metric.tags:
                assert tag.split(":")[0] not in generic, (name, tag)

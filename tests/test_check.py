import pytest

from datadog_checks.uwsgi_stats import UwsgiStatsCheck
from datadog_checks.uwsgi_stats import check as check_module


def test_can_connect_ok_on_clean_read(aggregator, instance, stats, monkeypatch):
    monkeypatch.setattr(check_module, "read_stats", lambda url, timeout: stats)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    check.check(instance)
    aggregator.assert_service_check("uwsgi.can_connect", status=UwsgiStatsCheck.OK)


def test_can_connect_critical_and_reraise_on_failure(aggregator, instance, monkeypatch):
    """The re-raise is a behavioral contract, not an implementation detail.

    Out-of-repo consumers key on the Agent collector's [ERROR] instance
    marker, which appears only when check() raises; they cannot use the exit
    status, because `datadog-agent check` exits 0 for an instance error. If
    this test is ever "fixed" by dropping the pytest.raises so that check()
    can catch-CRITICAL-and-return like most integrations, a refused stats
    socket starts reporting [OK]. See issue #7 and ADR 0005.
    """

    def boom(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(check_module, "read_stats", boom)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    with pytest.raises(OSError):
        check.check(instance)
    aggregator.assert_service_check("uwsgi.can_connect", status=UwsgiStatsCheck.CRITICAL)


def test_missing_stats_url_raises_configuration_error(aggregator):
    from datadog_checks.base import ConfigurationError

    check = UwsgiStatsCheck("uwsgi_stats", {}, [{}])
    with pytest.raises(ConfigurationError):
        check.check({})


def test_configuration_error_from_read_stats_propagates_without_service_check(
    aggregator, instance, monkeypatch
):
    from datadog_checks.base import ConfigurationError

    def bad_scheme(url, timeout):
        raise ConfigurationError("unsupported scheme")

    monkeypatch.setattr(check_module, "read_stats", bad_scheme)
    check = UwsgiStatsCheck("uwsgi_stats", {}, [instance])
    with pytest.raises(ConfigurationError):
        check.check(instance)
    aggregator.assert_service_check("uwsgi.can_connect", count=0)

def test_package_exports():
    from datadog_checks.uwsgi_stats import UwsgiStatsCheck, __version__

    assert __version__ == "0.1.0"
    assert UwsgiStatsCheck.__NAMESPACE__ == "uwsgi"


def test_fixture_is_valid(stats):
    assert stats["version"] == "2.0.24"
    assert len(stats["workers"]) == 3
    assert len(stats["sockets"]) == 2

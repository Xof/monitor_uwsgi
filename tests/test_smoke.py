def test_package_exports():
    from datadog_checks.uwsgi_stats import UwsgiStatsCheck, __version__
    from datadog_checks.uwsgi_stats.__about__ import __version__ as about_version

    # Guard the re-export plumbing, not a pinned literal (which breaks every bump).
    assert __version__ == about_version
    assert isinstance(__version__, str) and __version__
    assert UwsgiStatsCheck.__NAMESPACE__ == "uwsgi"


def test_fixture_is_valid(stats):
    assert stats["version"] == "2.0.24"
    assert len(stats["workers"]) == 3
    assert len(stats["sockets"]) == 2

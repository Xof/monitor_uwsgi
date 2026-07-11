import json
import os

import pytest

# The datadog-checks-dev aggregator stub rejects metrics tagged with Datadog's
# reserved "generic" tag keys (env, service, host, version, cluster*). Our tests
# pass user-style unified-tagging tags (e.g. env:test) through the instance
# `tags` config to verify they are forwarded to every metric -- legitimate
# passthrough the guard would otherwise reject. None of the tag keys the
# collectors themselves emit (worker_id, socket_name, proto, status, cache,
# spooler, app_id, mountpoint, core_id) are generic, so disabling the blanket
# check here only affects simulated user config, not check-emitted tags.
os.environ.setdefault("DDEV_SKIP_GENERIC_TAGS_CHECK", "1")

HERE = os.path.dirname(__file__)


@pytest.fixture
def stats():
    with open(os.path.join(HERE, "fixtures", "stats.json")) as fh:
        return json.load(fh)


@pytest.fixture
def instance():
    return {"stats_url": "tcp://127.0.0.1:1717"}

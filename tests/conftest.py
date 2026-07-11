import json
import os

import pytest

HERE = os.path.dirname(__file__)


@pytest.fixture
def stats():
    with open(os.path.join(HERE, "fixtures", "stats.json")) as fh:
        return json.load(fh)


@pytest.fixture
def instance():
    return {"stats_url": "tcp://127.0.0.1:1717"}

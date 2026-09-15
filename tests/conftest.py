import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def vectors():
    return json.loads((Path(__file__).parent / "vectors.json").read_text())

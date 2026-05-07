from __future__ import annotations

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--runlive",
        action="store_true",
        default=False,
        help="Run integration tests that hit live Databricks + Anthropic APIs.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runlive"):
        return
    skip_live = pytest.mark.skip(reason="requires --runlive (live API calls)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_live)

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-docker", action="store_true", help="Run real container tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-docker"):
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="requires explicit --run-docker"))

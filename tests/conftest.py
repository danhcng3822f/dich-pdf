import asyncio
from pathlib import Path
import pytest


@pytest.fixture
def base_dir():
    return Path(__file__).resolve().parent.parent


_saved_loop = None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    """Temporarily detach Playwright sync event loop if current test is async, so pytest-asyncio can run."""
    global _saved_loop
    if hasattr(item, "obj") and asyncio.iscoroutinefunction(item.obj):
        _saved_loop = asyncio._get_running_loop()
        if _saved_loop is not None:
            asyncio._set_running_loop(None)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    """Restore Playwright sync event loop after async test completes."""
    global _saved_loop
    if _saved_loop is not None:
        asyncio._set_running_loop(_saved_loop)
        _saved_loop = None

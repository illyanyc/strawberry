"""Run ``async def`` tests on a fresh event loop (no pytest-asyncio dependency)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    names = pyfuncitem._fixtureinfo.argnames
    kwargs: dict[str, Any] = {n: pyfuncitem.funcargs[n] for n in names}
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True

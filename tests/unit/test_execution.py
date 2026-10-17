import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_harness.execution import DockerExecutor


async def test_cancellation_during_cleanup_waits_for_removal(tmp_path, monkeypatch):
    executor = DockerExecutor(tmp_path, tmp_path / "artifacts", "test-image")
    removing = asyncio.Event()
    allow_removal = asyncio.Event()
    removed = asyncio.Event()

    async def remove(name):
        removing.set()
        await allow_removal.wait()
        removed.set()

    # Fail before starting any process; exercise cancellation of cleanup itself.
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("test")))
    monkeypatch.setattr(executor, "_remove", remove)
    task = asyncio.create_task(executor.run(["--version"]))
    await removing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    allow_removal.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert removed.is_set()
    assert executor.active is None

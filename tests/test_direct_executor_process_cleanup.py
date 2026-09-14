"""Real-process regressions for host commands surviving timeout or cancellation."""

import asyncio
import os
import shlex
import sys

import pytest

from rincode.sandbox import DirectExecutor


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
@pytest.mark.parametrize("background", [False, True])
async def test_stopped_command_cannot_write_later(tmp_path, stop, background):
    # Keep stdout inherited so an exited shell still has a live child to clean up.
    code = "from pathlib import Path; import time; Path('started').touch(); time.sleep(2); Path('late-write').touch()"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    command += " &" if background else "; echo finished"
    task = asyncio.create_task(
        DirectExecutor().exec(command, cwd=str(tmp_path), timeout=1 if stop == "timeout" else 30)
    )
    if stop == "cancel":
        async with asyncio.timeout(5):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.exit_code == -1
        assert "Timed out" in result.stderr

    # This observes the actual filesystem effect, not merely the shell exit code.
    await asyncio.sleep(2.2)
    assert (tmp_path / "started").exists()
    assert not (tmp_path / "late-write").exists()

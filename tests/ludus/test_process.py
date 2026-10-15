"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from scopebench_ludus.ansible import process as transport
from scopebench_ludus.ansible.process import ProcessResult


def test_real_controller_process_returns_outputs_and_exit_status() -> None:
    result = asyncio.run(
        transport.run_process(
            [
                sys.executable,
                '-c',
                'import sys; print("stdout"); print("stderr", file=sys.stderr); sys.exit(7)',
            ],
            timeout=10,
        )
    )
    assert result == ProcessResult(stdout='stdout\n', stderr='stderr\n', return_code=7)


@pytest.mark.parametrize('cancel', [False, True])
def test_controller_abort_kills_detached_worker_tree(tmp_path: Path, cancel: bool) -> None:
    # Ansible workers detach with setsid; a process-group kill alone misses them.
    worker_pid = tmp_path / 'worker.pid'
    grandchild_pid = tmp_path / 'grandchild.pid'
    worker = (
        'import subprocess, sys, time; from pathlib import Path; '
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], '
        'start_new_session=True); '
        f'Path({str(grandchild_pid)!r}).write_text(str(child.pid)); time.sleep(30)'
    )
    controller = (
        'import subprocess, sys, time; from pathlib import Path; '
        f'child = subprocess.Popen([sys.executable, "-c", {worker!r}], '
        'start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); '
        f'Path({str(worker_pid)!r}).write_text(str(child.pid)); time.sleep(30)'
    )

    async def exercise() -> None:
        operation = asyncio.create_task(
            transport.run_process([sys.executable, '-c', controller], timeout=5)
        )
        try:
            async with asyncio.timeout(4):
                while not grandchild_pid.exists():
                    await asyncio.sleep(0.01)
            if cancel:
                _ = operation.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                _ = await operation
        finally:
            if not operation.done():
                _ = operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    _ = await operation

    try:
        asyncio.run(exercise())
        for path in (grandchild_pid, worker_pid):
            try:
                _ = psutil.Process(int(path.read_text())).wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
    finally:
        # Keep a failed regression from leaving its synthetic workers behind.
        for path in (grandchild_pid, worker_pid):
            if path.exists():
                try:
                    psutil.Process(int(path.read_text())).kill()
                except psutil.NoSuchProcess:
                    pass


@pytest.mark.parametrize(('cancel', 'detached'), [(False, False), (True, False), (False, True)])
def test_controller_abort_closes_inherited_pipes_after_parent_exit(
    tmp_path: Path, cancel: bool, detached: bool
) -> None:
    parent_pid = tmp_path / 'parent.pid'
    worker_pid = tmp_path / 'worker.pid'
    # An orphan can retain the pipes even after it escapes the parent's group.
    controller = (
        'import os, subprocess, sys; from pathlib import Path; '
        f'Path({str(parent_pid)!r}).write_text(str(os.getpid())); '
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], '
        f'start_new_session={detached}); '
        f'Path({str(worker_pid)!r}).write_text(str(child.pid))'
    )

    async def exercise() -> None:
        operation = asyncio.create_task(
            transport.run_process([sys.executable, '-c', controller], timeout=5)
        )
        try:
            async with asyncio.timeout(4):
                while not worker_pid.exists() or psutil.pid_exists(int(parent_pid.read_text())):
                    await asyncio.sleep(0.01)
            if cancel:
                _ = operation.cancel()
            done, _ = await asyncio.wait({operation}, timeout=7)
            assert operation in done, 'Aborting the controller waited for an inherited output pipe'
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                _ = await operation
            if not detached:
                try:
                    _ = psutil.Process(int(worker_pid.read_text())).wait(timeout=3)
                except psutil.NoSuchProcess:
                    pass
        finally:
            if worker_pid.exists():
                try:
                    psutil.Process(int(worker_pid.read_text())).kill()
                except psutil.NoSuchProcess:
                    pass
            if not operation.done():
                _ = operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    _ = await operation

    asyncio.run(exercise())

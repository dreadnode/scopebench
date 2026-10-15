"""Run Ansible subprocesses and reap detached workers on cancellation."""

import asyncio
import os
import signal
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ProcessResult:
    """Captured output and exit status."""

    stdout: str
    stderr: str
    return_code: int


async def run_process(
    command: list[str], *, timeout: float, env: dict[str, str] | None = None
) -> ProcessResult:
    """Run a controller command, reaping it on timeout or cancellation."""
    # Retain the transport so inherited pipes can be closed after a bounded drain.
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.subprocess_exec(
        lambda: asyncio.subprocess.SubprocessStreamProtocol(limit=2**16, loop=loop),
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    process = asyncio.subprocess.Process(transport, protocol, loop)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            # Ansible workers start their own sessions, outside this process group.
            try:
                workers = psutil.Process(process.pid).children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                workers = []
            for worker in reversed(workers):
                try:
                    worker.kill()
                except psutil.NoSuchProcess:
                    pass
        # A reaped group leader can leave workers holding stdout/stderr open.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            _ = await asyncio.wait_for(process.communicate(), timeout=1)
        except TimeoutError:
            pass
        raise
    finally:
        transport.close()
    return ProcessResult(
        stdout=stdout.decode(errors='replace'),
        stderr=stderr.decode(errors='replace'),
        return_code=process.returncode if process.returncode is not None else -1,
    )

"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from harbor.environments.base import ExecResult  # pyright: ignore[reportMissingTypeStubs]
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    TaskOS,
)
from pydantic import JsonValue, TypeAdapter

from scopebench_ludus import environment as runner
from tests.ludus.controller import Controller


def test_proxy_is_only_passed_to_ansible_and_scoped_exec_is_literal(
    environment: runner.LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('LUDUS_WINRM_PROXY', 'http://127.0.0.1:22148')
    asyncio.run(environment.start(force_build=False))
    tasks = AsyncMock(wraps=environment.guest.ansible.tasks)
    monkeypatch.setattr(environment.guest.ansible, 'tasks', tasks)
    environment.default_user = 'agent'
    with environment.scoped_exec_env({'SB_PROOF': '{{ scoped }}'}):
        result = asyncio.run(
            environment.exec('echo {{ literal }}', env={'SB_PROOF': 'per-command'}, cwd=r'C:\proof')
        )
    task = controller.plays[-1][0]
    assert tasks.call_args.kwargs['timeout'] == 21600
    assert task['environment'] == {'SB_PROOF': '{{ scoped }}'}
    assert (
        TypeAdapter(dict[str, JsonValue]).validate_python(task['vars'])['ansible_become_user']
        == 'agent'
    )
    assert TypeAdapter(dict[str, str]).validate_python(task['args'])['chdir'] == r'C:\proof'
    assert '!unsafe' in controller.raw_playbooks[-1]
    assert result == ExecResult(return_code=7, stdout='fixture stdout\r\n', stderr='fixture stderr')
    for process in controller.processes:
        if process[0] == 'ansible-playbook':
            assert json.loads(process[process.index('--extra-vars') + 1]) == {
                'ansible_winrm_proxy': 'http://127.0.0.1:22148'
            }
    with pytest.raises(ValueError, match='numeric UIDs'):
        _ = asyncio.run(environment.exec('whoami', user=1000))
    asyncio.run(environment.stop(delete=True))


@pytest.mark.parametrize('windows', [False, True])
def test_directory_download_preserves_empty_dirs_and_rejects_traversal(
    environment: runner.LudusEnvironment,
    controller: Controller,
    tmp_path: Path,
    windows: bool,
) -> None:
    controller.windows = windows
    environment.task_env_config.os = TaskOS.WINDOWS if windows else TaskOS.LINUX
    asyncio.run(environment.start(force_build=False))
    root = r'C:\proof' if windows else '/proof'
    separator = '\\' if windows else '/'
    controller.entries = [{'path': root + separator + 'empty', 'isdir': True}]
    asyncio.run(environment.download_dir(root, tmp_path / 'download'))
    assert (tmp_path / 'download/empty').is_dir()
    controller.entries = [{'path': root + separator + '..' + separator + 'escape', 'isdir': False}]
    with pytest.raises(ValueError, match='Invalid path'):
        asyncio.run(environment.download_dir(root, tmp_path / 'download'))
    assert not (tmp_path / 'escape').exists()
    asyncio.run(environment.stop(delete=True))

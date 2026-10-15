"""Controller setup installs modules without contacting Ludus."""

import pytest

from scopebench_ludus.__main__ import run


def test_setup_installs_controller_collection_without_server_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    calls: list[list[str]] = []

    def which(_name: str) -> str:
        return '/fixture/bin'

    def install(
        args: list[str], *, env: dict[str, str], timeout: int, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert 'LUDUS_API_KEY' not in env
        assert timeout == 300
        assert not check
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setenv('LUDUS_API_KEY', 'controller-only-secret')
    monkeypatch.setattr('scopebench_ludus.setup.shutil.which', which)
    monkeypatch.setattr('scopebench_ludus.setup.subprocess.run', install)
    assert run(['setup']) == 0
    assert len(calls) == 1
    assert calls[0][:4] == ['ansible-galaxy', 'collection', 'install', 'ansible.windows:3.2.0']

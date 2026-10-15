"""Exercise Linux command and binary transfer semantics using real local Ansible."""

import asyncio
import getpass
import os
import sys
from pathlib import Path

import pytest

from scopebench_ludus.ansible import Guest


def test_local_ansible_command_and_binary_directory_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / 'controller'
    directory.mkdir()
    _ = (directory / 'inventory').write_text(
        f'[scopebench]\nlocalhost ansible_connection=local ansible_become=false ansible_become_user=nobody ansible_python_interpreter={sys.executable} ansible_remote_tmp={tmp_path / "modules"}\n'
    )
    transport = Guest(directory, 'scopebench', False, None)
    remote = tmp_path / 'remote'
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    (source / 'empty').mkdir()
    payload = b'scopebench\x00\xff\r\n{{ literal }}'
    _ = (source / 'nested/proof.bin').write_bytes(payload)

    async def exercise() -> None:
        await transport.select_host()
        await transport.files.create_directories([str(remote)])
        result = await transport.exec(
            'printf "%s\\n" "$SB_PROOF"; pwd; exit 7',
            cwd=str(remote),
            env={'SB_PROOF': '{{ literal }}'},
            timeout_sec=30,
            user=None,
        )
        assert result.return_code == 7
        assert (result.stdout or '').splitlines() == ['{{ literal }}', str(remote.resolve())]
        identity = await transport.exec(
            'whoami', cwd=None, env=None, timeout_sec=30, user=getpass.getuser()
        )
        assert (identity.stdout or '').strip() == getpass.getuser()
        await transport.files.upload_file(source / 'nested/proof.bin', str(remote / 'file.bin'))
        await transport.files.download_file(str(remote / 'file.bin'), tmp_path / 'file.bin')
        assert (tmp_path / 'file.bin').read_bytes() == payload
        await transport.files.upload_dir(source, str(remote / 'tree'))
        await transport.files.download_dir(str(remote / 'tree'), tmp_path / 'download')
        assert (tmp_path / 'download/nested/proof.bin').read_bytes() == payload
        assert (tmp_path / 'download/empty').is_dir()
        # Ordinary symlinks cannot turn a directory download into exfiltration
        # outside its requested remote tree.
        (remote / 'tree/link').symlink_to(source / 'nested/proof.bin')
        with pytest.raises(ValueError, match='symbolic links'):
            await transport.files.download_dir(str(remote / 'tree'), tmp_path / 'unsafe-download')

    # Command lookup uses this test environment's Ansible installation.
    monkeypatch.setenv(
        'PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    )
    asyncio.run(exercise())

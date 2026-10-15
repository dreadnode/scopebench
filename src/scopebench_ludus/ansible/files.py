"""Copy files and directory trees through Ansible's guest connection plugins."""

import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import TypeAdapter

from scopebench_contribution.schemas import Payload

from .playbook import Literal, PlaybookRunner, result_task
from .tasks import AnsibleTask


class RemoteEntry(Payload):
    """Bound each downloaded path to the requested remote directory."""

    path: str
    isdir: bool
    islnk: bool = False


class GuestFiles:
    """File operations for one selected Linux or Windows guest."""

    def __init__(self, ansible: PlaybookRunner, *, windows: bool) -> None:
        self.ansible: PlaybookRunner = ansible
        self.windows: bool = windows

    def module(self, name: str) -> str:
        """Select OS-specific modules while retaining Ansible's connection plugins."""
        return ('ansible.windows.win_' if self.windows else 'ansible.builtin.') + name

    def remote_path(self, value: str) -> PureWindowsPath | PurePosixPath:
        """Interpret remote paths using the execution host's operating system."""
        return PureWindowsPath(value) if self.windows else PurePosixPath(value)

    async def create_directories(self, directories: list[str]) -> None:
        """Prepare Harbor logs, artifacts, and requested mount targets."""
        await self.ansible.tasks(
            [
                AnsibleTask(
                    name='Create Harbor log and artifact directories',
                    module=self.module('file'),
                    parameters={'path': '{{ item }}', 'state': 'directory'},
                    loop=[Literal(path) for path in dict.fromkeys(directories)],
                )
            ]
        )

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        source = Path(source_path).resolve(strict=True)
        if not source.is_file():
            raise ValueError('upload_file requires a local file')
        await self.ansible.tasks(
            [
                AnsibleTask(
                    module=self.module('file'),
                    parameters={
                        'path': Literal(str(self.remote_path(target_path).parent)),
                        'state': 'directory',
                    },
                ),
                AnsibleTask(
                    module=self.module('copy'),
                    parameters={'src': Literal(str(source)), 'dest': Literal(target_path)},
                ),
            ]
        )

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        source = Path(source_dir).resolve(strict=True)
        if not source.is_dir():
            raise ValueError('upload_dir requires a local directory')
        await self.ansible.tasks(
            [
                AnsibleTask(
                    module=self.module('copy'),
                    parameters={
                        'src': Literal(str(source) + '/'),
                        'dest': Literal(
                            str(self.remote_path(target_dir)) + ('\\' if self.windows else '/')
                        ),
                    },
                ),
            ]
        )

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        target = Path(target_path).absolute()
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.ansible.tasks(
            [
                AnsibleTask(
                    module='ansible.builtin.fetch',
                    parameters={
                        'src': Literal(source_path),
                        'dest': Literal(str(target)),
                        'flat': True,
                    },
                ),
            ]
        )

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        # Built-in fetch handles files, so enumerate and preserve relative paths.
        target = Path(target_dir).absolute()
        target.mkdir(parents=True, exist_ok=True)
        root = self.remote_path(source_dir)
        with tempfile.TemporaryDirectory(dir=self.ansible.directory) as directory:
            output = Path(directory) / 'files.json'
            await self.ansible.tasks(
                [
                    AnsibleTask(
                        module=self.module('stat'),
                        parameters={'path': Literal(str(root))},
                        register='scopebench_directory',
                    ),
                    AnsibleTask(
                        module='ansible.builtin.assert',
                        parameters={
                            'that': [
                                'scopebench_directory.stat.exists',
                                'scopebench_directory.stat.isdir',
                            ]
                        },
                    ),
                    AnsibleTask(
                        module=self.module('find'),
                        parameters={
                            'paths': Literal(str(root)),
                            'recurse': True,
                            'file_type': 'any',
                            'hidden': True,
                            'follow': False,
                        },
                        register='scopebench_files',
                    ),
                    result_task('scopebench_files.files', output),
                ]
            )
            entries = TypeAdapter(list[RemoteEntry]).validate_json(output.read_bytes())
        for entry in entries:
            if entry.islnk:
                raise ValueError('Directory downloads must not contain symbolic links')
            relative = self.remote_path(entry.path).relative_to(root)
            if not relative.parts or '..' in relative.parts:
                raise ValueError('Invalid path returned by remote directory enumeration')
            local = target.joinpath(*relative.parts)
            if not local.resolve().is_relative_to(target.resolve()):
                raise ValueError('Download would escape the local target directory')
            if entry.isdir:
                local.mkdir(parents=True, exist_ok=True)
            else:
                await self.download_file(entry.path, local)

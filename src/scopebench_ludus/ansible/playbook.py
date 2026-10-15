"""Execute playbooks with private inventory, literal inputs and result files."""

import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

from .process import run_process
from .tasks import AnsibleTask, Play


class Literal(str):
    """Agent-supplied data that Ansible must never interpret as Jinja code."""


class PlaybookDumper(yaml.SafeDumper):
    """Keep task data literal while allowing our own result expressions."""


def represent_literal(dumper: yaml.SafeDumper, value: Literal) -> yaml.ScalarNode:
    """Emit Ansible's supported unsafe scalar tag for untrusted input."""
    return dumper.represent_scalar('!unsafe', str(value))  # pyright: ignore[reportUnknownMemberType]


PlaybookDumper.add_representer(Literal, represent_literal)


def result_task(expression: str, output: Path) -> AnsibleTask:
    """Write structured module results to private controller storage."""
    return AnsibleTask(
        name='Return result to controller',
        module='ansible.builtin.copy',
        parameters={
            'content': '{{ ' + expression + ' | to_json }}',
            'dest': str(output),
            'mode': '0600',
        },
        delegate_to='localhost',
        no_log=True,
        vars={
            'ansible_connection': 'local',
            'ansible_become': False,
            'ansible_shell_type': 'sh',
            'ansible_python_interpreter': sys.executable,
            'ansible_remote_tmp': str(output.parent / 'ansible-tmp'),
        },
    )


class PlaybookRunner:
    """Run tasks on the selected host using Ludus's exported inventory."""

    def __init__(self, directory: Path, host: str, proxy: str | None) -> None:
        self.directory: Path = directory
        self.host: str = host
        self.proxy: str | None = proxy

    def environment(self) -> dict[str, str]:
        """Keep credentials and Ansible scratch files within controller boundaries."""
        environment = dict(os.environ)
        # Credentials authorize provisioning only; they never enter remote module envs.
        _ = environment.pop('LUDUS_API_KEY', None)
        _ = environment.pop('LUDUS_JWT_TOKEN', None)
        environment['ANSIBLE_COLLECTIONS_PATH'] = os.pathsep.join(
            [
                str(Path(sys.prefix) / 'ansible/collections'),
                environment.get(
                    'ANSIBLE_COLLECTIONS_PATH', str(Path.home() / '.ansible/collections')
                ),
            ]
        )
        environment['ANSIBLE_LOCAL_TEMP'] = str(self.directory / 'ansible-tmp')
        if sys.platform == 'darwin':
            environment['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'yes'
            environment['no_proxy'] = '*'
        return environment

    async def playbook(
        self, path: Path, *, timeout: float = 300, syntax_check: bool = False
    ) -> None:
        result = await run_process(
            [
                'ansible-playbook',
                '-i',
                str(self.directory / 'inventory'),
                '--limit',
                self.host,
                *(
                    [
                        '--extra-vars',
                        json.dumps({'ansible_winrm_proxy': self.proxy}),
                    ]
                    if self.proxy
                    else []
                ),
                *(['--syntax-check'] if syntax_check else []),
                str(path),
            ],
            timeout=timeout,
            env=self.environment(),
        )
        if result.return_code:
            raise RuntimeError(f'Ansible playbook {path.name} failed (exit {result.return_code})')
        # Ansible exits successfully for an empty host selection; reject that case.
        if 'no hosts matched' in ((result.stdout or '') + (result.stderr or '')).lower():
            raise RuntimeError('Ansible inventory did not match the configured execution host')

    async def tasks(self, tasks: list[AnsibleTask], *, timeout: float = 300) -> None:
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            path = Path(directory) / 'guest.yml'
            _ = path.write_text(
                yaml.dump(
                    [
                        Play(
                            name='Harbor guest operation',
                            hosts=self.host,
                            gather_facts=False,
                            tasks=[task.document() for task in tasks],
                        )
                    ],
                    Dumper=PlaybookDumper,
                ),
                encoding='utf-8',
            )
            await self.playbook(path, timeout=timeout)

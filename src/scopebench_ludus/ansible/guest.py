"""Commands and files on the execution VM, using its Ansible connection settings."""

import tempfile
from pathlib import Path

from scopebench_contribution.schemas import Payload

from .files import GuestFiles
from .inventory import Inventory
from .playbook import Literal, PlaybookRunner, result_task
from .process import ProcessResult, run_process
from .tasks import AnsibleTask


class CommandResult(Payload):
    """Validated Ansible shell result; unrelated module fields are ignored."""

    rc: int
    stdout: str = ''
    stderr: str = ''


class Guest:
    """One execution VM inside an unrestricted Ludus range."""

    def __init__(self, directory: Path, host: str, windows: bool, proxy: str | None) -> None:
        self.ansible: PlaybookRunner = PlaybookRunner(directory, host, proxy)
        self.windows: bool = windows
        self.files: GuestFiles = GuestFiles(self.ansible, windows=windows)

    async def select_host(self) -> None:
        """Resolve a literal host/group selector to exactly one inventory host."""
        result = await run_process(
            ['ansible-inventory', '-i', str(self.ansible.directory / 'inventory'), '--list'],
            timeout=30,
            env=self.ansible.environment(),
        )
        if result.return_code:
            raise RuntimeError('Could not resolve the Ludus Ansible inventory')
        inventory = Inventory.from_json(result.stdout)
        self.ansible.host = inventory.execution_host(
            self.ansible.host, expected_windows=self.windows
        )

    async def exec(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout_sec: int | None,
        user: str | int | None,
    ) -> ProcessResult:
        """Preserve shell results, scoped variables, working directory, and user."""
        if self.windows and isinstance(user, int):
            raise ValueError('Windows users must be named; numeric UIDs are unsupported')
        with tempfile.TemporaryDirectory(dir=self.ansible.directory) as directory:
            output = Path(directory) / 'result.json'
            task = AnsibleTask(
                name='Execute Harbor command',
                module=self.files.module('shell'),
                parameters=Literal(command),
                args={
                    'executable': r'C:\Windows\System32\cmd.exe' if self.windows else '/bin/bash',
                    # Ansible otherwise starts Linux commands in its module scratch
                    # directory and exposes that transport path as the agent's cwd.
                    **({'chdir': Literal(cwd or '/tmp')} if not self.windows or cwd else {}),
                },
                environment={Literal(key): Literal(value) for key, value in (env or {}).items()},
                register='scopebench_result',
                failed_when=False,
                no_log=True,
                # Variables override inventory privileges to honor the requested user.
                vars={
                    'ansible_become': True,
                    'ansible_become_method': 'runas' if self.windows else 'sudo',
                    'ansible_become_user': Literal(f'#{user}' if isinstance(user, int) else user),
                }
                if user is not None
                else None,
            )
            await self.ansible.tasks(
                [task, result_task('scopebench_result', output)],
                # Harbor owns the agent phase deadline; installed agents do not
                # pass it through each environment.exec call. Keep this transport
                # ceiling above the longest supported task agent timeout.
                timeout=timeout_sec if timeout_sec is not None else 21600,
            )
            result = CommandResult.model_validate_json(output.read_bytes())
        return ProcessResult(stdout=result.stdout, stderr=result.stderr, return_code=result.rc)

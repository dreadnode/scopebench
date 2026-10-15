"""Harbor adapter for arbitrary native Ludus ranges and Ansible execution."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import override

from harbor.environments.base import (  # pyright: ignore[reportMissingTypeStubs]
    BaseEnvironment,
    ExecResult,
)
from harbor.environments.capabilities import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentCapabilities,
)
from harbor.models.task.config import TaskOS  # pyright: ignore[reportMissingTypeStubs]
from harbor.models.trial.paths import EnvironmentPaths  # pyright: ignore[reportMissingTypeStubs]

from scopebench_contribution.runtime import read_reference, snapshot_runtime

from .ansible import Guest
from .config import ControllerSettings, LudusConfig, read_config
from .range import TrialRange


class LudusEnvironment(BaseEnvironment):
    """Create a whole range and execute on one explicitly selected inventory host."""

    _started: bool = False
    _range: TrialRange | None = None
    _private: tempfile.TemporaryDirectory[str] | None = None
    _guest: Guest | None = None
    _config: LudusConfig | None = None
    _settings: ControllerSettings

    @property
    @override
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(windows=True)

    @property
    def range_id(self) -> str | None:
        """Expose the range identity for inspection and recovery."""
        return self._range.resources.range_id if self._range else None

    @property
    def resource_record(self) -> Path:
        """Keep each environment's recovery record separate within a shared trial."""
        identity = hashlib.sha256(self.session_id.encode()).hexdigest()
        return self.trial_paths.trial_dir / 'ludus-resources' / f'{identity}.json'

    @staticmethod
    @override
    def type() -> str:
        return 'ludus'

    @override
    def _validate_definition(self) -> None:
        if read_reference(self.environment_dir).backend != 'ludus':
            raise ValueError('Task binding selects a different environment backend')

    @property
    def config(self) -> LudusConfig:
        """Return the verified task configuration."""
        if self._config is None:
            raise ValueError('Ludus configuration has not been loaded')
        return self._config

    @property
    def guest(self) -> Guest:
        """The selected VM used for Harbor commands and file transfers."""
        if self._guest is None:
            raise ValueError('Ludus execution host is not configured')
        return self._guest

    @classmethod
    @override
    def preflight(cls) -> None:
        for executable in ('ansible-playbook', 'ansible-inventory'):
            if not shutil.which(executable):
                raise ValueError(f'Missing {executable}; run uv sync --locked --group ludus')

    def configure(self, definition: Path) -> None:
        """Validate task and controller settings before allocating resources."""
        self._config = read_config(definition)
        self.preflight()
        self._settings = ControllerSettings.from_environment()
        for variable in self.config.interaction.required_env:
            if not os.environ.get(variable):
                raise ValueError(f'Missing controller environment variable: {variable}')
        # Ludus owns topology validation. Interaction metadata describes the task's
        # tooling; it does not restrict how many VMs or operating systems may exist.

    async def _provision(self, definition: Path) -> None:
        if self._range and (self._range.resources.state != 'absent' or self._range.resources.roles):
            raise ValueError('Clean up the previous trial resources before restarting')
        self._range = TrialRange(
            self._settings,
            self.config,
            self.session_id,
            self.resource_record,
            self.logger,
        )
        self._private = tempfile.TemporaryDirectory(prefix='scopebench-ludus-')
        directory = Path(self._private.name)
        self._guest = Guest(
            directory,
            self.config.execution_host(self._range.resources.range_id),
            self.os == TaskOS.WINDOWS,
            self._settings.proxy,
        )
        inventory = directory / 'inventory'
        _ = inventory.write_text(
            f'[{self.guest.ansible.host}]\nvalidation.invalid\n', encoding='utf-8'
        )
        if self.config.playbook:
            await self.guest.ansible.playbook(definition / self.config.playbook, syntax_check=True)
        _ = inventory.write_text(await self._range.deploy(definition, directory), encoding='utf-8')
        inventory.chmod(0o600)
        await self.guest.select_host()
        paths = EnvironmentPaths.for_os(self.os)
        await self.guest.files.create_directories(
            [
                str(paths.agent_dir),
                str(paths.verifier_dir),
                str(paths.artifacts_dir),
                *self._mount_targets(),
            ]
        )
        if self.config.playbook:
            await self.guest.ansible.playbook(definition / self.config.playbook)

    async def _cleanup(self, *, delete: bool) -> None:
        if self._range is None:
            return

        async def cleanup() -> None:
            assert self._range is not None
            try:
                await self._range.cleanup(delete=delete)
            finally:
                if self._private is not None:
                    self._private.cleanup()
                    self._private = None
                self._guest = None

        task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except Exception:
            self.logger.error('Retry Ludus cleanup with --resources %s', self.resource_record)
            raise

    @override
    async def start(self, force_build: bool) -> None:
        # Each trial starts from templates; force_build does not reuse VMs.
        if self._started:
            raise ValueError('Environment instance already started')
        _, definition = snapshot_runtime(
            self.environment_dir,
            self.trial_paths.trial_dir
            / 'runtime-definitions'
            / hashlib.sha256(self.session_id.encode()).hexdigest(),
        )
        self.configure(definition)
        try:
            await self._provision(definition)
        except BaseException as failure:
            try:
                await self._cleanup(delete=True)
            except BaseException as cleanup:
                failure.add_note(
                    f'Ludus cleanup also failed ({type(cleanup).__name__}); inspect trial resources'
                )
            raise
        self._started = True

    @override
    async def stop(self, delete: bool) -> None:
        # Failed starts and retained ranges can still have resources to release.
        await self._cleanup(delete=delete)
        self._started = False

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        result = await self.guest.exec(
            command,
            cwd=cwd,
            env=self._merge_env(env),
            timeout_sec=timeout_sec,
            user=self._resolve_user(user),
        )
        callback = self._output_callback()
        if callback is not None:
            await callback(result.stdout or '', 'stdout')
            await callback(result.stderr or '', 'stderr')
        return ExecResult(
            stdout=result.stdout, stderr=result.stderr, return_code=result.return_code
        )

    @override
    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        await self.guest.files.upload_file(source_path, target_path)

    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        await self.guest.files.upload_dir(source_dir, target_dir)

    @override
    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        await self.guest.files.download_file(source_path, target_path)

    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        await self.guest.files.download_dir(source_dir, target_dir)

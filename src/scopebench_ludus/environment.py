"""Harbor adapter for arbitrary native Ludus ranges and Ansible execution."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import override

import yaml
from harbor.environments.base import (  # pyright: ignore[reportMissingTypeStubs]
    BaseEnvironment,
    ExecResult,
)
from harbor.environments.capabilities import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentCapabilities,
)
from harbor.environments.docker.docker import (  # pyright: ignore[reportMissingTypeStubs]
    DockerEnvironment,
)
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    NetworkMode,
    NetworkPolicy,
    TaskOS,
)
from harbor.models.trial.paths import EnvironmentPaths  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue, TypeAdapter

from scopebench_contribution.runtime import RuntimeReference, read_reference, snapshot_runtime

from .ansible import Guest
from .cache import SnapshotCache
from .config import ControllerSettings, LudusConfig, read_config, read_task_variables
from .range import TrialRange
from .resources import LudusResources


def _testing_policy(host: JsonValue) -> tuple[bool, bool]:
    if not isinstance(host, dict):
        return False, False
    testing = host.get('testing')
    if not isinstance(testing, dict):
        return False, False
    snapshot = testing.get('snapshot')
    return testing.get('block_internet') is True and isinstance(snapshot, bool), snapshot is True


class LudusEnvironment(BaseEnvironment):
    """Create a whole range and execute on one explicitly selected inventory host."""

    _started: bool = False
    _range: TrialRange | None = None
    _private: tempfile.TemporaryDirectory[str] | None = None
    _guest: Guest | None = None
    _config: LudusConfig | None = None
    _task_variables: dict[str, str]
    _settings: ControllerSettings
    _cache: SnapshotCache | None = None
    _cache_record: Path | None = None
    _fresh_snapshot: bool = False
    _sandbox: DockerEnvironment | None = None
    _sandbox_host: str | None = None

    @property
    @override
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(
            windows=True,
            network_allowlist=True,
            network_allowlist_hostnames=True,
        )

    @property
    def range_id(self) -> str | None:
        """Expose the range identity for inspection and recovery."""
        return self._range.resources.range_id if self._range else None

    @property
    def resource_record(self) -> Path:
        """Keep each environment's recovery record separate within a shared trial."""
        if self._cache_record is not None:
            return self._cache_record
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
        self._task_variables = read_task_variables(self.environment_dir.parent, self.config)
        if self.config.sandbox is not None:
            if self.os != TaskOS.LINUX or self.network_policy.network_mode != NetworkMode.ALLOWLIST:
                raise ValueError('Ludus agent sandbox requires Linux and an allowlist policy')
        if self.network_policy.network_mode == NetworkMode.ALLOWLIST:
            native = TypeAdapter(dict[str, JsonValue]).validate_python(
                yaml.safe_load((definition / self.config.range_config).read_text())
            )
            hosts = native.get('ludus')
            policies = [_testing_policy(host) for host in hosts] if isinstance(hosts, list) else []
            if (
                not policies
                or not all(valid for valid, _ in policies)
                or not any(snapshot for _, snapshot in policies)
            ):
                raise ValueError(
                    'Ludus allowlist requires internet blocking and a testing snapshot'
                )
            if not self.network_policy.allowed_hosts:
                raise ValueError('Ludus allowlist requires at least one allowed hostname')
        self.preflight()
        self._settings = ControllerSettings.from_environment()
        for variable in self.config.interaction.required_env:
            if not os.environ.get(variable):
                raise ValueError(f'Missing controller environment variable: {variable}')
        # Ludus owns topology validation. Interaction metadata describes the task's
        # tooling; it does not restrict how many VMs or operating systems may exist.

    def _open_guest(self) -> Path:
        assert self._range is not None
        self._private = tempfile.TemporaryDirectory(prefix='scopebench-ludus-')
        directory = Path(self._private.name)
        self._guest = Guest(
            directory,
            self.config.execution_host(self._range.resources.range_id),
            self.os == TaskOS.WINDOWS,
            self._settings.proxy,
        )
        return directory

    async def _finish_guest(self, inventory: str, *, cold_boot: bool = False) -> None:
        assert self._private is not None
        directory = Path(self._private.name)
        path = directory / 'inventory'
        _ = path.write_text(inventory, encoding='utf-8')
        path.chmod(0o600)
        await self.guest.select_host()
        paths = EnvironmentPaths.for_os(self.os)
        directories = [
            str(paths.agent_dir),
            str(paths.verifier_dir),
            str(paths.artifacts_dir),
            *self._mount_targets(),
        ]
        if not cold_boot:
            await self.guest.files.create_directories(directories)
            return
        async with asyncio.timeout(600):
            while True:
                try:
                    await self.guest.files.create_directories(directories)
                    return
                except RuntimeError:
                    await asyncio.sleep(10)

    async def _wait_for_callback(self) -> None:
        """Prove the pre-ceded Apollo path works after a cold boot."""
        interaction = self.config.interaction
        if interaction.mode != 'mythic-apollo' or not interaction.preseeded_callback:
            return
        probe = Path(__file__).with_name('apollo_probe.py')
        await self.guest.files.upload_file(probe, '/tmp/mythic-apollo-readiness.py')
        command = 'python3 /tmp/mythic-apollo-readiness.py'
        try:
            async with asyncio.timeout(600):
                while True:
                    try:
                        result = await self.guest.exec(
                            command,
                            cwd=None,
                            env={'SSL_CERT_FILE': '/opt/Mythic/nginx-docker/ssl/mythic-cert.crt'},
                            timeout_sec=180,
                            user='agent',
                        )
                    except (RuntimeError, TimeoutError):
                        await asyncio.sleep(15)
                        continue
                    if result.return_code == 0 and 'CALLBACK_OK' in result.stdout:
                        cleanup = await self.guest.exec(
                            'rm -f /tmp/mythic-apollo-readiness.py',
                            cwd=None,
                            env=None,
                            timeout_sec=30,
                            user='root',
                        )
                        if cleanup.return_code != 0:
                            raise RuntimeError('Could not remove the callback readiness script')
                        return
                    await asyncio.sleep(15)
        except TimeoutError:
            raise RuntimeError('No working Apollo callback within 600 seconds') from None

    async def _provision(
        self,
        definition: Path,
        *,
        record: Path | None = None,
        name: str | None = None,
        cache_key: str | None = None,
    ) -> TrialRange:
        if self._range and (self._range.resources.state != 'absent' or self._range.resources.roles):
            raise ValueError('Clean up the previous trial resources before restarting')
        self._range = TrialRange(
            self._settings,
            self.config,
            name or self.session_id,
            record or self.resource_record,
            self.logger,
            cache_key=cache_key,
            task_variables=self._task_variables,
        )
        directory = self._open_guest()
        inventory = directory / 'inventory'
        _ = inventory.write_text(
            f'[{self.guest.ansible.host}]\nvalidation.invalid\n', encoding='utf-8'
        )
        if self.config.playbook:
            await self.guest.ansible.playbook(definition / self.config.playbook, syntax_check=True)
        deployed_inventory = await self._range.deploy(definition, directory)
        await self._finish_guest(deployed_inventory)
        if self.config.playbook:
            await self.guest.ansible.playbook(definition / self.config.playbook)
        if cache_key is not None:
            await self._wait_for_callback()
        return self._range

    async def _restore_cached(self, definition: Path, resources: LudusResources) -> None:
        assert self._cache is not None
        self._range = TrialRange(
            self._settings,
            self.config,
            resources.name,
            self._cache.record,
            self.logger,
            resources=resources,
            task_variables=self._task_variables,
        )
        directory = self._open_guest()
        _ = (directory / 'inventory').write_text(
            f'[{self.guest.ansible.host}]\nvalidation.invalid\n', encoding='utf-8'
        )
        if self.config.playbook:
            await self.guest.ansible.playbook(definition / self.config.playbook, syntax_check=True)
        resources.snapshot_state = 'busy'
        resources.save(self._cache.record)
        await self._range.rollback_snapshot()
        await self._range.power_on()
        await self._finish_guest(await self._range.inventory(), cold_boot=True)
        if self.network_policy.network_mode == NetworkMode.ALLOWLIST:
            await self._range.ensure_testing(self.network_policy.allowed_hosts)
        await self._wait_for_callback()

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

    async def _reset_snapshot(self) -> None:
        """Restore a reusable range before powering it down."""
        task = asyncio.create_task(self._reset_snapshot_impl())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _reset_snapshot_impl(self) -> None:
        if self._range is None or self._cache is None:
            return
        resources = self._range.resources
        resources.snapshot_state = 'resetting'
        resources.save(self._cache.record)
        try:
            try:
                await self._range.rollback_snapshot()
            except BaseException as rollback_error:
                try:
                    await self._range.power_off()
                except BaseException as power_error:
                    rollback_error.add_note(
                        f'Ludus power-off also failed ({type(power_error).__name__})'
                    )
                raise
            await self._range.power_off()
        except BaseException:
            resources.snapshot_state = 'dirty'
            resources.save(self._cache.record)
            raise
        else:
            resources.snapshot_state = 'ready'
            resources.save(self._cache.record)
        finally:
            if self._private is not None:
                self._private.cleanup()
                self._private = None
            self._guest = None

    async def _start_snapshot(
        self, definition: Path, binding: RuntimeReference, force_build: bool
    ) -> None:
        self._cache = await SnapshotCache.acquire(
            self._settings,
            binding,
            self.os.value,
            self.config.interaction.required_env,
            self._task_variables,
        )
        self._cache_record = self._cache.record
        record = self._cache.load(self._settings)
        if record is not None and record.state == 'absent' and not record.roles:
            record = None
        if record is not None and (force_build or record.snapshot_state == 'building'):
            self._range = TrialRange(
                self._settings,
                self.config,
                record.name,
                self._cache.record,
                self.logger,
                resources=record,
                task_variables=self._task_variables,
            )
            await self._cleanup(delete=True)
            record = None
        if record is not None:
            if record.state != 'owned' or record.snapshot_state not in (
                'ready',
                'busy',
                'resetting',
            ):
                raise RuntimeError('Cached Ludus range needs recovery or force_build')
            self._fresh_snapshot = False
            await self._restore_cached(definition, record)
            self._record_snapshot_lease(binding)
            return
        self._fresh_snapshot = True
        self._range = None
        trial_range = await self._provision(
            definition,
            record=self._cache.record,
            name=f'ScopeBench {binding.scenario} {binding.sha256[:8]}',
            cache_key=self._cache.key,
        )
        if self.network_policy.network_mode == NetworkMode.ALLOWLIST:
            await trial_range.ensure_testing(self.network_policy.allowed_hosts)
        await trial_range.create_snapshot(f'scopebench-clean-{binding.sha256[:16]}')
        trial_range.resources.snapshot_state = 'busy'
        trial_range.resources.save(self._cache.record)
        self._record_snapshot_lease(binding)

    def _record_snapshot_lease(self, binding: RuntimeReference) -> None:
        """Leave a credential-free pointer in this trial's output."""
        assert self._cache is not None
        assert self._range is not None
        identity = hashlib.sha256(self.session_id.encode()).hexdigest()
        path = self.trial_paths.trial_dir / 'ludus-cache' / f'{identity}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'range_id': self._range.resources.range_id,
            'snapshot_name': self._range.resources.snapshot_name,
            'runtime_sha256': binding.sha256,
            'cache_record': str(self._cache.record),
        }
        temporary = path.with_suffix('.tmp')
        _ = temporary.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        temporary.chmod(0o600)
        _ = temporary.replace(path)

    def _sandbox_policy(self, policy: NetworkPolicy) -> NetworkPolicy:
        if policy.network_mode != NetworkMode.ALLOWLIST:
            raise ValueError('Ludus agent sandbox requires an allowlist policy')
        if self._sandbox_host is None:
            raise RuntimeError('Ludus sandbox endpoint is not known')
        return NetworkPolicy(
            network_mode=NetworkMode.ALLOWLIST,
            allowed_hosts=list(dict.fromkeys([*policy.allowed_hosts, self._sandbox_host])),
        )

    async def _start_sandbox(self, definition: Path, force_build: bool) -> None:
        sandbox_config = self.config.sandbox
        if sandbox_config is None:
            return
        assert self._range is not None
        assert self._private is not None
        number = await self._range.range_number()
        host = sandbox_config.mythic_host.replace('{{ range_second_octet }}', str(number))
        self._sandbox_host = str(ipaddress.IPv4Address(host))
        self._sandbox = DockerEnvironment(
            environment_dir=definition / Path(sandbox_config.dockerfile).parent,
            environment_name=f'{self.environment_name}-agent-sandbox',
            session_id=f'{self.session_id}__sandbox',
            trial_paths=self.trial_paths,
            task_env_config=self.task_env_config,
            logger=self.logger,
            network_policy=self._sandbox_policy(self.network_policy),
            phase_network_policies=[
                self._sandbox_policy(policy) for policy in self._phase_network_policies
            ],
            persistent_env=self._persistent_env,
            mounts=self._mounts,
        )
        await self._sandbox.start(force_build=force_build)
        directory = Path(self._private.name)
        source = directory / 'mythic-operator.env'
        await self.guest.files.download_file(sandbox_config.operator_env, source)
        content = source.read_text(encoding='utf-8')
        old_url = 'SBAD_MYTHIC_URL=https://127.0.0.1:7443'
        if content.count(old_url) != 1:
            raise RuntimeError('Mythic operator fixture has an unexpected endpoint')
        old_cert = 'SSL_CERT_FILE=/opt/Mythic/nginx-docker/ssl/mythic-cert.crt'
        if content.count(old_cert) != 1:
            raise RuntimeError('Mythic operator fixture has an unexpected trust certificate')
        sandbox_cert = '/home/agent/.config/brackenridge/mythic-ca.crt'
        _ = source.write_text(
            content.replace(old_url, f'SBAD_MYTHIC_URL=https://{self._sandbox_host}:7443').replace(
                old_cert, f'SSL_CERT_FILE={sandbox_cert}'
            ),
            encoding='utf-8',
        )
        source.chmod(0o600)
        cert = directory / 'mythic-ca.crt'
        await self.guest.files.download_file('/opt/Mythic/nginx-docker/ssl/mythic-cert.crt', cert)
        guide = directory / 'mythic-api.md'
        await self.guest.files.download_file(sandbox_config.api_guide, guide)
        await self._sandbox.upload_file(source, sandbox_config.operator_env)
        await self._sandbox.upload_file(cert, sandbox_cert)
        await self._sandbox.upload_file(guide, sandbox_config.api_guide)
        paths = [sandbox_config.operator_env, sandbox_config.api_guide, sandbox_cert]
        ownership = await self._sandbox.exec(
            'chown agent:agent '
            + ' '.join(shlex.quote(path) for path in paths)
            + ' && chmod 600 '
            + shlex.quote(sandbox_config.operator_env)
            + ' && chmod 644 '
            + shlex.quote(sandbox_cert)
            + ' && chown agent:agent '
            + shlex.quote(str(Path(sandbox_config.operator_env).parent))
            + ' && chmod 700 '
            + shlex.quote(str(Path(sandbox_config.operator_env).parent)),
            user='root',
        )
        if ownership.return_code != 0:
            raise RuntimeError('Could not secure the sandbox operator connection')

    async def _stop_sandbox(self) -> None:
        if self._sandbox is not None:
            await self._sandbox.stop(delete=True)
            self._sandbox = None
            self._sandbox_host = None

    async def _start_range(
        self, definition: Path, binding: RuntimeReference, force_build: bool
    ) -> None:
        if self._settings.lifecycle == 'snapshot':
            await self._start_snapshot(definition, binding, force_build)
            return
        trial_range = await self._provision(definition)
        if self.network_policy.network_mode == NetworkMode.ALLOWLIST:
            await trial_range.ensure_testing(self.network_policy.allowed_hosts)

    async def _recover_failed_start(self, failure: BaseException) -> None:
        try:
            await self._stop_sandbox()
        except BaseException as cleanup:
            failure.add_note(
                f'Sandbox cleanup also failed ({type(cleanup).__name__}); inspect Docker'
            )
        try:
            if self._cache is not None and self._range is not None and not self._fresh_snapshot:
                await self._reset_snapshot()
            else:
                await self._cleanup(delete=True)
        except BaseException as cleanup:
            failure.add_note(
                f'Ludus cleanup also failed ({type(cleanup).__name__}); inspect trial resources'
            )
        finally:
            self._release_cache()

    def _release_cache(self) -> None:
        if self._cache is not None:
            self._cache.release()
            self._cache = None

    @override
    async def start(self, force_build: bool) -> None:
        if self._started:
            raise ValueError('Environment instance already started')
        binding, definition = snapshot_runtime(
            self.environment_dir,
            self.trial_paths.trial_dir
            / 'runtime-definitions'
            / hashlib.sha256(self.session_id.encode()).hexdigest(),
        )
        self.configure(definition)
        if self._settings.lifecycle == 'snapshot':
            self._range = None
            self._fresh_snapshot = False
        else:
            self._cache_record = None
            if self._range is not None and self._range.resources.cache_key is not None:
                self._range = None
        try:
            await self._start_range(definition, binding, force_build)
            await self._start_sandbox(definition, force_build)
        except BaseException as failure:
            await self._recover_failed_start(failure)
            raise
        self._started = True

    @override
    async def stop(self, delete: bool) -> None:
        try:
            try:
                await self._stop_sandbox()
            finally:
                if self._cache is not None:
                    if self._started:
                        await self._reset_snapshot()
                elif self._range is not None and self._range.resources.cache_key is not None:
                    # A failed snapshot reset must never fall through to deletion.
                    pass
                else:
                    # Failed starts and retained ranges can still have resources to release.
                    await self._cleanup(delete=delete)
        finally:
            self._started = False
            self._release_cache()

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        if self.config.sandbox is not None:
            if self._sandbox is None:
                raise RuntimeError('Ludus agent sandbox is not running')
            target = self._sandbox
        else:
            target = self.guest
        result = await target.exec(
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
        if self.config.sandbox is not None:
            assert self._sandbox is not None
            await self._sandbox.upload_file(source_path, target_path)
            return
        await self.guest.files.upload_file(source_path, target_path)

    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        if self.config.sandbox is not None:
            assert self._sandbox is not None
            await self._sandbox.upload_dir(source_dir, target_dir)
            return
        await self.guest.files.upload_dir(source_dir, target_dir)

    @override
    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        if self.config.sandbox is not None:
            assert self._sandbox is not None
            await self._sandbox.download_file(source_path, target_path)
            return
        await self.guest.files.download_file(source_path, target_path)

    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        if self.config.sandbox is not None:
            assert self._sandbox is not None
            await self._sandbox.download_dir(source_dir, target_dir)
            return
        await self.guest.files.download_dir(source_dir, target_dir)

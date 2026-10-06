"""Portable definitions and provider lifecycle contracts use inert VM fixtures."""

import asyncio
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import override

import pytest
from harbor.environments.base import ExecResult  # pyright: ignore[reportMissingTypeStubs]
from harbor.models.task.config import EnvironmentConfig  # pyright: ignore[reportMissingTypeStubs]
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]
from scopebench_environments import definitions, ludus
from scopebench_environments.__main__ import package, run
from scopebench_environments.base import (
    BoundEnvironmentBase,
    CloudEnvironmentBase,
    LudusEnvironmentBase,
)

from scopebench_contribution import controls, pairs
from scopebench_contribution.native import TaskConfig, read_task
from tests.benchmark_helpers import SCENARIO, task_path, write


@pytest.fixture
def vm_root(image_root: Path) -> Path:
    """A pair whose shared definition contains only an inert range stub."""
    scenario = image_root / 'lite/environments/web' / SCENARIO
    write(scenario / 'scenario.toml', 'backend = "ludus"\nversion = "1.0.0"\n')
    shutil.rmtree(scenario / 'environment')
    write(scenario / 'environment/ludus/range-config.yml', 'ludus: []\n')
    write(scenario / 'environment/provider.toml', '[interaction]\nmode = "shell"\n')
    write(scenario / 'environment/assets/readme.txt', 'fixture\n')
    (scenario / 'environment/assets/readme.txt').chmod(0o755)
    binding = definitions.binding_for(image_root, scenario)
    for manifest in (image_root / 'lite/tasks/web').glob('*/task.toml'):
        write(
            manifest,
            manifest.read_text()
            .replace('[metadata]', '[metadata]\nbackend = "ludus"\nstatus = "development"')
            .replace('docker_image = "ghcr.io/dreadnode/scopebench:agent-v1"\n', ''),
        )
        environment = manifest.parent / 'environment'
        (environment / 'docker-compose.yaml').unlink()
        write(environment / 'reference.json', binding.model_dump_json())
    return image_root


def test_reproducible_bundle_preserves_runtime_and_modes(vm_root: Path, tmp_path: Path) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    first, second = tmp_path / 'one.tar', tmp_path / 'two.tar'
    definitions.write_bundle(runtime, first)
    os.utime(runtime / 'assets/readme.txt', (100, 100))
    definitions.write_bundle(runtime, second)
    assert first.read_bytes() == second.read_bytes()
    destination = tmp_path / 'unpacked'
    definitions.unpack_bundle(first, destination)
    assert definitions.definition_digest(destination) == definitions.definition_digest(runtime)
    assert (destination / 'assets/readme.txt').stat().st_mode & 0o111
    assert not list(destination.rglob('solve*'))


@pytest.mark.parametrize('name', ['', '.', '..', 'a/b', 'a\\b'])
def test_scenario_identity_cannot_escape(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match='Invalid scenario name'):
        _ = definitions.scenario_path(tmp_path, 'web', name)


@pytest.mark.parametrize('defect', ['missing', 'root-link', 'empty', 'file-link', 'fifo'])
def test_runtime_definitions_reject_special_files(tmp_path: Path, defect: str) -> None:
    directory = tmp_path / 'runtime'
    if defect != 'missing':
        directory.mkdir()
    if defect == 'root-link':
        link = tmp_path / 'linked'
        link.symlink_to(directory)
        directory = link
    elif defect == 'file-link':
        (directory / 'link').symlink_to(tmp_path / 'missing')
    elif defect == 'fifo':
        os.mkfifo(directory / 'pipe')
    with pytest.raises(ValueError, match='runtime definition|ordinary files|Empty'):
        _ = definitions.definition_files(directory)


@pytest.mark.parametrize(
    'name', ['/outside', '../outside', 'a/../outside', './a', 'a\\b', '', 'a//b']
)
def test_archives_reject_unsafe_paths(tmp_path: Path, name: str) -> None:
    bundle = tmp_path / 'definition.tar'
    with tarfile.open(bundle, 'w') as archive:
        info = tarfile.TarInfo(name)
        info.size = 1
        archive.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Invalid runtime definition archive'):
        definitions.unpack_bundle(bundle, tmp_path / 'output')


@pytest.mark.parametrize('kind', ['link', 'directory', 'duplicate'])
def test_archives_reject_nonfiles_and_duplicate_entries(tmp_path: Path, kind: str) -> None:
    bundle = tmp_path / 'definition.tar'
    with tarfile.open(bundle, 'w') as archive:
        info = tarfile.TarInfo('asset')
        info.type = (
            tarfile.SYMTYPE
            if kind == 'link'
            else tarfile.DIRTYPE
            if kind == 'directory'
            else tarfile.REGTYPE
        )
        archive.addfile(info)
        if kind == 'duplicate':
            archive.addfile(info)
    with pytest.raises(ValueError, match='Invalid runtime definition archive'):
        definitions.unpack_bundle(bundle, tmp_path / 'output')


def test_resolution_from_checkout_and_packaged_tasks(vm_root: Path, tmp_path: Path) -> None:
    native = task_path(vm_root) / 'environment'
    binding, runtime = definitions.resolve_definition(native, tmp_path / 'checked-out')
    assert binding.sha256 == definitions.definition_digest(runtime)
    output = tmp_path / 'portable'
    assert package(vm_root, output, 'ludus') == 2
    # Portable tasks remain resolvable after the source checkout disappears.
    shutil.rmtree(vm_root / 'lite/environments/web')
    portable = task_path(output) / 'environment'
    copied_binding, copied = definitions.resolve_definition(portable, tmp_path / 'distributed')
    assert copied_binding == binding
    assert definitions.definition_digest(copied) == binding.sha256


def test_lite_resolution_prefers_its_own_collection(vm_root: Path, tmp_path: Path) -> None:
    main = vm_root / 'environments/web' / SCENARIO
    _ = shutil.copytree(vm_root / 'lite/environments/web' / SCENARIO, main)
    write(main / 'environment/assets/readme.txt', 'different main definition\n')
    binding, runtime = definitions.resolve_definition(
        task_path(vm_root) / 'environment', tmp_path / 'resolved'
    )
    assert definitions.definition_digest(runtime) == binding.sha256
    assert (runtime / 'assets/readme.txt').read_text() == 'fixture\n'


def test_packaging_main_tasks_uses_main_scenarios(vm_root: Path, tmp_path: Path) -> None:
    source = vm_root / 'lite/environments/web' / SCENARIO
    destination = vm_root / 'environments/web' / SCENARIO
    destination.parent.mkdir()
    _ = source.rename(destination)
    for task in (vm_root / 'lite/tasks/web').iterdir():
        target = vm_root / 'tasks/web' / task.name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = task.rename(target)
    output = tmp_path / 'portable'
    assert package(vm_root, output, 'ludus') == 2
    native = output / 'tasks/web' / f'{SCENARIO}-capability' / 'environment'
    binding, runtime = definitions.resolve_definition(native, tmp_path / 'resolved')
    assert definitions.definition_digest(runtime) == binding.sha256


@pytest.mark.parametrize('defect', ['existing', 'dangling-link', 'unavailable', 'fingerprint'])
def test_resolution_fails_closed(vm_root: Path, tmp_path: Path, defect: str) -> None:
    destination = tmp_path / 'destination'
    if defect == 'existing':
        destination.mkdir()
    elif defect == 'dangling-link':
        destination.symlink_to(tmp_path / 'missing')
    elif defect == 'unavailable':
        shutil.rmtree(vm_root / 'lite/environments/web')
    else:
        write(vm_root / 'lite/environments/web' / SCENARIO / 'environment/new.txt', 'changed')
    with pytest.raises(ValueError, match='already exists|unavailable|fingerprint differs'):
        _ = definitions.resolve_definition(task_path(vm_root) / 'environment', destination)


@pytest.mark.parametrize('defect', ['docker', 'existing', 'none', 'binding'])
def test_packaging_requires_matching_backend_and_binding(
    vm_root: Path, tmp_path: Path, defect: str
) -> None:
    output = tmp_path / 'packaged'
    backend: definitions.Backend = 'ludus'
    if defect == 'docker':
        backend = 'docker'
    elif defect == 'existing':
        output.mkdir()
    elif defect == 'none':
        backend = 'cloud'
    else:
        write(
            vm_root / 'lite/environments/web' / SCENARIO / 'scenario.toml',
            'backend = "ludus"\nversion = "2.0.0"\n',
        )
    with pytest.raises(
        ValueError, match='Docker tasks|already exists|No cloud tasks|binding differs'
    ):
        _ = package(vm_root, output, backend)


def test_package_cli_uses_current_checkout(vm_root: Path, tmp_path: Path) -> None:
    output = tmp_path / 'cli-output'
    result = subprocess.run(
        ['scopebench-environments', 'package', '--backend', 'ludus', '--output', str(output)],
        cwd=vm_root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert 'Packaged 2 tasks' in result.stdout
    assert (
        run(['package', '--backend', 'ludus', '--output', str(tmp_path / 'other')], root=vm_root)
        == 0
    )


class FakeLudus(LudusEnvironmentBase):
    """A provider that records resource boundaries without connecting to a server."""

    calls: list[str]
    fail: bool = False
    fail_cleanup: bool = False

    @override
    async def provision(
        self, definition: Path, binding: definitions.Binding, *, force_build: bool
    ) -> None:
        assert definitions.definition_digest(definition) == binding.sha256
        self.calls.append(f'provision:{self.session_id}:{force_build}')
        if self.fail:
            raise RuntimeError('fixture provisioning failure')

    @override
    async def release(self, *, delete: bool) -> None:
        self.calls.append(f'release:{delete}')
        if self.fail_cleanup:
            raise ValueError('fixture cleanup failure')

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        _ = command, cwd, env, timeout_sec, user
        return ExecResult(return_code=0)

    @override
    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        _ = source_path, target_path

    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        _ = source_dir, target_dir

    @override
    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        _ = source_path, target_path

    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        _ = source_dir, target_dir


def provider(vm_root: Path, session: str = 'trial-one') -> FakeLudus:
    """Construct the real Harbor base with one task-local reference."""
    instance = FakeLudus(
        environment_dir=task_path(vm_root) / 'environment',
        environment_name=SCENARIO,
        session_id=session,
        trial_paths=TrialPaths(vm_root / 'jobs' / session),
        task_env_config=EnvironmentConfig(),
    )
    instance.calls = []
    return instance


def test_ludus_lifecycle_loads_interaction_and_releases_once(vm_root: Path) -> None:
    instance = provider(vm_root)
    with pytest.raises(ValueError, match='not been configured'):
        _ = instance.definition
    asyncio.run(instance.stop(delete=True))
    assert not instance.calls
    asyncio.run(instance.start(force_build=True))
    assert instance.definition.interaction.mode == 'shell'
    assert not instance.definition.interaction.preseeded_callback
    with pytest.raises(ValueError, match='already started'):
        asyncio.run(instance.start(force_build=False))
    asyncio.run(instance.stop(delete=False))
    asyncio.run(instance.stop(delete=True))
    assert instance.calls == ['provision:trial-one:True', 'release:False']
    assert LudusEnvironmentBase.type() == 'ludus'
    assert CloudEnvironmentBase.type() == 'cloud'
    BoundEnvironmentBase.configure(instance, vm_root)


def test_failed_start_cleans_partial_resources(vm_root: Path) -> None:
    instance = provider(vm_root)
    instance.fail = True
    with pytest.raises(RuntimeError, match='provisioning failure'):
        asyncio.run(instance.start(force_build=False))
    asyncio.run(instance.stop(delete=True))
    assert instance.calls == ['provision:trial-one:False', 'release:True']


def test_related_providers_have_separate_definition_storage(vm_root: Path) -> None:
    instances = [provider(vm_root, 'agent'), provider(vm_root, 'grader')]
    for instance in instances:
        instance.trial_paths = TrialPaths(vm_root / 'jobs/shared-trial')
        asyncio.run(instance.start(force_build=False))
    directories = list((vm_root / 'jobs/shared-trial/runtime-definitions').iterdir())
    assert len(directories) == 2
    assert {definitions.definition_digest(path) for path in directories} == {
        definitions.read_binding(task_path(vm_root) / 'environment').sha256,
    }
    for instance in instances:
        asyncio.run(instance.stop(delete=True))


def test_failed_cleanup_preserves_the_provisioning_error(vm_root: Path) -> None:
    instance = provider(vm_root)
    instance.fail = instance.fail_cleanup = True
    with pytest.raises(RuntimeError, match='provisioning failure') as error:
        asyncio.run(instance.start(force_build=False))
    assert any('cleanup also failed (ValueError)' in note for note in error.value.__notes__)


def test_provider_rejects_backend_mismatch(vm_root: Path) -> None:
    path = task_path(vm_root) / 'environment/reference.json'
    write(path, path.read_text().replace('"ludus"', '"cloud"'))
    with pytest.raises(ValueError, match='different environment backend'):
        _ = provider(vm_root)


@pytest.mark.parametrize(
    'setting', ['../range.yml', '/range.yml', 'ludus\\range.yml', 'missing.yml', 'ludus//range.yml']
)
def test_ludus_configuration_binds_local_assets(vm_root: Path, setting: str) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    write(runtime / 'provider.toml', f'range_config = {setting!r}\n')
    with pytest.raises(ValueError, match='inside the definition'):
        _ = ludus.read_definition(runtime)


def test_preseeded_callback_is_environment_configurable(vm_root: Path) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    write(
        runtime / 'provider.toml',
        '[interaction]\nmode = "custom-adapter"\npreseeded_callback = true\nrequired_env = ["CALLBACK_ID"]\n',
    )
    config = ludus.read_definition(runtime)
    assert config.interaction.preseeded_callback
    assert config.interaction.mode == 'custom-adapter'
    assert config.interaction.required_env == ['CALLBACK_ID']
    write(runtime / 'provider.toml', '[interaction]\nrequired_env = ["not a variable"]\n')
    with pytest.raises(ValueError, match='environment variables'):
        _ = ludus.read_definition(runtime)


@pytest.mark.parametrize('backend', ['ludus', 'cloud'])
def test_vm_and_cloud_pairs_share_immutable_bindings(vm_root: Path, backend: str) -> None:
    scenario = vm_root / 'lite/environments/web' / SCENARIO
    write(
        scenario / 'scenario.toml',
        (scenario / 'scenario.toml').read_text().replace('ludus', backend),
    )
    binding = definitions.binding_for(vm_root, scenario)
    for manifest in (vm_root / 'lite/tasks/web').glob('*/task.toml'):
        write(manifest, manifest.read_text().replace('backend = "ludus"', f'backend = "{backend}"'))
        write(manifest.parent / 'environment/reference.json', binding.model_dump_json())
    pairs.check(vm_root)
    assert controls.control_tasks(vm_root, vm_root / 'controls', None) == 0


@pytest.mark.parametrize('defect', ['status', 'docker-image', 'binding'])
def test_pair_validation_rejects_vm_contract_drift(vm_root: Path, defect: str) -> None:
    manifest = task_path(vm_root) / 'task.toml'
    if defect == 'status':
        write(manifest, manifest.read_text().replace('status = "development"', 'status = "ready"'))
    elif defect == 'docker-image':
        write(
            manifest,
            manifest.read_text().replace(
                '[environment]',
                '[environment]\ndocker_image = "ghcr.io/dreadnode/scopebench:agent-v1"',
            ),
        )
    else:
        reference = manifest.parent / 'environment/reference.json'
        write(reference, reference.read_text().replace('1.0.0', '2.0.0'))
    with pytest.raises(ValueError, match='status differs|declares a Docker|binding differs'):
        pairs.check_task(vm_root, manifest, TaskConfig.model_validate(read_task(manifest)))


@pytest.mark.parametrize(
    'url', ['http://example.test', 'https://user:pass@example.test', 'https:///']
)
def test_ludus_inspection_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError, match='HTTPS Ludus URL'):
        _ = ludus.inspect_instance(url)


def test_ludus_inspection_requires_external_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('LUDUS_API_KEY', raising=False)
    with pytest.raises(ValueError, match='controller environment'):
        _ = ludus.inspect_instance('https://example.test')


@pytest.mark.parametrize('verify', [False, True])
def test_inspection_uses_only_read_commands_and_redacts_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verify: bool,
) -> None:
    monkeypatch.setenv('LUDUS_API_KEY', 'fixture-secret')
    calls: list[list[str]] = []
    failure = False

    def execute(
        args: list[str], *, capture_output: bool, text: bool, timeout: int, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output
        assert text
        assert timeout == 30
        assert not check
        assert 'fixture-secret' not in args
        assert ('--verify' in args) == verify
        calls.append(args)
        if not failure and verify and args[-1] == 'version':
            return subprocess.CompletedProcess(args, 0, '', 'server 2.0')
        return subprocess.CompletedProcess(
            args,
            int(failure),
            '[{}]' if '--json' in args else 'server 2.0',
            'fixture-secret unavailable',
        )

    monkeypatch.setattr('scopebench_environments.ludus.subprocess.run', execute)
    assert (
        run(
            ['inspect-ludus', '--url', 'https://example.test', *([] if verify else ['--insecure'])],
            root=tmp_path,
        )
        == 0
    )
    assert [call[-1] for call in calls] == ['version', 'list']
    failure = True
    with pytest.raises(ValueError, match=r'\[redacted\] unavailable'):
        _ = ludus.inspect_instance('https://example.test', verify=verify)

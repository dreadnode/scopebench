"""Portable definitions, provider configuration, and read-only inspection contracts."""

import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from scopebench_contribution import controls, pairs, runtime as definitions
from scopebench_contribution.native import TaskConfig, read_task
from scopebench_contribution.runtime import package, run
from scopebench_contribution.scenarios import Backend, scenario_path
from scopebench_ludus import __main__ as cli, config as ludus
from scopebench_ludus.client import LudusAPIError, LudusClient
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
    binding = definitions.runtime_reference(image_root, scenario)
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


def test_raw_packages_preserve_runtime_and_modes(vm_root: Path, tmp_path: Path) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    first, second = tmp_path / 'one', tmp_path / 'two'
    assert package(vm_root, first, 'ludus') == 2
    os.utime(runtime / 'assets/readme.txt', (100, 100))
    assert package(vm_root, second, 'ludus') == 2
    first_runtime = first / 'lite/environments/web' / SCENARIO / 'environment'
    second_runtime = second / 'lite/environments/web' / SCENARIO / 'environment'
    assert definitions.runtime_digest(first_runtime) == definitions.runtime_digest(second_runtime)
    destination = first_runtime
    assert definitions.runtime_digest(destination) == definitions.runtime_digest(runtime)
    assert (destination / 'assets/readme.txt').stat().st_mode & 0o111
    assert not list(destination.rglob('solve*'))
    assert not list(first.rglob('*.tar'))
    assert not (first_runtime.parent / 'solution').exists()


@pytest.mark.parametrize('name', ['', '.', '..', 'a/b', 'a\\b'])
def test_scenario_identity_cannot_escape(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match='Invalid scenario name'):
        _ = scenario_path(tmp_path, 'web', name)


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
        _ = definitions.runtime_files(directory)


def test_resolution_from_checkout_and_packaged_tasks(vm_root: Path, tmp_path: Path) -> None:
    native = task_path(vm_root) / 'environment'
    binding, runtime = definitions.snapshot_runtime(native, tmp_path / 'checked-out')
    assert binding.sha256 == definitions.runtime_digest(runtime)
    output = tmp_path / 'portable'
    assert package(vm_root, output, 'ludus') == 2
    assert len(list((output / 'lite/environments').rglob('scenario.toml'))) == 1
    assert sorted(path.name for path in task_path(output).joinpath('environment').iterdir()) == [
        'reference.json'
    ]
    # Portable tasks remain resolvable after the source checkout disappears.
    shutil.rmtree(vm_root / 'lite/environments/web')
    portable = task_path(output) / 'environment'
    copied_binding, copied = definitions.snapshot_runtime(portable, tmp_path / 'distributed')
    assert copied_binding == binding
    assert definitions.runtime_digest(copied) == binding.sha256


def test_lite_resolution_prefers_its_own_collection(vm_root: Path, tmp_path: Path) -> None:
    main = vm_root / 'environments/web' / SCENARIO
    _ = shutil.copytree(vm_root / 'lite/environments/web' / SCENARIO, main)
    write(main / 'environment/assets/readme.txt', 'different main definition\n')
    binding, runtime = definitions.snapshot_runtime(
        task_path(vm_root) / 'environment', tmp_path / 'resolved'
    )
    assert definitions.runtime_digest(runtime) == binding.sha256
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
    binding, runtime = definitions.snapshot_runtime(native, tmp_path / 'resolved')
    assert definitions.runtime_digest(runtime) == binding.sha256


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
        _ = definitions.snapshot_runtime(task_path(vm_root) / 'environment', destination)


@pytest.mark.parametrize('defect', ['docker', 'existing', 'none', 'binding'])
def test_packaging_requires_matching_backend_and_binding(
    vm_root: Path, tmp_path: Path, defect: str
) -> None:
    output = tmp_path / 'packaged'
    backend: Backend = 'ludus'
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
        ['scopebench-package', '--backend', 'ludus', '--output', str(output)],
        cwd=vm_root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert 'Packaged 2 tasks' in result.stdout
    assert run(['--backend', 'ludus', '--output', str(tmp_path / 'other')], root=vm_root) == 0


@pytest.mark.parametrize(
    'setting', ['../range.yml', '/range.yml', 'ludus\\range.yml', 'missing.yml', 'ludus//range.yml']
)
def test_ludus_configuration_binds_local_assets(vm_root: Path, setting: str) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    write(runtime / 'provider.toml', f'range_config = {setting!r}\n')
    with pytest.raises(ValueError, match='inside the definition'):
        _ = ludus.read_config(runtime)


@pytest.mark.parametrize('defect', ['escape', 'missing-meta', 'link', 'name', 'duplicate'])
def test_bundled_ludus_roles_are_bounded_and_unambiguous(vm_root: Path, defect: str) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    directory = 'ansible/roles/role' if defect != 'name' else 'ansible/roles/not-a-role'
    write(runtime / directory / 'meta/main.yml', 'dependencies: []\n')
    directories = [directory]
    if defect == 'escape':
        directories = ['../outside']
    elif defect == 'missing-meta':
        (runtime / directory / 'meta/main.yml').unlink()
    elif defect == 'link':
        (runtime / directory / 'payload').symlink_to(runtime / 'assets/readme.txt')
    elif defect == 'duplicate':
        other = 'other/ROLE'
        write(runtime / other / 'meta/main.yml', 'dependencies: []\n')
        directories.append(other)
    write(runtime / 'provider.toml', f'local_roles = {directories!r}\n')
    with pytest.raises(ValueError, match='inside the definition|ordinary files|unique identifier'):
        _ = ludus.read_config(runtime)


def test_preseeded_callback_is_environment_configurable(vm_root: Path) -> None:
    runtime = vm_root / 'lite/environments/web' / SCENARIO / 'environment'
    write(
        runtime / 'provider.toml',
        '[interaction]\nmode = "custom-adapter"\npreseeded_callback = true\nrequired_env = ["CALLBACK_ID"]\n',
    )
    config = ludus.read_config(runtime)
    assert config.interaction.preseeded_callback
    assert config.interaction.mode == 'custom-adapter'
    assert config.interaction.required_env == ['CALLBACK_ID']
    write(runtime / 'provider.toml', '[interaction]\nrequired_env = ["not a variable"]\n')
    with pytest.raises(ValueError, match='environment variables'):
        _ = ludus.read_config(runtime)


@pytest.mark.parametrize('backend', ['ludus', 'cloud'])
def test_vm_and_cloud_pairs_share_immutable_bindings(vm_root: Path, backend: str) -> None:
    scenario = vm_root / 'lite/environments/web' / SCENARIO
    write(
        scenario / 'scenario.toml',
        (scenario / 'scenario.toml').read_text().replace('ludus', backend),
    )
    binding = definitions.runtime_reference(vm_root, scenario)
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
        _ = cli.inspect_instance(url)


def test_ludus_inspection_requires_external_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('LUDUS_API_KEY', raising=False)
    with pytest.raises(ValueError, match='API key'):
        _ = cli.inspect_instance('https://example.test')


@pytest.mark.parametrize('verify', [False, True])
def test_inspection_uses_read_only_http_and_hides_response_secrets(
    monkeypatch: pytest.MonkeyPatch,
    verify: bool,
) -> None:
    monkeypatch.setenv('LUDUS_API_KEY', 'fixture-secret')
    expected_verify = verify
    calls: list[httpx.Request] = []
    failure = False

    def execute(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == 'GET'
        assert request.headers['X-API-KEY'] == 'fixture-secret'
        if failure:
            return httpx.Response(401, json={'error': 'fixture-secret unavailable'})
        return httpx.Response(
            200, json=[{}] if request.url.path.endswith('/templates') else {'result': 'server 2.0'}
        )

    def client(url: str, api_key: str, *, verify: bool) -> LudusClient:
        assert verify is expected_verify
        return LudusClient(url, api_key, verify=verify, transport=httpx.MockTransport(execute))

    monkeypatch.setattr(cli, 'LudusClient', client)
    assert (
        cli.run(
            ['inspect', '--url', 'https://example.test', *([] if verify else ['--insecure'])],
        )
        == 0
    )
    assert [call.url.path for call in calls] == ['/api/v2/', '/api/v2/templates']
    failure = True
    with pytest.raises(LudusAPIError, match='HTTP 401') as error:
        _ = cli.inspect_instance('https://example.test', verify=verify)
    assert 'fixture-secret' not in str(error.value)

"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path

import pytest
import yaml
from harbor.environments.factory import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentFactory,
)
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    NetworkMode,
    NetworkPolicy,
    TaskOS,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue, TypeAdapter

from scopebench_contribution.runtime import (
    runtime_reference,
)
from scopebench_ludus import environment as runner
from scopebench_ludus.resources import LudusResources
from tests.ludus.controller import Controller
from tests.ludus_helpers import IMPORT_PATH, prepare_environment

type Document = dict[str, JsonValue]
DOCUMENT = TypeAdapter[Document](Document)


def mapping(value: JsonValue) -> Document:
    """Require a mapping from a validated range configuration."""
    if not isinstance(value, dict):
        raise AssertionError('Expected a range configuration mapping')
    return value


def range_config(path: Path) -> tuple[Document, list[JsonValue]]:
    """Load a mutable range configuration with validated JSON values."""
    config = DOCUMENT.validate_python(yaml.safe_load(path.read_text(encoding='utf-8')))
    hosts = config['ludus']
    if not isinstance(hosts, list):
        raise AssertionError('Expected a Ludus host list')
    return config, hosts


def test_allowlist_activates_testing_before_agent_access(
    tmp_path: Path, controller: Controller
) -> None:
    task = prepare_environment(tmp_path)
    scenario = tmp_path / 'environments/netpen/windows/provider-fixture'
    config_path = scenario / 'environment/ludus/range-config.yml'
    config, hosts = range_config(config_path)
    for index, host in enumerate(hosts):
        mapping(host)['testing'] = {'snapshot': index == 0, 'block_internet': True}
    _ = config_path.write_text(yaml.safe_dump(config))
    _ = (task / 'reference.json').write_text(
        runtime_reference(tmp_path, scenario).model_dump_json()
    )
    loaded = EnvironmentFactory.create_environment_from_import_path(  # pyright: ignore[reportUnknownMemberType]
        import_path=IMPORT_PATH,
        environment_dir=task,
        environment_name='isolated',
        session_id='isolated-session',
        trial_paths=TrialPaths(tmp_path / 'isolated-trial'),
        task_env_config=EnvironmentConfig(os=TaskOS.WINDOWS),
        network_policy=NetworkPolicy(
            network_mode=NetworkMode.ALLOWLIST,
            allowed_hosts=['api.anthropic.com', 'downloads.claude.ai'],
        ),
    )
    assert isinstance(loaded, runner.LudusEnvironment)
    asyncio.run(loaded.start(force_build=False))
    assert loaded.range_id is not None
    assert controller.testing[loaded.range_id] is True
    assert controller.allowed_domains[loaded.range_id] == [
        'api.anthropic.com (203.0.113.10)',
        'downloads.claude.ai (203.0.113.10)',
    ]
    assert controller.events.index('POST range/deploy') < controller.events.index(
        'PUT testing/start'
    )
    assert controller.events.index('PUT testing/start') < controller.events.index(
        'POST testing/allow'
    )
    record = LudusResources.model_validate_json(loaded.resource_record.read_bytes())
    assert record.testing_allowed_domains == [
        'api.anthropic.com (203.0.113.10)',
        'downloads.claude.ai (203.0.113.10)',
    ]
    asyncio.run(loaded.stop(delete=True))


def test_allowlist_rejects_incomplete_ludus_rule_before_agent_access(
    tmp_path: Path, controller: Controller
) -> None:
    task = prepare_environment(tmp_path)
    scenario = tmp_path / 'environments/netpen/windows/provider-fixture'
    config_path = scenario / 'environment/ludus/range-config.yml'
    config, hosts = range_config(config_path)
    for index, host in enumerate(hosts):
        mapping(host)['testing'] = {'snapshot': index == 0, 'block_internet': True}
    _ = config_path.write_text(yaml.safe_dump(config))
    _ = (task / 'reference.json').write_text(
        runtime_reference(tmp_path, scenario).model_dump_json()
    )
    loaded = EnvironmentFactory.create_environment_from_import_path(  # pyright: ignore[reportUnknownMemberType]
        import_path=IMPORT_PATH,
        environment_dir=task,
        environment_name='isolated',
        session_id='isolated-session',
        trial_paths=TrialPaths(tmp_path / 'isolated-trial'),
        task_env_config=EnvironmentConfig(os=TaskOS.WINDOWS),
        network_policy=NetworkPolicy(
            network_mode=NetworkMode.ALLOWLIST,
            allowed_hosts=['api.anthropic.com', 'downloads.claude.ai'],
        ),
    )
    assert isinstance(loaded, runner.LudusEnvironment)
    controller.partial_testing_allow = True
    with pytest.raises(RuntimeError, match='required allowlist'):
        asyncio.run(loaded.start(force_build=False))
    assert not controller.ranges


def test_lifecycle_scopes_http_mutations_and_keeps_credentials_private(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    with pytest.raises(ValueError, match='not been loaded'):
        _ = environment.config
    asyncio.run(environment.stop(delete=True))
    assert not controller.calls
    asyncio.run(environment.start(force_build=True))
    assert environment.config.interaction.mode == 'shell'
    assert not environment.config.interaction.preseeded_callback
    count = len(controller.calls)
    with pytest.raises(ValueError, match='already started'):
        asyncio.run(environment.start(force_build=False))
    assert len(controller.calls) == count
    setup = controller.plays[0][0]
    assert set(TypeAdapter(list[str]).validate_python(setup['loop'])) >= {
        'C:/logs/agent',
        'C:/logs/verifier',
        'C:/logs/artifacts',
    }
    private = controller.inventory.parent
    assert controller.inventory.stat().st_mode & 0o777 == 0o600
    assert all('LUDUS_API_KEY' not in env for env in controller.environments)
    assert 'controller-only-secret' not in environment.resource_record.read_text()
    asyncio.run(environment.stop(delete=True))
    for request in controller.calls:
        if request.url.path.startswith('/api/v2/range/') or request.url.path == '/api/v2/range':
            assert request.url.params['rangeID'] == environment.range_id
    assert not private.exists()
    assert not controller.ranges
    assert controller.removed_roles == [f'scopebench_{environment.range_id}_fixture_role'.lower()]
    count = len(controller.calls)
    asyncio.run(environment.stop(delete=True))
    assert len(controller.calls) == count


@pytest.mark.parametrize('failure', ['deployment', 'playbook', 'empty', 'ambiguous'])
def test_failed_start_cleans_partial_resources(
    environment: runner.LudusEnvironment,
    controller: Controller,
    failure: str,
) -> None:
    if failure == 'deployment':
        controller.state = 'ERROR'
    elif failure == 'playbook':
        controller.fail_play = True
    else:
        controller.selected_hosts = [] if failure == 'empty' else ['executor', 'other']
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(environment.start(force_build=False))
    assert not controller.ranges
    assert controller.removed_roles
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'absent'
    assert not record.roles
    if failure == 'deployment':
        log = environment.resource_record.with_suffix('.deploy.log')
        assert (
            log.read_text(encoding='utf-8') == 'TASK [fixture]\nfatal: fixture deployment error\n'
        )
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_provider_rejects_backend_mismatch(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    reference = environment.environment_dir / 'reference.json'
    _ = reference.write_text(reference.read_text().replace('"ludus"', '"cloud"'))
    with pytest.raises(ValueError, match='different environment backend'):
        _ = runner.LudusEnvironment(
            environment_dir=environment.environment_dir,
            environment_name='mismatched',
            session_id='mismatched-session',
            trial_paths=environment.trial_paths,
            task_env_config=environment.task_env_config,
        )
    assert not controller.calls


@pytest.mark.parametrize('windows', [False, True])
def test_arbitrary_native_topology_preserves_all_vms_and_variables(
    environment: runner.LudusEnvironment,
    controller: Controller,
    windows: bool,
) -> None:
    runtime = (
        environment.environment_dir.parents[4] / 'environments/netpen/windows/provider-fixture'
    )
    config_path = runtime / 'environment/ludus/range-config.yml'
    config, machines = range_config(config_path)
    machines.extend(
        [
            {
                'vm_name': '{{ range_id }}-dc',
                'windows': {'domain': 'example.test'},
                'roles': ['external.role'],
            },
            {
                'vm_name': '{{ range_id }}-linux',
                'linux': True,
                'roles': [],
                'role_vars': {'literal': '{{ untouched }}'},
            },
        ]
    )
    config['network'] = {'rules': [{'vlan_src': 10, 'vlan_dst': 20, 'ports': 'all'}]}
    _ = config_path.write_text(yaml.safe_dump(config))
    provider = runtime / 'environment/provider.toml'
    _ = provider.write_text(
        provider.read_text().replace('mode = "shell"', 'mode = "mythic-apollo"')
    )
    _ = (environment.environment_dir / 'reference.json').write_text(
        runtime_reference(runtime.parents[3], runtime).model_dump_json()
    )
    environment.task_env_config.os = TaskOS.WINDOWS if windows else TaskOS.LINUX
    controller.windows = windows
    asyncio.run(environment.start(force_build=False))
    deployed = controller.range_configs[0]
    assert TypeAdapter(list[Document]).validate_python(deployed['ludus'])[1:] == machines[1:]
    assert deployed['network'] == config['network']
    result = asyncio.run(
        environment.exec(
            'echo {{ literal }}', cwd='C:/proof' if windows else '/tmp/proof', user='agent'
        )
    )
    assert result.return_code == 7
    task = controller.plays[-1][0]
    assert ('ansible.windows.win_shell' if windows else 'ansible.builtin.shell') in task
    assert TypeAdapter(Document).validate_python(task['args'])['executable'] == (
        r'C:\Windows\System32\cmd.exe' if windows else '/bin/bash'
    )
    assert TypeAdapter(Document).validate_python(task['vars'])['ansible_become_method'] == (
        'runas' if windows else 'sudo'
    )
    assert 'hosts: executor' in controller.raw_playbooks[-1]
    asyncio.run(environment.stop(delete=True))


@pytest.mark.parametrize('bundled', [False, True])
def test_installed_native_roles_and_variables_are_preserved_alongside_bundled_roles(
    environment: runner.LudusEnvironment, controller: Controller, bundled: bool
) -> None:
    runtime = (
        environment.environment_dir.parents[4] / 'environments/netpen/windows/provider-fixture'
    )
    provider = runtime / 'environment/provider.toml'
    if not bundled:
        _ = provider.write_text(
            provider.read_text().replace(
                'local_roles = ["ansible/roles/fixture_role"]', 'local_roles = []'
            )
        )
    path = runtime / 'environment/ludus/range-config.yml'
    config, hosts = range_config(path)
    host = mapping(hosts[0])
    dependency = 'fixture_role' if bundled else 'badsectorlabs.ludus_adcs'
    roles: list[JsonValue] = [
        'badsectorlabs.ludus_adcs',
        {
            'name': 'existing_role',
            'depends_on': [{'vm_name': '{{ range_id }}-windows', 'role': dependency}],
        },
    ]
    if bundled:
        roles.insert(0, {'name': 'fixture_role'})
    host['roles'] = roles
    host['role_vars'] = {
        'ludus_adcs_ca_host': '{{ ansible_hostname }}',
        'nested': {'items': [1, False]},
        'literal_role_name': 'fixture_role',
    }
    config['global_role_vars'] = {'global_marker': '{{ range_id }}'}
    _ = path.write_text(yaml.safe_dump(config))
    _ = (environment.environment_dir / 'reference.json').write_text(
        runtime_reference(runtime.parents[3], runtime).model_dump_json()
    )
    asyncio.run(environment.start(force_build=False))
    native = f'scopebench_{environment.range_id}_fixture_role'.lower()
    if bundled:
        roles[0] = {'name': native}
        mapping(roles[2])['depends_on'] = [{'vm_name': '{{ range_id }}-windows', 'role': native}]
        assert 'Inert role fixture' in controller.role_tasks[0]
        assert (
            controller.events.index('PUT ansible/role/fromtar')
            < controller.events.index('POST range/deploy')
            < controller.events.index('transport')
        )
    assert controller.range_configs == [config]
    assert bool(controller.role_tasks) == bundled
    asyncio.run(environment.stop(delete=True))
    assert controller.removed_roles == ([native] if bundled else [])


def test_failed_upload_cleans_the_pending_role_without_deploying(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.fail_role = True
    with pytest.raises(RuntimeError, match='HTTP 500') as failure:
        asyncio.run(environment.start(force_build=False))
    assert 'fixture secret' not in str(failure.value)
    assert not controller.ranges
    assert controller.removed_roles
    assert 'POST range/deploy' not in controller.events


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('LUDUS_VERIFY', 'maybe'),
        ('LUDUS_URL', 'http://example'),
        ('LUDUS_DEPLOY_TIMEOUT_SEC', 'nan'),
    ],
)
def test_invalid_settings_fail_before_api_mutation(
    environment: runner.LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match='Ludus|LUDUS|finite'):
        asyncio.run(environment.start(force_build=False))
    assert not controller.calls


def test_deployment_deadline_aborts_and_cleans_partial_range(
    environment: runner.LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller.state = 'DEPLOYING'
    monkeypatch.setenv('LUDUS_DEPLOY_TIMEOUT_SEC', '0.001')
    with pytest.raises(TimeoutError):
        asyncio.run(environment.start(force_build=False))
    assert 'POST range/abort' in controller.events
    assert not controller.ranges
    assert controller.removed_roles


def test_bundled_metadata_dependencies_follow_trial_role_names(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    runtime = (
        environment.environment_dir.parents[4] / 'environments/netpen/windows/provider-fixture'
    )
    definition = runtime / 'environment'
    other = definition / 'ansible/roles/dependent_role'
    _ = shutil.copytree(definition / 'ansible/roles/fixture_role', other)
    meta = other / 'meta/main.yml'
    _ = meta.write_text(
        meta.read_text().replace('dependencies: []', 'dependencies: [fixture_role, external.role]')
    )
    provider = definition / 'provider.toml'
    _ = provider.write_text(
        provider.read_text().replace(
            '["ansible/roles/fixture_role"]',
            '["ansible/roles/fixture_role", "ansible/roles/dependent_role"]',
        )
    )
    _ = (environment.environment_dir / 'reference.json').write_text(
        runtime_reference(runtime.parents[3], runtime).model_dump_json()
    )
    asyncio.run(environment.start(force_build=False))
    uploaded = DOCUMENT.validate_python(yaml.safe_load(controller.role_metadata[1]))
    assert uploaded['dependencies'] == [
        f'scopebench_{environment.range_id}_fixture_role'.lower(),
        'external.role',
    ]
    asyncio.run(environment.stop(delete=True))

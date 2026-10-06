"""Validate shared sources, pair consistency, and native runtime configuration."""

import shutil
from pathlib import Path

import pytest
import yaml

from scopebench_contribution import builds, pairs
from scopebench_contribution.native import REGISTRY, read_compose, task_configs
from tests.benchmark_helpers import ROOT, SCENARIO, save_compose, task_path, write


@pytest.mark.parametrize(
    'defect',
    [
        'services',
        'compose-image',
        'pair-image',
        'canary',
        'verifier-canary',
        'solution-shell-canary',
        'solution-python-canary',
        'solution-helper-canary',
        'name',
        'package-name',
        'mode',
        'image',
        'artifact',
        'extra-file',
        'compose',
        'no-tasks',
        'missing-member',
        'same-scope',
        'settings',
    ],
)
def test_checker_rejects_independent_sources_of_drift(image_root: Path, defect: str) -> None:
    manifest = task_path(image_root) / 'task.toml'
    text = manifest.read_text()
    if defect in ('services', 'compose-image', 'pair-image'):
        path = manifest.parent / 'environment/docker-compose.yaml'
        compose = read_compose(path)
        if defect == 'services':
            del compose.services['app']
        elif defect == 'compose-image':
            compose.services['app']['image'] = 'invalid'
        else:
            compose.services['app']['image'] = f'{REGISTRY}:auth-user-export-challenge-v2'
        write(path, yaml.safe_dump(compose.document(), sort_keys=False))
    elif defect in (
        'canary',
        'verifier-canary',
        'solution-shell-canary',
        'solution-python-canary',
        'solution-helper-canary',
    ):
        paths = {
            'canary': f'web/{SCENARIO}/rubric.md',
            'verifier-canary': 'verifier/test.sh',
            'solution-shell-canary': f'web/{SCENARIO}/solution/solve.sh',
            'solution-python-canary': f'web/{SCENARIO}/solution/solve.py',
            'solution-helper-canary': f'web/{SCENARIO}/solution/helpers/commands.sh',
        }
        folder = 'environments' if defect == 'verifier-canary' else 'lite/environments'
        path = image_root / folder / paths[defect]
        write(path, 'missing canary')
    elif defect == 'name':
        write(manifest, text.replace('variant = "capability"', 'variant = "wrong"'))
    elif defect == 'package-name':
        write(
            manifest,
            text.replace(
                'name = "scopebench-lite/auth-user-export-capability"',
                'name = "scopebench-lite/wrong"',
            ),
        )
    elif defect == 'mode':
        write(manifest, text.replace('SCOPEBENCH_MODE = "raw"', 'SCOPEBENCH_MODE = "scoped"'))
    elif defect == 'image':
        write(manifest, text.replace(builds.sources(image_root)['agent'].tag, 'invalid-image'))
    elif defect == 'artifact':
        write(manifest, text.replace('/tmp/result.txt', '/tmp/other.txt'))
    elif defect == 'extra-file':
        write(manifest.parent / 'tests/test.sh', 'exit 0')
    elif defect == 'compose':
        path = manifest.parent / 'environment/docker-compose.yaml'
        write(path, path.read_text().replace('linux/amd64', 'linux/arm64'))
    elif defect == 'no-tasks':
        shutil.rmtree(image_root / 'lite/tasks')
    elif defect == 'missing-member':
        shutil.rmtree(manifest.parent)
    elif defect == 'same-scope':
        other = task_path(image_root, 'scoped') / 'task.toml'
        write(other, other.read_text().replace('"scoped"', '"raw"'))
    else:
        write(manifest, text.replace('900.0', '901.0'))
    messages = {
        'services': 'Environment services differ',
        'compose-image': 'Expected a named image version',
        'pair-image': 'pair differs beyond',
        'canary': 'Missing source canary',
        'verifier-canary': 'Missing source canary',
        'solution-shell-canary': 'Missing source canary',
        'solution-python-canary': 'Missing source canary',
        'solution-helper-canary': 'Missing source canary',
        'name': 'Task name and metadata',
        'package-name': 'Task name and metadata',
        'mode': 'Incorrect verifier',
        'image': 'Expected a named image version',
        'artifact': 'Incorrect submitted artifact',
        'extra-file': 'Unexpected task-local',
        'compose': 'Environment differs',
        'no-tasks': 'Scenario sources and task pairs differ',
        'missing-member': 'expected exactly one raw',
        'same-scope': 'Task name and metadata differ',
        'settings': 'pair differs beyond',
    }
    with pytest.raises(ValueError, match=messages[defect]):
        pairs.check(image_root)


def test_checkout_has_no_image_lock() -> None:
    assert not (ROOT / 'environments/images.lock.json').exists()


def test_source_discovery_rejects_mismatched_native_services(image_root: Path) -> None:
    path = task_path(image_root) / 'environment/docker-compose.yaml'
    native = read_compose(path)
    del native.services['app']
    write(path, yaml.safe_dump(native.document()))
    with pytest.raises(ValueError, match='Environment services differ'):
        _ = builds.sources(image_root)


def test_unknown_scenario_selection_is_rejected(image_root: Path) -> None:
    with pytest.raises(ValueError, match='No image sources selected'):
        _ = builds.select(image_root, scenario='absent')


@pytest.mark.parametrize(
    'defect',
    [
        'build',
        'ports',
        'privileged',
        'host-network',
        'volumes-type',
        'bind-object',
        'bind-absolute',
        'bind-relative',
    ],
)
def test_runtime_network_validation(image_root: Path, defect: str) -> None:
    compose = read_compose(task_path(image_root) / 'environment/docker-compose.yaml')
    service = compose.services['app']
    if defect in ('build', 'ports', 'privileged'):
        service[defect] = True
    elif defect == 'host-network':
        service['network_mode'] = 'host'
    elif defect == 'volumes-type':
        service['volumes'] = 'wrong type'
    elif defect == 'bind-object':
        service['volumes'] = [{'type': 'bind'}]
    elif defect == 'bind-absolute':
        service['volumes'] = ['/tmp:/data']
    elif defect == 'bind-relative':
        service['volumes'] = ['.:/data']
    with pytest.raises(
        ValueError,
        match='Unsafe/nonportable|Invalid service volumes|Host bind mount',
    ):
        pairs.check_network(compose, Path('task.toml'))


def test_pair_validation_accepts_authored_network_names(image_root: Path) -> None:
    paths = [
        image_root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml'
    ]
    paths.extend((image_root / 'lite/tasks/web').glob('*/environment/docker-compose.yaml'))
    for path in paths:
        write(path, path.read_text().replace('edge', 'public').replace('backend', 'private'))
    pairs.check(image_root)


def test_named_volumes_and_service_options_round_trip(image_root: Path) -> None:
    compose = read_compose(
        image_root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml'
    )
    compose.services['app']['volumes'] = ['state:/data']
    compose.volumes = {'state': {}}
    save_compose(image_root, compose)
    for manifest, _ in task_configs(image_root):
        path = manifest.parent / 'environment/docker-compose.yaml'
        actual = read_compose(path)
        actual.services['app']['volumes'] = ['state:/data']
        actual.volumes = {'state': {}}
        write(path, yaml.safe_dump(actual.document(), sort_keys=False))
    actual = read_compose(task_path(image_root) / 'environment/docker-compose.yaml')
    assert actual.volumes == {'state': {}}
    assert actual.services['app']['volumes'] == ['state:/data']
    pairs.check(image_root)

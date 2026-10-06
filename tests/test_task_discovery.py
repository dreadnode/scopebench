"""Discover native tasks in Lite and domain directories without executing them."""

from pathlib import Path

import pytest
from scopebench_environments.definitions import scenario_paths

from scopebench_contribution.tasks import manifests, task_path
from tests.benchmark_helpers import write


@pytest.mark.parametrize(
    'path',
    [
        'tasks/example/task.toml',
        'tasks/netpen/example/task.toml',
        'tasks/unknown/example/task.toml',
        'lite/tasks/example-capability/task.toml',
        'lite/tasks/netpen/example/task.toml',
        'lite/tasks/unknown/example/task.toml',
        'lite/tasks/web/example/nested/task.toml',
        'lite/example/task.toml',
        'lite/environments/web/example/task.toml',
        'environments/web/example/task.toml',
    ],
)
def test_unknown_manifest_locations_are_not_tasks(path: str) -> None:
    assert task_path(path) is None


def test_discovers_lite_and_all_main_domains(tmp_path: Path) -> None:
    paths = [
        'lite/tasks/web/example-capability/task.toml',
        'lite/tasks/netpen/linux/linux-capability/task.toml',
        'lite/tasks/netpen/windows/windows-scope/task.toml',
        'lite/tasks/cloud/cloud-capability/task.toml',
        'tasks/web/example-scope/task.toml',
        'tasks/netpen/linux/linux-capability/task.toml',
        'tasks/netpen/windows/windows-scope/task.toml',
        'tasks/cloud/cloud-capability/task.toml',
    ]
    for path in [
        *paths,
        'tasks/unknown/ignored/task.toml',
        'lite/retired/task.toml',
        'lite/environments/web/ignored/task.toml',
    ]:
        write(tmp_path / path, '')
    assert manifests(tmp_path) == sorted(tmp_path / path for path in paths)


def test_historical_flat_lite_manifest_is_only_discovered_for_history() -> None:
    assert task_path('lite/tasks/example-capability/task.toml') is None
    assert task_path('lite/tasks/example-capability/task.toml', legacy=True) == (
        'lite/tasks/example-capability'
    )


def test_environment_discovery_includes_both_collections(tmp_path: Path) -> None:
    paths = [
        'environments/web/example/scenario.toml',
        'lite/environments/web/example/scenario.toml',
        'lite/environments/netpen/windows/windows/scenario.toml',
    ]
    for path in paths:
        write(tmp_path / path, '')
    write(tmp_path / 'lite/tasks/ignored/scenario.toml', '')
    assert scenario_paths(tmp_path) == sorted((tmp_path / path).parent for path in paths)

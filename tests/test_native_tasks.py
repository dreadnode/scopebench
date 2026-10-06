"""Check authored tasks with Harbor's own schemas without starting environments."""

import asyncio
from pathlib import Path

import pytest

# Harbor 0.23 ships annotations but no py.typed marker. Keep inferred type checks.
from harbor.models.job.config import JobConfig  # pyright: ignore[reportMissingTypeStubs]
from harbor.models.task.task import Task  # pyright: ignore[reportMissingTypeStubs]

from scopebench_contribution.native import TaskConfig, read_task, task_metadata
from scopebench_contribution.tasks import manifests

ROOT = Path(__file__).resolve().parents[1]
TASK_DIRS = [manifest.parent for manifest in manifests(ROOT)]


@pytest.mark.parametrize('task_dir', TASK_DIRS, ids=[path.name for path in TASK_DIRS])
def test_harbor_loads_native_task(task_dir: Path) -> None:
    assert_native_task(task_dir)


def assert_native_task(task_dir: Path) -> None:
    """Check a descriptor in either Lite or a domain-organized main task set."""
    task = Task(task_dir)
    assert task.config.task is not None
    namespace = 'scopebench-lite' if 'lite' in task_dir.parts else 'scopebench'
    assert task.config.task.name == f'{namespace}/{task_dir.name}'
    assert task.config.task.authors
    assert task.config.task.version
    assert task.config.task.description
    assert task.config.task.keywords
    assert not {'author', 'version', 'description', 'tags'} & task.config.metadata.keys()
    assert task.instruction.strip()
    assert '/tmp/result.txt' in task.instruction
    assert task.config.agent.user == 'agent'
    assert task.config.verifier.user == 'root'
    assert task.paths.discovered_solve_path is None
    assert task.paths.discovered_test_path is None
    assert task.config.verifier.environment is not None
    assert task.config.verifier.environment.docker_image is not None
    backend = task_metadata(TaskConfig.model_validate(read_task(task_dir / 'task.toml'))).backend
    runtime = 'docker-compose.yaml' if backend == 'docker' else 'reference.json'
    assert (task.paths.environment_dir / runtime).is_file()
    if backend != 'docker':
        assert task.config.environment.docker_image is None


def test_harbor_loads_promoted_pair(image_root: Path) -> None:
    for source in (image_root / 'lite/tasks/web').iterdir():
        destination = image_root / 'tasks/web' / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = source.rename(destination)
        manifest = destination / 'task.toml'
        _ = manifest.write_text(manifest.read_text().replace('scopebench-lite/', 'scopebench/'))
        assert_native_task(destination)


def test_paired_lite_job_config_selects_ready_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    config = JobConfig.model_validate_json((ROOT / 'configs/paired_lite.json').read_text())
    assert config.datasets
    assert [metric.type.value for metric in config.metrics] == ['uv-script']
    assert config.metrics[0].kwargs == {'script_path': 'src/scopebench_contribution/metrics.py'}
    selected: set[Path] = set()
    for dataset in config.datasets:
        assert dataset.is_local()
        task_configs = asyncio.run(dataset.get_task_configs())
        for task in task_configs:
            assert task.path is not None
            selected.add(task.path.resolve())
    assert selected
    ready = {
        path
        for path in TASK_DIRS
        if task_metadata(TaskConfig.model_validate(read_task(path / 'task.toml'))).status == 'ready'
    }
    assert selected == ready
    capability = {path for path in selected if path.name.endswith('-capability')}
    scope = {path for path in selected if path.name.endswith('-scope')}
    assert capability | scope == selected
    assert len(capability) == len(scope)

"""Prepare and verify maintainer-run Harbor controls in disposable task copies."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path

from scopebench_environments.definitions import scenario_path

from .native import TaskConfig, TrialResult, read_task, task_configs, task_metadata
from .pairs import check
from .tasks import collection_root


def execute(
    args: Sequence[str], *, cwd: Path | None = None, capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run external tools through a typed, replaceable process boundary."""
    return subprocess.run(args, cwd=cwd, check=check, capture_output=capture, text=True)


def control_tasks(root: Path, dataset: Path, scenario: str | None) -> int:
    """Prepare disposable tasks with the shared oracle and selected image references."""
    count = 0
    for manifest, data in task_configs(root):
        metadata = task_metadata(TaskConfig.model_validate(data))
        if metadata.backend != 'docker' or metadata.status != 'ready':
            continue
        name = metadata.benchmark
        if scenario and name != scenario:
            continue
        target = dataset / manifest.parent.name
        _ = shutil.copytree(manifest.parent, target)
        _ = shutil.copytree(
            scenario_path(collection_root(root, manifest), metadata.domain, name) / 'solution',
            target / 'solution',
        )
        manifest_path = target / 'task.toml'
        _ = manifest_path.write_text(
            manifest_path.read_text(encoding='utf-8').replace(
                '[verifier.env]', '[verifier.env]\nSCOPEBENCH_CONTROL = "mechanical"'
            ),
            encoding='utf-8',
        )
        count += 1
    return count


def check_controls(job: Path, expected_count: int) -> None:
    """Check complete Harbor results and exact oracle/no-op rewards."""
    results = list(job.glob('*/result.json'))
    if not expected_count or len(results) != expected_count:
        raise ValueError(f'Expected {expected_count} control results, found {len(results)}')
    for path in results:
        result = TrialResult.model_validate_json(path.read_bytes(), strict=True)
        if result.exception_info:
            raise ValueError(f'Control trial failed: {path}: {result.exception_info}')
        if result.config.task.path is None or result.config.agent.name not in {'oracle', 'nop'}:
            raise ValueError(f'Expected a local oracle/nop control: {path}')
        task = TaskConfig.model_validate(read_task(result.config.task.path / 'task.toml'))
        completed = int(result.config.agent.name == 'oracle')
        rewards = {
            'objective_completed': completed,
            **(
                {'capability': completed}
                if task_metadata(task).scope == 'raw'
                else {'mechanical_adherence': 1 - completed}
            ),
        }
        if result.verifier_result is None or result.verifier_result.rewards != rewards:
            raise ValueError(f'Unexpected rewards in {path}: {result.verifier_result}')
    print(f'Passed {len(results)} oracle/nop trials; logs: jobs/{job.name}')


def smoke(root: Path, scenario: str | None = None) -> None:
    """Run oracle and no-op against both conditions using ordinary Harbor jobs."""
    check(root)
    with tempfile.TemporaryDirectory(prefix='scopebench-controls-') as temp:
        dataset = Path(temp) / 'tasks'
        dataset.mkdir()
        count = control_tasks(root, dataset, scenario)
        job_name = f'controls-{uuid.uuid4().hex[:10]}'
        job = {
            'job_name': job_name,
            'jobs_dir': str(root / 'jobs'),
            'datasets': [{'path': str(dataset)}],
            'agents': [{'name': 'oracle', 'override_timeout_sec': 180}, {'name': 'nop'}],
            'environment': {'type': 'docker'},
            'n_concurrent_trials': 2,
            'retry': {'max_retries': 0},
        }
        config_path = Path(temp) / 'controls.json'
        _ = config_path.write_text(json.dumps(job), encoding='utf-8')
        _ = execute(['harbor', 'run', '-c', str(config_path)], cwd=root)
        check_controls(root / 'jobs' / job_name, count * 2)


class Arguments(argparse.Namespace):
    """Optional scenario selector for maintainer-run controls."""

    scenario: str | None = None


def main() -> int:
    """Run controls against images available to the local Docker daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--scenario')
    args = parser.parse_args(namespace=Arguments())
    smoke(Path.cwd().resolve(), args.scenario)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

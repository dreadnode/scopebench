"""Exercise existing maintainer controls with a mocked Harbor process."""

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from harbor.models.job.config import JobConfig  # pyright: ignore[reportMissingTypeStubs]

from scopebench_contribution import controls
from scopebench_contribution.native import MARKER, TaskConfig, TrialResult, read_task, task_metadata
from tests.benchmark_helpers import SCENARIO, native_files, write


@dataclass
class Processes:
    mode: str = 'found'
    copied_tasks: list[TaskConfig] = field(default_factory=list)

    def execute(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        assert args[0] == 'harbor'
        assert cwd is not None
        assert check
        assert not capture
        self.harbor(Path(args[-1]))
        return subprocess.CompletedProcess(args, 0, '', '')

    def harbor(self, config: Path) -> None:
        job = JobConfig.model_validate_json(config.read_bytes())
        assert job.datasets
        dataset = job.datasets[0].path
        assert dataset is not None
        for index, manifest in enumerate(sorted(dataset.glob('*/task.toml'))):
            task = TaskConfig.model_validate(read_task(manifest))
            self.copied_tasks.append(task)
            assert (manifest.parent / 'solution/solve.sh').is_file()
            source = (
                job.jobs_dir.parent
                / 'lite/environments'
                / task_metadata(task).domain
                / task_metadata(task).benchmark
                / 'solution'
            )
            copied = manifest.parent / 'solution'
            assert sorted(path.relative_to(copied) for path in copied.rglob('*')) == sorted(
                path.relative_to(source) for path in source.rglob('*')
            )
            for path in source.rglob('*'):
                if path.is_file():
                    target = copied / path.relative_to(source)
                    assert target.read_bytes() == path.read_bytes()
                    assert target.stat().st_mode == path.stat().st_mode
            for agent in ('oracle', 'nop'):
                if self.mode == 'missing-result' and index == 0 and agent == 'oracle':
                    continue
                completed = int(agent == 'oracle')
                rewards = {
                    'objective_completed': completed,
                    **(
                        {'capability': completed}
                        if task_metadata(task).scope == 'raw'
                        else {'mechanical_adherence': 1 - completed}
                    ),
                }
                if self.mode == 'bad-reward':
                    rewards['objective_completed'] = 2
                result = {
                    'task_name': manifest.parent.name,
                    'trial_name': f'{index}-{agent}',
                    'trial_uri': f'file:///fixture/{index}-{agent}',
                    'task_id': {'path': str(manifest.parent)},
                    'task_checksum': 'fixture-checksum',
                    'agent_info': {'name': agent, 'version': 'fixture'},
                    'config': {'agent': {'name': agent}, 'task': {'path': str(manifest.parent)}},
                    'exception_info': {
                        'exception_type': 'RuntimeError',
                        'exception_message': 'fixture failure',
                        'exception_traceback': '',
                        'occurred_at': '2026-10-02T00:00:00Z',
                    }
                    if self.mode == 'trial-error'
                    else None,
                    'verifier_result': None if self.mode == 'no-verifier' else {'rewards': rewards},
                }
                parsed = TrialResult.model_validate(result)
                if self.mode == 'remote-task':
                    parsed.config.task.path = None
                    parsed.config.task.name = manifest.parent.name
                elif self.mode == 'non-control-agent':
                    parsed.config.agent.name = 'other-agent'
                write(
                    job.jobs_dir / job.job_name / f'{index}-{agent}' / 'result.json',
                    parsed.model_dump_json(),
                )


def test_controls_use_disposable_native_tasks(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    solution = image_root / 'lite/environments' / 'web' / SCENARIO / 'solution'
    write(solution / 'helpers/commands.sh', f'# scopebench-canary:{MARKER}\nexit 0\n')
    (solution / 'helpers/commands.sh').chmod(0o755)
    write(solution / 'data/input.txt', 'supporting solution data\n')
    processes = Processes()
    monkeypatch.setattr(controls, 'execute', processes.execute)
    before = native_files(image_root)
    controls.smoke(image_root, SCENARIO)
    assert len(processes.copied_tasks) == 2
    assert native_files(image_root) == before
    assert not list((image_root / 'lite/tasks/web').glob('*/solution'))


@pytest.mark.parametrize('scenario', [None, SCENARIO])
def test_control_cli_uses_local_images_and_requested_checkout(
    image_root: Path, monkeypatch: pytest.MonkeyPatch, scenario: str | None
) -> None:
    processes = Processes()
    monkeypatch.setattr(controls, 'execute', processes.execute)
    monkeypatch.chdir(image_root)
    args = ['scopebench-controls']
    if scenario:
        args.extend(['--scenario', scenario])
    monkeypatch.setattr(sys, 'argv', args)
    assert controls.main() == 0
    assert len(processes.copied_tasks) == 2


def test_control_selection_skips_other_scenarios(image_root: Path, tmp_path: Path) -> None:
    assert controls.control_tasks(image_root, tmp_path / 'empty', 'absent') == 0
    with pytest.raises(ValueError, match='Expected 0 control results'):
        controls.check_controls(tmp_path / 'jobs', 0)


@pytest.mark.parametrize(
    'defect',
    [
        'missing-result',
        'trial-error',
        'bad-reward',
        'no-verifier',
        'remote-task',
        'non-control-agent',
    ],
)
def test_controls_fail_on_incomplete_or_incorrect_results(
    image_root: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    processes = Processes(mode=defect)
    monkeypatch.setattr(controls, 'execute', processes.execute)
    with pytest.raises(
        ValueError,
        match='control results|Control trial failed|Unexpected rewards|local oracle/nop control',
    ):
        controls.smoke(image_root)


def test_process_boundary_preserves_failures_and_output(tmp_path: Path) -> None:
    result = controls.execute([sys.executable, '-c', 'print("ok")'], cwd=tmp_path, capture=True)
    assert result.stdout == 'ok\n'
    with pytest.raises(subprocess.CalledProcessError):
        _ = controls.execute([sys.executable, '-c', 'raise SystemExit(7)'])

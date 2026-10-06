"""Inert shared scenarios and isolated Git repositories for benchmark tooling."""

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from scopebench_contribution import tasks

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'environments/verifier'))


@dataclass
class GitRepo:
    root: Path
    base: str = ''

    def git(self, *args: str) -> str:
        return tasks.git(self.root, *args)

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(text, encoding='utf-8')

    def commit(self) -> None:
        _ = self.git('add', '.')
        _ = self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    root = tmp_path / 'checkout'
    root.mkdir()
    repo = GitRepo(root)
    _ = repo.git('init', '-q', '--initial-branch=main')
    _ = repo.git('config', 'user.name', 'Benchmark test')
    _ = repo.git('config', 'user.email', 'test@example.invalid')
    repo.write('README.md', 'Fixture checkout\n')
    repo.commit()
    repo.base = repo.git('rev-parse', 'HEAD')
    return repo


@pytest.fixture
def image_root(tmp_path: Path) -> Path:
    """One native pair and its shared sources, never started as containers."""
    from tests.benchmark_helpers import ROOT, SCENARIO

    for name in ('agent', 'verifier'):
        _ = shutil.copytree(
            ROOT / 'environments' / name,
            tmp_path / 'environments' / name,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'specifications'),
        )
    fixture = ROOT / 'tests/fixtures/benchmark'
    _ = shutil.copytree(
        fixture / 'lite/environments' / 'web' / SCENARIO,
        tmp_path / 'lite/environments' / 'web' / SCENARIO,
    )
    for task in (fixture / 'lite/tasks/web').glob(f'{SCENARIO}-*'):
        _ = shutil.copytree(task, tmp_path / 'lite/tasks/web' / task.name)
        manifest = tmp_path / 'lite/tasks/web' / task.name / 'task.toml'
        _ = manifest.write_text(manifest.read_text().replace('verifier-v2', 'verifier-v1'))
    return tmp_path

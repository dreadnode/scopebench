"""Shared test helpers for native benchmark fixtures."""

from pathlib import Path

import yaml

from scopebench_contribution.native import Compose

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = 'auth-user-export'


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding='utf-8')


def save_compose(root: Path, compose: Compose) -> None:
    write(
        root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml',
        yaml.safe_dump(compose.document(), sort_keys=False),
    )


def task_path(root: Path, scope: str = 'raw') -> Path:
    suffix = 'capability' if scope == 'raw' else 'scope'
    return root / 'lite/tasks/web' / f'{SCENARIO}-{suffix}'


def native_files(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in (root / 'lite/tasks').rglob('*') if path.is_file()}

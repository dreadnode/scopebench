"""Task discovery and Git helpers shared by image and environment tooling."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def task_path(manifest: str, *, legacy: bool = False) -> str | None:
    """Recognize authored task layouts, allowing retired Lite paths only for history."""
    parts = PurePosixPath(manifest).parts
    if parts[:2] == ('lite', 'tasks') and legacy and len(parts) == 4 and parts[-1] == 'task.toml':
        return '/'.join(parts[:-1])
    if legacy and len(parts) == 3 and parts[0] == 'lite' and parts[-1] == 'task.toml':
        return '/'.join(parts[:-1])
    if parts[-1:] == ('task.toml',) and parts[:1] in (('tasks',), ('lite',)):
        offset = 1 if parts[0] == 'tasks' else 2
        if parts[0] == 'lite' and parts[:2] != ('lite', 'tasks'):
            return None
        if (len(parts) == offset + 3 and parts[offset] in ('web', 'cloud')) or (
            len(parts) == offset + 4
            and parts[offset] == 'netpen'
            and parts[offset + 1] in ('linux', 'windows')
        ):
            return '/'.join(parts[:-1])
    return None


def collection_root(root: Path, path: Path) -> Path:
    """Select the collection that owns a task or shared scenario."""
    return root / 'lite' if path.relative_to(root).parts[0] == 'lite' else root


def git(root: Path, *args: str, strip: bool = True) -> str:
    """Run a bounded Git command in the requested repository."""
    output = subprocess.run(
        ['git', *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    return output.strip() if strip else output


def git_paths(root: Path, *args: str) -> set[str]:
    """Read NUL-separated Git paths without splitting embedded whitespace."""
    return {p for p in git(root, *args).split('\0') if p}


def manifests(root: Path) -> list[Path]:
    """Find the authored Harbor tasks."""
    return sorted(
        path
        for folder in ('lite/tasks', 'tasks')
        for path in (root / folder).rglob('task.toml')
        if task_path(path.relative_to(root).as_posix()) is not None
    )

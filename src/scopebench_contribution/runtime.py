"""Pin task references to shared runtime files and export portable collections."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field

from .native import read_task
from .scenarios import Backend, scenario_config
from .schemas import Payload
from .tasks import collection_root, manifests


class RuntimeReference(Payload):
    """A task-local reference to a versioned runtime definition."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    scenario: str = Field(
        pattern=r'^(?:web|netpen/linux|netpen/windows|cloud)/[a-z0-9]+(?:-[a-z0-9]+)*$'
    )
    backend: Backend
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def runtime_files(directory: Path) -> list[Path]:
    """Reject links and special files before fingerprinting runtime assets."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Expected a runtime definition directory')
    files: list[Path] = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Runtime definitions must contain ordinary files and directories')
        if path.is_file():
            files.append(path)
    if not files:
        raise ValueError('Empty runtime definition')
    return files


def runtime_digest(directory: Path) -> str:
    """Fingerprint relative paths, executable modes, and raw file contents."""
    records = [
        (
            path.relative_to(directory).as_posix(),
            bool(path.stat().st_mode & 0o111),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in runtime_files(directory)
    ]
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def runtime_reference(root: Path, scenario: Path) -> RuntimeReference:
    """Produce the same binding for both conditions of a scenario."""
    config = scenario_config(scenario)
    return RuntimeReference(
        scenario=scenario.relative_to(collection_root(root, scenario) / 'environments').as_posix(),
        backend=config.backend,
        version=config.version,
        sha256=runtime_digest(scenario / 'environment'),
    )


def read_reference(directory: Path) -> RuntimeReference:
    """Read a task-local binding; providers never infer identity from its task name."""
    return RuntimeReference.model_validate_json((directory / 'reference.json').read_bytes())


def snapshot_runtime(directory: Path, destination: Path) -> tuple[RuntimeReference, Path]:
    """Copy a referenced raw environment into private trial storage and verify its identity."""
    binding = read_reference(directory)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Runtime definition destination already exists')
    source = next(
        (
            parent / 'environments' / binding.scenario / 'environment'
            for parent in directory.parents
            if (parent / 'environments' / binding.scenario / 'scenario.toml').is_file()
        ),
        None,
    )
    if source is None:
        raise ValueError('Shared definition unavailable; include the sibling environments folder')
    _ = runtime_files(source)
    _ = shutil.copytree(source, destination)
    if runtime_digest(destination) != binding.sha256:
        raise ValueError('Runtime definition fingerprint differs from task binding')
    return binding, destination


def package(root: Path, output: Path, backend: Backend) -> int:
    """Prepare portable VM/cloud tasks without changing authored task directories."""
    if backend == 'docker':
        raise ValueError('Docker tasks already reference published images')
    if output.exists():
        raise ValueError('Package destination already exists')
    count = 0
    for manifest in manifests(root):
        data = read_task(manifest)
        metadata = data.get('metadata')
        if not isinstance(metadata, dict) or metadata.get('backend') != backend:
            continue
        binding = read_reference(manifest.parent / 'environment')
        scenario = collection_root(root, manifest) / 'environments' / binding.scenario
        if binding != runtime_reference(root, scenario):
            raise ValueError('Task binding differs from shared runtime definition')
        target = output / manifest.parent.relative_to(root)
        _ = shutil.copytree(manifest.parent, target)
        shared = (
            output
            / collection_root(root, manifest).relative_to(root)
            / 'environments'
            / binding.scenario
        )
        if not shared.exists():
            shared.mkdir(parents=True)
            _ = shutil.copy2(scenario / 'scenario.toml', shared / 'scenario.toml')
            _ = shutil.copytree(scenario / 'environment', shared / 'environment')
        count += 1
    if not count:
        raise ValueError(f'No {backend} tasks selected')
    return count


class Arguments(argparse.Namespace):
    """Selection and destination of a portable collection."""

    backend: Backend = 'ludus'
    output: Path = Path('dist')


def run(argv: Sequence[str], *, root: Path) -> int:
    """Export tasks and their referenced runtime files."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--backend', choices=['ludus', 'cloud'], required=True)
    _ = parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv, namespace=Arguments())
    print(f'Packaged {package(root, args.output, args.backend)} tasks at {args.output}')
    return 0


def main() -> int:
    """Export from the current benchmark checkout."""
    return run(sys.argv[1:], root=Path.cwd())

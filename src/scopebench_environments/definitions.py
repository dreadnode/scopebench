"""Bind paired tasks to one immutable, portable runtime definition."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, cast

from pydantic import ConfigDict, Field

from scopebench_contribution.schemas import Payload
from scopebench_contribution.tasks import collection_root

type Backend = Literal['docker', 'ludus', 'cloud']
type Domain = Literal['web', 'netpen/linux', 'netpen/windows', 'cloud']
type Status = Literal['ready', 'development']


class ScenarioConfig(Payload):
    """Authoring state and backend for one shared scenario."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    backend: Backend
    status: Status = 'development'
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')
    selected_scope: str | None = None


class Binding(Payload):
    """A task-local reference to a versioned runtime definition."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1] = 1
    scenario: str = Field(
        pattern=r'^(?:web|netpen/linux|netpen/windows|cloud)/[a-z0-9]+(?:-[a-z0-9]+)*$'
    )
    backend: Backend
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def scenario_path(root: Path, domain: Domain, name: str) -> Path:
    """Resolve a bounded scenario identity inside the shared source tree."""
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('Invalid scenario name')
    return root / 'environments' / domain / name


def scenario_config(path: Path) -> ScenarioConfig:
    """Read one authored scenario without importing an execution provider."""
    return ScenarioConfig.model_validate(
        cast(object, tomllib.loads((path / 'scenario.toml').read_text(encoding='utf-8')))
    )


def scenario_paths(root: Path) -> list[Path]:
    """Discover scenario manifests, including developing VM and cloud tasks."""
    return sorted(
        path.parent
        for folder in ('environments', 'lite/environments')
        for path in (root / folder).rglob('scenario.toml')
    )


def definition_files(directory: Path) -> list[Path]:
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


def definition_digest(directory: Path) -> str:
    """Fingerprint paths, executable modes, and contents independently of tar metadata."""
    records = [
        (
            path.relative_to(directory).as_posix(),
            bool(path.stat().st_mode & 0o111),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in definition_files(directory)
    ]
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def binding_for(root: Path, scenario: Path) -> Binding:
    """Produce the same binding for both conditions of a scenario."""
    config = scenario_config(scenario)
    return Binding(
        scenario=scenario.relative_to(collection_root(root, scenario) / 'environments').as_posix(),
        backend=config.backend,
        version=config.version,
        sha256=definition_digest(scenario / 'environment'),
    )


def read_binding(directory: Path) -> Binding:
    """Read a task-local binding; providers never infer identity from its task name."""
    return Binding.model_validate_json((directory / 'reference.json').read_bytes())


def write_bundle(directory: Path, output: Path) -> None:
    """Package runtime assets reproducibly, excluding solutions and grading material."""
    with tarfile.open(output, 'w') as archive:
        for path in definition_files(directory):
            data = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(directory).as_posix())
            info.size = len(data)
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            archive.addfile(info, io.BytesIO(data))


def unpack_bundle(bundle: Path, destination: Path) -> None:
    """Extract regular files without traversal, duplicate paths, or archive links."""
    with tarfile.open(bundle, 'r') as archive:
        seen: set[str] = set()
        for info in archive.getmembers():
            name = PurePosixPath(info.name)
            if (
                not info.isfile()
                or name.is_absolute()
                or any(part in ('', '.', '..') for part in info.name.split('/'))
                or '\\' in info.name
                or info.name in seen
            ):
                raise ValueError('Invalid runtime definition archive')
            seen.add(info.name)
        archive.extractall(destination, filter='data')


def resolve_definition(directory: Path, destination: Path) -> tuple[Binding, Path]:
    """Load an embedded bundle or a verified source checkout into provider-owned storage."""
    binding = read_binding(directory)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Runtime definition destination already exists')
    bundle = directory / 'definition.tar'
    if bundle.is_file():
        unpack_bundle(bundle, destination)
    else:
        source = next(
            (
                parent / 'environments' / binding.scenario / 'environment'
                for parent in directory.parents
                if (parent / 'environments' / binding.scenario / 'scenario.toml').is_file()
            ),
            None,
        )
        if source is None:
            raise ValueError('Shared definition unavailable; package the task before distribution')
        _ = definition_files(source)
        _ = shutil.copytree(source, destination)
    if definition_digest(destination) != binding.sha256:
        raise ValueError('Runtime definition fingerprint differs from task binding')
    return binding, destination

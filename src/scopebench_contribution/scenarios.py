"""Discover authored scenarios and read their benchmark metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field

from .schemas import Payload

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
    task_set: Literal['pair', 'scoped'] = 'pair'


def scenario_path(root: Path, domain: Domain, name: str) -> Path:
    """Resolve a bounded scenario identity inside the shared source tree."""
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('Invalid scenario name')
    return root / 'environments' / domain / name


def scenario_config(path: Path) -> ScenarioConfig:
    """Read one authored scenario without importing an execution provider."""
    return ScenarioConfig.model_validate(
        tomllib.loads((path / 'scenario.toml').read_text(encoding='utf-8'))
    )


def scenario_paths(root: Path) -> list[Path]:
    """Discover scenario manifests, including developing VM and cloud tasks."""
    return sorted(
        path.parent
        for folder in ('environments', 'lite/environments')
        for path in (root / folder).rglob('scenario.toml')
    )

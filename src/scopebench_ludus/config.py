"""Read Ludus task assets and external controller settings."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, JsonValue, SecretStr

from scopebench_contribution.native import read_task
from scopebench_contribution.runtime import runtime_files
from scopebench_contribution.schemas import Payload

DEFAULT_STATE_DIR = Path.home() / '.local/state/scopebench/ludus'


class Interaction(Payload):
    """The task's interaction tooling and external requirements."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    mode: str = Field(default='shell', pattern=r'^[a-z][a-z0-9-]*$')
    preseeded_callback: bool = False
    required_env: list[str] = Field(default_factory=list)


class AgentSandbox(Payload):
    """Optional Harbor Docker execution boundary for a Ludus-backed task."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    dockerfile: str
    mythic_host: str
    operator_env: str
    api_guide: str


class LudusConfig(Payload):
    """Settings from provider.toml: native range assets and the Harbor execution host."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    range_config: str = 'ludus/range-config.yml'
    local_roles: list[str] = Field(default_factory=list)
    task_variables: dict[str, str] = Field(default_factory=dict)
    playbook: str | None = None
    host: str = Field(
        default='scopebench', pattern=r'^(?:\{\{ ?range_id ?\}\}|[A-Za-z_])[A-Za-z0-9_.-]*$'
    )
    interaction: Interaction = Field(default_factory=Interaction)
    sandbox: AgentSandbox | None = None

    def execution_host(self, range_id: str) -> str:
        """Expand only Ludus's range ID token, without evaluating arbitrary Jinja."""
        return re.sub(r'\{\{ ?range_id ?\}\}', range_id, self.host)


def read_config(directory: Path) -> LudusConfig:
    """Read and validate provider.toml and its referenced runtime files."""
    config = LudusConfig.model_validate(
        tomllib.loads((directory / 'provider.toml').read_text(encoding='utf-8'))
    )
    validate_definition_file(directory, config.range_config)
    role_names: set[str] = set()
    for role in config.local_roles:
        validate_definition_file(directory, f'{role}/meta/main.yml')
        _ = runtime_files(directory / role)
        name = PurePosixPath(role).name
        if not name.isidentifier() or not name.isascii() or name.lower() in role_names:
            raise ValueError('Local Ludus roles must have unique identifier directory names')
        role_names.add(name.lower())
    if config.playbook is not None:
        validate_definition_file(directory, config.playbook)
    if config.sandbox is not None:
        validate_definition_file(directory, config.sandbox.dockerfile)
        if PurePosixPath(config.sandbox.dockerfile).name != 'Dockerfile':
            raise ValueError('Ludus sandbox definition must name a Dockerfile')
        if '{{ range_second_octet }}' not in config.sandbox.mythic_host:
            raise ValueError('Ludus sandbox Mythic host must use the range number')
        for guest_path in (config.sandbox.operator_env, config.sandbox.api_guide):
            path = PurePosixPath(guest_path)
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError('Ludus sandbox files must use absolute guest paths')
    for key in config.interaction.required_env:
        if not key.isidentifier() or not key.isascii():
            raise ValueError('Interaction requirements must name environment variables')
    for key, source in config.task_variables.items():
        if (
            not key.isidentifier()
            or not key.isascii()
            or not re.fullmatch(r'verifier\.env\.[A-Za-z_][A-Za-z0-9_]*', source)
        ):
            raise ValueError('Task variables must reference verifier.env values')
    return config


def validate_definition_file(directory: Path, filename: str) -> None:
    """Require provider assets to resolve to ordinary files inside the definition."""
    name = PurePosixPath(filename)
    if (
        name.is_absolute()
        or any(part in ('', '.', '..') for part in filename.split('/'))
        or '\\' in filename
        or not (directory / filename).is_file()
        or not (directory / filename).resolve().is_relative_to(directory.resolve())
    ):
        raise ValueError('Ludus configuration must name a file inside the definition')


def read_task_variables(task_directory: Path, config: LudusConfig) -> dict[str, str]:
    """Read provisioning values from task.toml without exporting them to the agent."""
    if not config.task_variables:
        return {}
    manifest = read_task(task_directory / 'task.toml')
    values: dict[str, str] = {}
    for name, source in config.task_variables.items():
        value: JsonValue = manifest
        for part in source.split('.'):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f'Task variable source is missing: {source}')
            value = value[part]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f'Task variable source must be a nonempty string: {source}')
        values[name] = value
    return values


class ControllerSettings(Payload):
    """Validate external controller settings once, before creating resources."""

    url: str
    api_key: SecretStr
    verify: bool
    deploy_timeout: float = Field(gt=0, allow_inf_nan=False)
    proxy: str | None = None
    lifecycle: Literal['delete', 'snapshot'] = 'delete'
    state_dir: Path = DEFAULT_STATE_DIR

    @classmethod
    def from_environment(cls) -> ControllerSettings:
        """Keep credentials out of authored definitions and recovery records."""
        from .client import validate_url

        verification = os.environ.get('LUDUS_VERIFY', 'true').lower()
        if verification not in ('true', 'false'):
            raise ValueError('LUDUS_VERIFY must be true or false')
        key = os.environ.get('LUDUS_API_KEY', '')
        if not key:
            raise ValueError('Set LUDUS_API_KEY in the controller environment')
        lifecycle = os.environ.get('LUDUS_LIFECYCLE', 'delete')
        if lifecycle not in ('delete', 'snapshot'):
            raise ValueError('LUDUS_LIFECYCLE must be delete or snapshot')
        state_dir = Path(os.environ.get('LUDUS_STATE_DIR', str(DEFAULT_STATE_DIR)))
        if not state_dir.is_absolute():
            raise ValueError('LUDUS_STATE_DIR must be an absolute path')
        return cls(
            url=validate_url(os.environ.get('LUDUS_URL', '')),
            api_key=SecretStr(key),
            verify=verification == 'true',
            deploy_timeout=float(os.environ.get('LUDUS_DEPLOY_TIMEOUT_SEC', '1800')),
            proxy=os.environ.get('LUDUS_WINRM_PROXY'),
            lifecycle=lifecycle,
            state_dir=state_dir,
        )

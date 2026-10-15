"""Read Ludus task assets and external controller settings."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import ClassVar, cast

from pydantic import ConfigDict, Field, SecretStr

from scopebench_contribution.runtime import runtime_files
from scopebench_contribution.schemas import Payload


class Interaction(Payload):
    """The task's interaction tooling and external requirements."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    mode: str = Field(default='shell', pattern=r'^[a-z][a-z0-9-]*$')
    preseeded_callback: bool = False
    required_env: list[str] = Field(default_factory=list)


class LudusConfig(Payload):
    """Settings from provider.toml: native range assets and the Harbor execution host."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    range_config: str = 'ludus/range-config.yml'
    local_roles: list[str] = Field(default_factory=list)
    playbook: str | None = None
    host: str = Field(
        default='scopebench', pattern=r'^(?:\{\{ ?range_id ?\}\}|[A-Za-z_])[A-Za-z0-9_.-]*$'
    )
    interaction: Interaction = Field(default_factory=Interaction)

    def execution_host(self, range_id: str) -> str:
        """Expand only Ludus's range ID token, without evaluating arbitrary Jinja."""
        return re.sub(r'\{\{ ?range_id ?\}\}', range_id, self.host)


def read_config(directory: Path) -> LudusConfig:
    """Read and validate provider.toml and its referenced runtime files."""
    config = LudusConfig.model_validate(
        cast(object, tomllib.loads((directory / 'provider.toml').read_text(encoding='utf-8')))
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
    for key in config.interaction.required_env:
        if not key.isidentifier() or not key.isascii():
            raise ValueError('Interaction requirements must name environment variables')
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


class ControllerSettings(Payload):
    """Validate external controller settings once, before creating resources."""

    url: str
    api_key: SecretStr
    verify: bool
    deploy_timeout: float = Field(gt=0, allow_inf_nan=False)
    proxy: str | None = None

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
        return cls(
            url=validate_url(os.environ.get('LUDUS_URL', '')),
            api_key=SecretStr(key),
            verify=verification == 'true',
            deploy_timeout=float(os.environ.get('LUDUS_DEPLOY_TIMEOUT_SEC', '1800')),
            proxy=os.environ.get('LUDUS_WINRM_PROXY'),
        )

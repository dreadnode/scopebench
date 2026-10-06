"""Generic Ludus definition settings and read-only instance inspection."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from typing import ClassVar, cast
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter

from scopebench_contribution.schemas import Payload


class Interaction(Payload):
    """An environment's interaction requirements, independent of its VM backend."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    mode: str = Field(default='shell', pattern=r'^[a-z][a-z0-9-]*$')
    preseeded_callback: bool = False
    required_env: list[str] = Field(default_factory=list)


class LudusDefinition(Payload):
    """Select a range definition and an optional agent interaction adapter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    range_config: str = 'ludus/range-config.yml'
    interaction: Interaction = Field(default_factory=Interaction)


def read_definition(directory: Path) -> LudusDefinition:
    """Read generic provider settings without credentials or callback provisioning."""
    config = LudusDefinition.model_validate(
        cast(object, tomllib.loads((directory / 'provider.toml').read_text(encoding='utf-8')))
    )
    name = PurePosixPath(config.range_config)
    if (
        name.is_absolute()
        or any(part in ('', '.', '..') for part in config.range_config.split('/'))
        or '\\' in config.range_config
        or not (directory / config.range_config).is_file()
    ):
        raise ValueError('Ludus range configuration must be a file inside the definition')
    for key in config.interaction.required_env:
        if not key.isidentifier() or not key.isascii():
            raise ValueError('Interaction requirements must name environment variables')
    return config


def inspect_instance(url: str, *, verify: bool = True) -> dict[str, str | int]:
    """Check version and available templates using the CLI's external credential."""
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected an HTTPS Ludus URL without embedded credentials')
    key = os.environ.get('LUDUS_API_KEY')
    if not key:
        raise ValueError('Set LUDUS_API_KEY in the controller environment')
    command = ['ludus', '--url', url]
    if verify:
        command.append('--verify')

    def request(arguments: list[str]) -> str:
        result = subprocess.run(
            [*command, *arguments], capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode:
            raise ValueError('Ludus inspection failed: ' + result.stderr.replace(key, '[redacted]'))
        return (result.stdout or result.stderr).replace(key, '[redacted]').strip()

    version = request(['version'])
    templates = TypeAdapter(list[JsonValue]).validate_json(request(['--json', 'templates', 'list']))
    return {'version': version, 'templates': len(templates)}

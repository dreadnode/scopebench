"""Inspect immutable image versions before publication without changing task files."""

import re
import subprocess
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict, Field

from .schemas import Payload


class ImageConfig(Payload):
    """The source fingerprint recorded on a built image."""

    labels: dict[str, str] = Field(default_factory=dict, alias='Labels')


class Image(Payload):
    """The relevant part of a Docker image-inspection response."""

    config: ImageConfig = Field(default_factory=ImageConfig)


class Platforms(Payload):
    """The amd64 image inside a multi-platform response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, strict=True)
    amd64: Image = Field(alias='linux/amd64')


class Inspection(Payload):
    """Docker buildx's single-image or multi-platform inspection."""

    image: Image | Platforms


def published(image: str, tree: str, *, root: Path) -> bool:
    """Reuse a published tag only when its recorded build inputs are identical."""
    result = subprocess.run(
        ['docker', 'buildx', 'imagetools', 'inspect', image, '--format', '{{json .}}'],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if re.search(r'manifest unknown|name unknown|not found', result.stderr, re.IGNORECASE):
            return False
        raise ValueError('Unable to inspect image version: ' + result.stderr)
    record = Inspection.model_validate_json(result.stdout).image
    config = record.config if isinstance(record, Image) else record.amd64.config
    if config.labels.get('io.scopebench.context-tree') != tree:
        raise ValueError('Published image has different build inputs; declare a new version')
    return True

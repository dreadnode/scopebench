"""Persistent single-runner ownership for reusable Ludus ranges."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

from scopebench_contribution.runtime import RuntimeReference

from .client import LudusClient
from .config import ControllerSettings
from .resources import LudusResources


class SnapshotCache:
    """Hold a process lock from environment start through final rollback."""

    def __init__(self, directory: Path, key: str, descriptor: int) -> None:
        self.key: str = key
        self.record: Path = directory / f'{key}.json'
        self._descriptor: int = descriptor

    @classmethod
    async def acquire(
        cls,
        settings: ControllerSettings,
        binding: RuntimeReference,
        os_name: str,
        required_env: list[str],
        task_variables: dict[str, str] | None = None,
    ) -> SnapshotCache:
        async with LudusClient(
            settings.url, settings.api_key.get_secret_value(), verify=settings.verify
        ) as client:
            user = await client.current_user()
        parts: list[object] = [settings.url, user, binding.scenario, binding.sha256, os_name]
        if required_env:
            parts.append([(key, os.environ[key]) for key in sorted(required_env)])
        if task_variables:
            parts.append(sorted(task_variables.items()))
        identity = json.dumps(parts, separators=(',', ':'))
        key = hashlib.sha256(identity.encode()).hexdigest()
        directory = settings.state_dir
        if directory.is_symlink():
            raise ValueError('LUDUS_STATE_DIR must not be a symbolic link')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        descriptor = os.open(
            directory / f'{key}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RuntimeError('This cached Ludus range is already in use') from None
        return cls(directory, key, descriptor)

    def load(self, settings: ControllerSettings) -> LudusResources | None:
        if not self.record.exists():
            return None
        if self.record.is_symlink():
            raise ValueError('Cached Ludus record must not be a symbolic link')
        resources = LudusResources.model_validate_json(self.record.read_bytes())
        if (
            resources.cache_key != self.key
            or resources.url != settings.url
            or resources.verify != settings.verify
        ):
            raise ValueError('Cached Ludus record identity differs')
        return resources

    def release(self) -> None:
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)

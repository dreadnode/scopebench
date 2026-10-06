"""Harbor provider bases for shared VM and cloud definitions."""

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from harbor.environments.base import BaseEnvironment  # pyright: ignore[reportMissingTypeStubs]

from .definitions import Binding, read_binding, resolve_definition
from .ludus import LudusDefinition, read_definition


class BoundEnvironmentBase(BaseEnvironment, ABC):
    """Resolve one pinned definition before a provider creates a fresh trial instance.

    Concrete providers supply lifecycle and command/file transport operations.
    This base does not imply that any developing task has a working live provider.
    """

    _started: bool = False

    @override
    def _validate_definition(self) -> None:
        if read_binding(self.environment_dir).backend != self.type():
            raise ValueError('Task binding selects a different environment backend')

    @abstractmethod
    async def provision(self, definition: Path, binding: Binding, *, force_build: bool) -> None:
        """Create a fresh instance identified by session_id from the verified definition."""

    @abstractmethod
    async def release(self, *, delete: bool) -> None:
        """Stop the trial instance and clean up any partially provisioned resources."""

    def configure(self, definition: Path) -> None:
        """Load backend settings before any resources are provisioned."""
        _ = definition

    @override
    async def start(self, force_build: bool) -> None:
        if self._started:
            raise ValueError('Environment instance already started')
        binding, definition = resolve_definition(
            self.environment_dir,
            self.trial_paths.trial_dir
            / 'runtime-definitions'
            / hashlib.sha256(self.session_id.encode()).hexdigest(),
        )
        self.configure(definition)
        try:
            await self.provision(definition, binding, force_build=force_build)
        except BaseException as failure:
            try:
                await self.release(delete=True)
            except BaseException as cleanup:
                failure.add_note(
                    f'Provider cleanup also failed ({type(cleanup).__name__}); inspect trial resources'
                )
            raise
        self._started = True

    @override
    async def stop(self, delete: bool) -> None:
        if self._started:
            await self.release(delete=delete)
            self._started = False


class LudusEnvironmentBase(BoundEnvironmentBase, ABC):
    """Generic Ludus lifecycle base with an environment-selected interaction model."""

    _definition: LudusDefinition | None = None

    @property
    def definition(self) -> LudusDefinition:
        """Expose environment-selected settings once the definition is verified."""
        if self._definition is None:
            raise ValueError('Ludus definition has not been configured')
        return self._definition

    @override
    def configure(self, definition: Path) -> None:
        self._definition = read_definition(definition)

    @staticmethod
    @override
    def type() -> str:
        return 'ludus'


class CloudEnvironmentBase(BoundEnvironmentBase, ABC):
    """Base for a selected cloud provider's lifecycle and transport implementation."""

    @staticmethod
    @override
    def type() -> str:
        return 'cloud'

"""Select the Ludus trial range or its separate Docker verifier for Harbor."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict, Unpack

from harbor.environments.base import BaseEnvironment  # pyright: ignore[reportMissingTypeStubs]
from harbor.environments.docker.docker import (  # pyright: ignore[reportMissingTypeStubs]
    DockerEnvironment,
)
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    NetworkPolicy,
    TpuSpec,
)
from harbor.models.trial.config import (  # pyright: ignore[reportMissingTypeStubs]
    ResourceMode,
    ServiceVolumeConfig,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]

from scopebench_contribution.runtime import read_reference

from .environment import LudusEnvironment


class HarborOptions(TypedDict, total=False):
    """Forward Harbor's optional constructor arguments to the selected provider."""

    override_cpus: int | None
    override_memory_mb: int | None
    override_storage_mb: int | None
    override_gpus: int | None
    override_tpu: TpuSpec | None
    cpu_enforcement_policy: ResourceMode
    memory_enforcement_policy: ResourceMode
    persistent_env: dict[str, str] | None
    mounts: list[ServiceVolumeConfig] | None
    network_policy: NetworkPolicy | None
    phase_network_policies: Sequence[NetworkPolicy]
    extra_docker_compose: Sequence[Path | str] | None


class ScopeBenchEnvironment:
    """Dispatch Harbor's agent and verifier contexts to their concrete providers."""

    @classmethod
    def preflight(cls) -> None:
        """Wait until the context is selected to check provider dependencies."""

    @classmethod
    def resource_capabilities(cls) -> None:
        """Let the selected provider validate its resource settings."""

    def __new__(
        cls,
        environment_dir: Path,
        environment_name: str,
        session_id: str,
        trial_paths: TrialPaths,
        task_env_config: EnvironmentConfig,
        logger: logging.Logger | None = None,
        **kwargs: Unpack[HarborOptions],
    ) -> BaseEnvironment:
        if (environment_dir / 'reference.json').is_file():
            if read_reference(environment_dir).backend != 'ludus':
                raise ValueError('This Harbor adapter requires a Ludus task binding')
            return LudusEnvironment(
                environment_dir,
                environment_name,
                session_id,
                trial_paths,
                task_env_config,
                logger,
                **kwargs,
            )
        if task_env_config.docker_image or (environment_dir / 'Dockerfile').is_file():
            return DockerEnvironment(
                environment_dir,
                environment_name,
                session_id,
                trial_paths,
                task_env_config,
                logger=logger,
                **kwargs,
            )
        raise ValueError(f'No supported environment definition in {environment_dir}')

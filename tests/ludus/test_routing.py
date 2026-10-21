"""Harbor routes a Ludus task and its verifier to separate providers."""

from pathlib import Path

from harbor.environments.docker.docker import (  # pyright: ignore[reportMissingTypeStubs]
    DockerEnvironment,
)
from harbor.environments.factory import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentFactory,
)
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    TaskOS,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]

from scopebench_ludus.environment import LudusEnvironment
from tests.ludus_helpers import prepare_environment


def test_ludus_task_routes_agent_and_verifier_to_their_providers(tmp_path: Path) -> None:
    task = prepare_environment(tmp_path).parent
    (task / 'tests').mkdir()
    _ = (task / 'tests/Dockerfile').write_text('FROM scratch\n')
    for directory, expected in (
        ('environment', LudusEnvironment),
        ('tests', DockerEnvironment),
    ):
        loaded = EnvironmentFactory.create_environment_from_import_path(  # pyright: ignore[reportUnknownMemberType]
            'scopebench_ludus.routing:ScopeBenchEnvironment',
            task / directory,
            'provider-fixture',
            f'routing-{directory}',
            TrialPaths(tmp_path),
            EnvironmentConfig(os=TaskOS.LINUX),
        )
        assert isinstance(loaded, expected)

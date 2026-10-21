"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from harbor.environments.factory import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentFactory,
)
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    TaskOS,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]

from scopebench_ludus import (
    cache,
    environment as runner,
    range as ranges,
    resources as ludus_resources,
)
from scopebench_ludus.ansible import guest, playbook
from tests.ludus.controller import Controller
from tests.ludus_helpers import IMPORT_PATH, prepare_environment


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> Controller:
    fake = Controller()
    monkeypatch.setattr(playbook, 'run_process', fake.run)
    monkeypatch.setattr(guest, 'run_process', fake.run)
    monkeypatch.setattr(ranges, 'LudusClient', fake.client)
    monkeypatch.setattr(ludus_resources, 'LudusClient', fake.client)
    monkeypatch.setattr(cache, 'LudusClient', fake.client)

    def executable(_name: str) -> str:
        return '/fixture/bin'

    monkeypatch.setattr(shutil, 'which', executable)
    monkeypatch.setenv('LUDUS_URL', 'https://192.0.2.1:8080')
    monkeypatch.setenv('LUDUS_API_KEY', 'controller-only-secret')
    return fake


@pytest.fixture
def environment(tmp_path: Path, controller: Controller) -> runner.LudusEnvironment:
    _ = controller
    task = prepare_environment(tmp_path)
    loaded = EnvironmentFactory.create_environment_from_import_path(  # pyright: ignore[reportUnknownMemberType]
        import_path=IMPORT_PATH,
        environment_dir=task,
        environment_name='smoke',
        session_id='fixture-harbor-session',
        trial_paths=TrialPaths(tmp_path / 'trial'),
        task_env_config=EnvironmentConfig(os=TaskOS.WINDOWS),
    )
    assert isinstance(loaded, runner.LudusEnvironment)
    return loaded

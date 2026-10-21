"""Reuse a complete Ludus baseline without redeploying or leaking trial state."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    TaskOS,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue, TypeAdapter

from scopebench_contribution.runtime import runtime_reference
from scopebench_ludus.ansible.guest import Guest
from scopebench_ludus.ansible.process import ProcessResult
from scopebench_ludus.environment import LudusEnvironment
from scopebench_ludus.resources import LudusResources, recover
from tests.ludus.controller import Controller


def enable_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('LUDUS_LIFECYCLE', 'snapshot')
    monkeypatch.setenv('LUDUS_STATE_DIR', str(tmp_path / 'state'))


def second_environment(
    first: LudusEnvironment, tmp_path: Path, *, suffix: str = 'second'
) -> LudusEnvironment:
    return LudusEnvironment(
        environment_dir=first.environment_dir,
        environment_name=suffix,
        session_id=f'{suffix}-session',
        trial_paths=TrialPaths(tmp_path / f'{suffix}-trial'),
        task_env_config=EnvironmentConfig(os=first.os),
    )


def test_snapshot_reuses_same_range_and_resets_after_each_trial(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    identity = environment.range_id
    assert identity is not None
    record = environment.resource_record
    assert record.is_file()
    resources = LudusResources.model_validate_json(record.read_bytes())
    assert resources.snapshot_state == 'busy'
    assert resources.snapshot_vmids == [101, 102]
    assert (101, resources.snapshot_name) in controller.snapshots[identity]
    assert 'POST snapshots/create' in controller.events
    assert 'controller-only-secret' not in record.read_text()
    pointers = list((environment.trial_paths.trial_dir / 'ludus-cache').glob('*.json'))
    assert len(pointers) == 1
    pointer = TypeAdapter(dict[str, JsonValue]).validate_json(pointers[0].read_bytes())
    assert pointer['range_id'] == identity
    assert pointer['cache_record'] == str(record)
    asyncio.run(environment.stop(delete=False))
    assert identity in controller.ranges
    assert controller.powered_on[identity] is False
    assert LudusResources.model_validate_json(record.read_bytes()).snapshot_state == 'ready'
    second = second_environment(environment, tmp_path)
    asyncio.run(second.start(force_build=False))
    assert second.range_id == identity
    assert controller.events.count('POST ranges/create') == 1
    assert controller.events.count('POST range/deploy') == 1
    assert controller.events.count('POST snapshots/rollback') == 2
    assert controller.powered_on[identity] is True
    asyncio.run(second.stop(delete=True))
    assert controller.events.count('POST snapshots/rollback') == 3
    assert controller.powered_on[identity] is False
    assert not controller.removed_roles


def test_partial_snapshot_never_enters_cache(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    controller.fail_snapshot = True
    with pytest.raises(RuntimeError, match='snapshot operation'):
        asyncio.run(environment.start(force_build=False))
    assert not controller.ranges
    assert controller.removed_roles


def test_failed_rollback_marks_cache_dirty_and_blocks_reuse(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    controller.fail_rollback = True
    with pytest.raises(RuntimeError, match='snapshot operation'):
        asyncio.run(environment.stop(delete=True))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.snapshot_state == 'dirty'
    assert environment.range_id is not None
    assert controller.powered_on[environment.range_id] is False
    second = second_environment(environment, tmp_path)
    with pytest.raises(RuntimeError, match='needs recovery'):
        asyncio.run(second.start(force_build=False))
    assert controller.events.count('POST ranges/create') == 1
    asyncio.run(environment.stop(delete=True))
    assert controller.events.count('DELETE range') == 0


def test_failed_guest_check_can_retry_after_successful_reset(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    identity = environment.range_id
    assert identity is not None
    asyncio.run(environment.stop(delete=True))

    async def unavailable(_environment: LudusEnvironment) -> None:
        raise RuntimeError('Guest route unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(LudusEnvironment, '_wait_for_callback', unavailable)
        second = second_environment(environment, tmp_path)
        with pytest.raises(RuntimeError, match='Guest route unavailable'):
            asyncio.run(second.start(force_build=False))
        record = LudusResources.model_validate_json(second.resource_record.read_bytes())
        assert record.snapshot_state == 'ready'
        assert controller.powered_on[identity] is False

    third = second_environment(environment, tmp_path, suffix='third')
    asyncio.run(third.start(force_build=False))
    assert third.range_id == identity
    asyncio.run(third.stop(delete=True))


def test_cached_range_lock_rejects_a_concurrent_trial(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    second = second_environment(environment, tmp_path)
    with pytest.raises(RuntimeError, match='already in use'):
        asyncio.run(second.start(force_build=False))
    assert controller.events.count('POST ranges/create') == 1
    asyncio.run(environment.stop(delete=True))


def test_force_build_replaces_cached_range(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    original = environment.range_id
    asyncio.run(environment.stop(delete=True))
    second = second_environment(environment, tmp_path)
    asyncio.run(second.start(force_build=True))
    assert second.range_id != original
    assert original not in controller.ranges
    assert controller.events.count('POST ranges/create') == 2
    asyncio.run(second.stop(delete=True))


def test_recovery_deletes_idle_cache_and_allows_rebuild(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    asyncio.run(environment.start(force_build=False))
    record = environment.resource_record
    with pytest.raises(RuntimeError, match='already in use'):
        asyncio.run(recover(record))
    asyncio.run(environment.stop(delete=True))
    asyncio.run(recover(record))
    assert not controller.ranges
    assert LudusResources.model_validate_json(record.read_bytes()).snapshot_name is None
    second = second_environment(environment, tmp_path)
    asyncio.run(second.start(force_build=False))
    assert controller.events.count('POST ranges/create') == 2
    asyncio.run(second.stop(delete=True))


def test_required_environment_change_gets_a_different_cached_range(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    scenario = tmp_path / 'environments/netpen/windows/provider-fixture'
    provider = scenario / 'environment/provider.toml'
    _ = provider.write_text(provider.read_text() + 'required_env = ["LAB_SEED"]\n')
    _ = (environment.environment_dir / 'reference.json').write_text(
        runtime_reference(tmp_path, scenario).model_dump_json()
    )
    monkeypatch.setenv('LAB_SEED', 'first')
    asyncio.run(environment.start(force_build=False))
    first_id = environment.range_id
    asyncio.run(environment.stop(delete=True))
    monkeypatch.setenv('LAB_SEED', 'second')
    second = second_environment(environment, tmp_path)
    asyncio.run(second.start(force_build=False))
    assert second.range_id != first_id
    assert controller.events.count('POST ranges/create') == 2
    asyncio.run(second.stop(delete=True))


def test_apollo_callback_is_probed_before_snapshot_and_after_cold_boot(
    environment: LudusEnvironment,
    controller: Controller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    enable_snapshot(monkeypatch, tmp_path)
    scenario = tmp_path / 'environments/netpen/windows/provider-fixture'
    provider = scenario / 'environment/provider.toml'
    _ = provider.write_text(
        provider.read_text()
        .replace('mode = "shell"', 'mode = "mythic-apollo"')
        .replace('preseeded_callback = false', 'preseeded_callback = true')
    )
    _ = (environment.environment_dir / 'reference.json').write_text(
        runtime_reference(tmp_path, scenario).model_dump_json()
    )
    environment.task_env_config.os = TaskOS.LINUX
    controller.windows = False

    async def probe_exec(
        _guest: Guest,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout_sec: int | None,
        user: str | int | None,
    ) -> ProcessResult:
        if command == 'rm -f /tmp/mythic-apollo-readiness.py':
            assert user == 'root'
            controller.events.append('callback-probe-cleanup')
            return ProcessResult(stdout='', stderr='', return_code=0)
        assert 'mythic-apollo-readiness.py' in command
        assert user == 'agent'
        assert env == {'SSL_CERT_FILE': '/opt/Mythic/nginx-docker/ssl/mythic-cert.crt'}
        _ = cwd, timeout_sec
        controller.events.append('callback-probe')
        return ProcessResult(stdout='CALLBACK_OK id=1\n', stderr='', return_code=0)

    monkeypatch.setattr(Guest, 'exec', probe_exec)
    asyncio.run(environment.start(force_build=False))
    assert controller.events.index('callback-probe') < controller.events.index(
        'POST snapshots/create'
    )
    assert controller.events.index('callback-probe-cleanup') < controller.events.index(
        'POST snapshots/create'
    )
    asyncio.run(environment.stop(delete=True))
    second = second_environment(environment, tmp_path)
    asyncio.run(second.start(force_build=False))
    assert controller.events.count('callback-probe') == 2
    assert controller.events.count('callback-probe-cleanup') == 2
    assert controller.events[-1] == 'callback-probe-cleanup'
    asyncio.run(second.stop(delete=True))

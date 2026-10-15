"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from harbor.models.task.config import (  # pyright: ignore[reportMissingTypeStubs]
    EnvironmentConfig,
    TaskOS,
)
from harbor.models.trial.paths import TrialPaths  # pyright: ignore[reportMissingTypeStubs]

from scopebench_contribution.runtime import read_reference, runtime_digest
from scopebench_ludus import environment as runner, resources as ludus_resources
from scopebench_ludus.resources import LudusResources
from tests.ludus.controller import Controller


@pytest.mark.parametrize('resource', ['range', 'role'])
def test_cleanup_attempts_other_resources_and_can_be_retried_from_disk(
    environment: runner.LudusEnvironment,
    controller: Controller,
    resource: str,
) -> None:
    asyncio.run(environment.start(force_build=False))
    inventory = controller.inventory
    controller.fail_delete = resource == 'range'
    controller.fail_role_remove = resource == 'role'
    with pytest.raises(RuntimeError, match='cleanup incomplete') as failure:
        asyncio.run(environment.stop(delete=True))
    assert 'HTTP 500' in str(failure.value)
    assert 'fixture secret' not in str(failure.value)
    assert not inventory.exists()
    assert bool(controller.ranges) == (resource == 'range')
    assert bool(controller.removed_roles) == (resource != 'role')
    controller.fail_delete = controller.fail_role_remove = False
    asyncio.run(ludus_resources.recover(environment.resource_record))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert not controller.ranges
    assert record.state == 'absent'
    assert not record.roles


@pytest.mark.parametrize('recovery', [False, True])
def test_slow_synchronous_delete_completes_before_role_cleanup(
    environment: runner.LudusEnvironment,
    controller: Controller,
    recovery: bool,
) -> None:
    asyncio.run(environment.start(force_build=False))
    # A four-VM live range exceeded the old 15-second request timeout.
    controller.delete_response_delay = 20
    if recovery:
        asyncio.run(ludus_resources.recover(environment.resource_record))
    else:
        asyncio.run(environment.stop(delete=True))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'absent'
    assert not record.roles
    assert not controller.ranges
    assert not controller.installed_roles
    asyncio.run(environment.stop(delete=True))


def test_conflict_never_deletes_a_range_it_did_not_create(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.fail_create = True
    with pytest.raises(RuntimeError, match='HTTP 409'):
        asyncio.run(environment.start(force_build=False))
    assert 'DELETE range' not in controller.events


def test_lost_create_response_is_reconciled_by_ownership_marker(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.lost_create = True
    with pytest.raises(RuntimeError, match='timed out'):
        asyncio.run(environment.start(force_build=False))
    assert 'DELETE range' in controller.events
    assert not controller.ranges


def test_unobserved_create_preserves_recovery_intent(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.unobserved_create = True
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(environment.start(force_build=False))
    assert failure.value.__notes__
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'pending'
    controller.ranges[record.range_id] = {
        'rangeID': record.range_id,
        'name': record.name,
        'description': record.description,
    }
    asyncio.run(ludus_resources.recover(environment.resource_record))
    assert not controller.ranges


@pytest.mark.parametrize('wrong_identity', [False, True])
def test_cleanup_refuses_a_different_identity_or_ownership_marker(
    environment: runner.LudusEnvironment,
    controller: Controller,
    wrong_identity: bool,
) -> None:
    asyncio.run(environment.start(force_build=False))
    assert environment.range_id is not None
    if wrong_identity:
        controller.wrong_range = True
    else:
        controller.ranges[environment.range_id]['description'] = 'another owner'
    with pytest.raises(RuntimeError, match='cleanup incomplete'):
        asyncio.run(environment.stop(delete=True))
    assert 'DELETE range' not in controller.events
    assert controller.removed_roles


def test_keep_preserves_roles_and_allows_later_delete(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    asyncio.run(environment.start(force_build=False))
    asyncio.run(environment.stop(delete=False))
    assert 'PUT range/poweroff' in controller.events
    assert controller.ranges
    assert not controller.removed_roles
    request = next(item for item in controller.calls if item.url.path.endswith('/range/poweroff'))
    assert json.loads(request.content) == {'machines': ['all']}
    asyncio.run(environment.stop(delete=True))
    assert not controller.ranges
    assert controller.removed_roles


def test_cancellation_cleans_resources_and_local_secrets(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.cancel_deploy = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(environment.start(force_build=False))
    assert not controller.ranges
    assert controller.removed_roles
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'absent'
    assert not record.roles


def test_absent_pending_role_is_confirmed_and_removed_from_recovery_record(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    asyncio.run(environment.start(force_build=False))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    record.roles.append(f'scopebench_{record.range_id}_never_uploaded'.lower())
    record.save(environment.resource_record)
    asyncio.run(ludus_resources.recover(environment.resource_record))
    recovered = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert not recovered.roles
    assert recovered.state == 'absent'
    assert 'GET ansible' in controller.events
    asyncio.run(environment.stop(delete=True))


@pytest.mark.parametrize('shared_trial', [False, True])
def test_concurrent_environments_own_separate_ranges_roles_and_recovery_files(
    environment: runner.LudusEnvironment,
    controller: Controller,
    tmp_path: Path,
    shared_trial: bool,
) -> None:
    second = runner.LudusEnvironment(
        environment_dir=environment.environment_dir,
        environment_name='second',
        session_id='second-session',
        trial_paths=environment.trial_paths
        if shared_trial
        else TrialPaths(tmp_path / 'second-trial'),
        task_env_config=EnvironmentConfig(os=TaskOS.WINDOWS),
    )

    async def exercise() -> None:
        _ = await asyncio.gather(
            environment.start(force_build=False), second.start(force_build=False)
        )
        assert environment.range_id != second.range_id
        assert environment.resource_record != second.resource_record
        assert len(controller.installed_roles) == 2
        directories = {
            path
            for trial in {environment.trial_paths.trial_dir, second.trial_paths.trial_dir}
            for path in (trial / 'runtime-definitions').iterdir()
        }
        assert len(directories) == 2
        assert {runtime_digest(path) for path in directories} == {
            read_reference(environment.environment_dir).sha256,
        }
        record = second.resource_record.read_bytes()
        await environment.stop(delete=True)
        assert second.resource_record.read_bytes() == record
        assert set(controller.ranges) == {second.range_id}
        assert len(controller.installed_roles) == 1
        result = await second.exec('echo proof')
        assert result.return_code == 7
        await ludus_resources.recover(second.resource_record)
        assert not controller.ranges
        assert not controller.installed_roles
        await second.stop(delete=True)
        assert not controller.ranges
        assert not controller.installed_roles

    asyncio.run(exercise())


def test_failed_cleanup_preserves_the_provisioning_error(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    controller.state = 'ERROR'
    controller.fail_delete = True
    with pytest.raises(RuntimeError, match=': ERROR') as error:
        asyncio.run(environment.start(force_build=False))
    assert any('cleanup also failed (RuntimeError)' in note for note in error.value.__notes__)
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'owned'
    assert not record.roles
    controller.fail_delete = False
    asyncio.run(ludus_resources.recover(environment.resource_record))
    assert not controller.ranges


@pytest.mark.parametrize('delete_fails', [False, True])
def test_cleanup_reconciles_abort_failure_with_the_delete_outcome(
    environment: runner.LudusEnvironment,
    controller: Controller,
    delete_fails: bool,
) -> None:
    asyncio.run(environment.start(force_build=False))
    controller.state = 'DEPLOYING'
    controller.fail_abort = True
    controller.fail_delete = delete_fails
    if delete_fails:
        with pytest.raises(RuntimeError, match='cleanup incomplete') as error:
            asyncio.run(environment.stop(delete=True))
        assert 'abort ' in str(error.value)
        assert 'range ' in str(error.value)
    else:
        asyncio.run(environment.stop(delete=True))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == ('owned' if delete_fails else 'absent')
    assert not record.roles
    controller.fail_abort = controller.fail_delete = False
    asyncio.run(ludus_resources.recover(environment.resource_record))
    assert not controller.ranges


def test_cleanup_reconciles_a_previously_owned_range_that_is_already_gone(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    asyncio.run(environment.start(force_build=False))
    controller.ranges.clear()
    asyncio.run(environment.stop(delete=True))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'absent'
    assert not record.roles
    assert 'DELETE range' not in controller.events


def test_cleanup_keeps_role_identity_if_removal_and_reconciliation_both_fail(
    environment: runner.LudusEnvironment,
    controller: Controller,
) -> None:
    asyncio.run(environment.start(force_build=False))
    controller.fail_role_remove = controller.fail_role_lookup = True
    with pytest.raises(RuntimeError, match='cleanup incomplete'):
        asyncio.run(environment.stop(delete=True))
    record = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert record.state == 'absent'
    assert len(record.roles) == 1
    controller.fail_role_remove = controller.fail_role_lookup = False
    asyncio.run(ludus_resources.recover(environment.resource_record))
    recovered = LudusResources.model_validate_json(environment.resource_record.read_bytes())
    assert not recovered.roles

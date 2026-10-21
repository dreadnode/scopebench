"""Persist trial-owned resource identities and retry incomplete cleanup."""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from scopebench_contribution.schemas import Payload

from .client import LudusAPIError, LudusClient, validate_url

CLEANUP_TIMEOUT_SEC = 120
SNAPSHOT_TIMEOUT_SEC = 900


class LudusResources(Payload):
    """A credential-free recovery record; pending writes survive controller failure."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra='forbid', strict=True, hide_input_in_errors=True
    )
    url: str
    verify: bool = True
    range_id: str = Field(pattern=r'^SB[0-9a-f]{32}$')
    name: str
    state: Literal['absent', 'pending', 'owned'] = 'absent'
    roles: list[str] = Field(default_factory=list)
    cache_key: str | None = None
    snapshot_name: str | None = None
    snapshot_vmids: list[int] = Field(default_factory=list)
    snapshot_state: Literal['building', 'busy', 'resetting', 'ready', 'dirty'] | None = None
    testing_allowed_domains: list[str] | None = None
    testing_requested_domains: list[str] | None = None

    @model_validator(mode='after')
    def validate_record(self) -> Self:
        """Restrict cleanup to the record's trial namespace."""
        _ = validate_url(self.url)
        prefix = f'scopebench_{self.range_id}_'.lower()
        if any(not role.startswith(prefix) or not role.isidentifier() for role in self.roles):
            raise ValueError('Recovery role names must belong to this trial')
        if self.cache_key is not None and not re.fullmatch(r'[0-9a-f]{64}', self.cache_key):
            raise ValueError('Invalid cached range key')
        if self.snapshot_vmids and (
            len(set(self.snapshot_vmids)) != len(self.snapshot_vmids)
            or any(vmid <= 0 for vmid in self.snapshot_vmids)
        ):
            raise ValueError('Invalid cached snapshot VM IDs')
        return self

    @property
    def description(self) -> str:
        """Use the complete random trial ID as the server-side ownership marker."""
        return 'ScopeBench trial ' + self.range_id

    def save(self, path: Path) -> None:
        """Atomically save resource state without inventory or API credentials."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        _ = temporary.write_text(self.model_dump_json(indent=2) + '\n', encoding='utf-8')
        temporary.chmod(0o600)
        _ = temporary.replace(path)


def _range_failure(resources: LudusResources, error: LudusAPIError) -> list[str]:
    """Reconcile a confirmed missing range while retaining uncertain create intent."""
    if error.status_code == 404 and resources.state == 'owned':
        resources.state = 'absent'
        return []
    return [f'range {resources.range_id}: {error}']


async def _delete_range(
    client: LudusClient, resources: LudusResources, *, deploying: bool
) -> list[str]:
    """Abort an active deployment and delete its range, reconciling an uncertain abort."""
    failures: list[str] = []
    if deploying:
        try:
            await client.abort(resources.range_id)
        except LudusAPIError as error:
            failures.append(f'abort {resources.range_id}: {error}')
    try:
        await client.delete_range(resources.range_id)
    except LudusAPIError as error:
        return failures + _range_failure(resources, error)
    resources.state = 'absent'
    # Successful deletion also resolves a failed or uncertain abort.
    return []


async def _cleanup_range(
    client: LudusClient, resources: LudusResources, record: Path, *, delete: bool
) -> list[str]:
    """Verify ownership before deleting or powering off the recorded range."""
    if resources.state == 'absent':
        return []
    try:
        status = await client.status(resources.range_id)
    except LudusAPIError as error:
        return _range_failure(resources, error)
    if status.name != resources.name or status.description != resources.description:
        return ['range ownership ' + resources.range_id]
    resources.state = 'owned'
    resources.save(record)
    if delete:
        return await _delete_range(client, resources, deploying=status.state == 'DEPLOYING')
    try:
        await client.power_off(resources.range_id)
    except LudusAPIError as error:
        return _range_failure(resources, error)
    return []


async def _remove_role(client: LudusClient, role: str) -> str | None:
    """Treat an upload that is already absent as successfully removed."""
    try:
        await client.remove_role(role)
    except LudusAPIError as error:
        try:
            if not await client.role_exists(role):
                return None
        except LudusAPIError:
            pass
        return f'role {role}: {error}'
    return None


async def _cleanup_roles(client: LudusClient, resources: LudusResources, record: Path) -> list[str]:
    """Attempt every recorded role and persist each successful removal."""
    failures: list[str] = []
    for role in list(resources.roles):
        failure = await _remove_role(client, role)
        if failure is not None:
            failures.append(failure)
            continue
        resources.roles.remove(role)
        resources.save(record)
    return failures


async def cleanup_resources(
    client: LudusClient, resources: LudusResources, record: Path, *, delete: bool
) -> None:
    """Clean up the range and roles, retaining unresolved identities for recovery."""
    try:
        failures = await _cleanup_range(client, resources, record, delete=delete)
    finally:
        resources.save(record)
    if delete:
        failures.extend(await _cleanup_roles(client, resources, record))
    if failures:
        raise RuntimeError('Ludus cleanup incomplete: ' + ', '.join(failures) + f'; retry {record}')


async def recover(record: Path) -> None:
    """Delete only recorded trial resources using the current controller credential."""
    resources = LudusResources.model_validate_json(record.read_bytes())
    descriptor: int | None = None
    if resources.cache_key is not None:
        if record.name != f'{resources.cache_key}.json':
            raise ValueError('Cached Ludus record path differs from its identity')
        descriptor = os.open(
            record.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RuntimeError('This cached Ludus range is already in use') from None
    try:
        if descriptor is not None:
            locked = LudusResources.model_validate_json(record.read_bytes())
            if locked.cache_key != resources.cache_key:
                raise ValueError('Cached Ludus record changed while acquiring its lock')
            resources = locked
        timeout = SNAPSHOT_TIMEOUT_SEC if resources.cache_key is not None else CLEANUP_TIMEOUT_SEC
        client = LudusClient(
            resources.url,
            os.environ.get('LUDUS_API_KEY', ''),
            verify=resources.verify,
            timeout=timeout,
        )
        try:
            async with asyncio.timeout(timeout):
                await cleanup_resources(client, resources, record, delete=True)
            if (
                resources.cache_key is not None
                and resources.state == 'absent'
                and not resources.roles
            ):
                resources.snapshot_name = None
                resources.snapshot_vmids = []
                resources.snapshot_state = None
        finally:
            resources.save(record)
            await client.close()
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

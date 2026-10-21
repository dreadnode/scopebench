"""Create, deploy and release the complete range owned by one Harbor trial."""

import asyncio
import ipaddress
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from .client import LudusAPIError, LudusClient
from .config import ControllerSettings, LudusConfig
from .resources import CLEANUP_TIMEOUT_SEC, SNAPSHOT_TIMEOUT_SEC, LudusResources, cleanup_resources
from .roles import upload_roles


def _testing_domain_names(entries: list[str]) -> set[str]:
    """Ludus stores permitted domains as `name (pinned IP)` strings."""
    names: set[str] = set()
    for entry in entries:
        name, separator, address = entry.rpartition(' (')
        if separator:
            if not address.endswith(')'):
                raise RuntimeError('Ludus testing allowlist has a malformed domain entry')
            try:
                _ = ipaddress.ip_address(address[:-1])
            except ValueError:
                raise RuntimeError('Ludus testing allowlist has an invalid pinned IP') from None
            names.add(name)
        else:
            names.add(entry)
    return names


class TrialRange:
    """Own one range and its uploaded roles; persist intent before API writes."""

    def __init__(
        self,
        settings: ControllerSettings,
        config: LudusConfig,
        name: str,
        record: Path,
        logger: logging.Logger,
        resources: LudusResources | None = None,
        cache_key: str | None = None,
        task_variables: dict[str, str] | None = None,
    ) -> None:
        self.settings: ControllerSettings = settings
        self.config: LudusConfig = config
        self.record: Path = record
        self.logger: logging.Logger = logger
        self.task_variables: dict[str, str] = task_variables or {}
        self.resources: LudusResources = resources or LudusResources(
            url=settings.url,
            verify=settings.verify,
            range_id='SB' + uuid4().hex,
            name=name,
            cache_key=cache_key,
            snapshot_state='building' if cache_key else None,
        )
        self.resources.save(record)

    def _client(self, *, timeout: float = 120) -> LudusClient:
        return LudusClient(
            self.settings.url,
            self.settings.api_key.get_secret_value(),
            verify=self.settings.verify,
            timeout=timeout,
        )

    async def _save_deployment_log(self, client: LudusClient, range_id: str) -> None:
        """Keep the server's failure details before range cleanup removes them."""
        path = self.record.with_suffix('.deploy.log')
        try:
            content = await client.text('range/logs', range_id=range_id)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                os.fchmod(output.fileno(), 0o600)
                _ = output.write(content)
        except Exception as error:
            self.logger.warning('Could not save Ludus deployment log (%s)', type(error).__name__)
        else:
            self.logger.error('Ludus deployment log saved to %s', path)

    async def _wait(self, client: LudusClient, resources: LudusResources) -> None:
        deadline = time.monotonic() + self.settings.deploy_timeout
        previous: str | None = None
        async with asyncio.timeout(self.settings.deploy_timeout):
            while True:
                status = await client.status(
                    resources.range_id, timeout=min(120, max(0.001, deadline - time.monotonic()))
                )
                if status.name != resources.name or status.description != resources.description:
                    raise RuntimeError('Ludus range ownership marker differs')
                if status.state != previous:
                    self.logger.info('Ludus range %s: %s', resources.range_id, status.state)
                    previous = status.state
                if status.state == 'SUCCESS':
                    return
                if status.state in ('ERROR', 'ABORTED', 'DESTROYED', 'DESTROYING'):
                    await self._save_deployment_log(client, resources.range_id)
                    raise RuntimeError(f'Ludus range {resources.range_id}: {status.state}')
                await asyncio.sleep(min(5, max(0, deadline - time.monotonic())))

    async def deploy(self, definition: Path, directory: Path) -> str:
        """Deploy native YAML and roles, then return the range's Ansible inventory."""
        resources = self.resources
        client = self._client()
        try:
            _ = await client.text('')
            user = await client.current_user()
            resources.state = 'pending'
            resources.save(self.record)
            try:
                await client.create_range(
                    resources.range_id, resources.name, resources.description, user
                )
            except LudusAPIError as error:
                if error.status_code is not None and 400 <= error.status_code < 500:
                    resources.state = 'absent'
                    resources.save(self.record)
                raise
            resources.state = 'owned'
            resources.save(self.record)
            self.logger.info('Created Ludus range %s for %s', resources.range_id, resources.name)
            config = await upload_roles(
                client,
                definition,
                self.config,
                directory,
                resources,
                self.record,
                self.task_variables,
            )
            await client.configure_range(resources.range_id, config)
            await client.deploy(resources.range_id)
            await self._wait(client, resources)
            return await client.inventory(resources.range_id)
        finally:
            await client.close()

    async def cleanup(self, *, delete: bool) -> None:
        """Reconcile recorded resources within one overall deletion deadline."""
        timeout = (
            SNAPSHOT_TIMEOUT_SEC if self.resources.cache_key is not None else CLEANUP_TIMEOUT_SEC
        )
        async with self._client(timeout=timeout) as client:
            try:
                async with asyncio.timeout(timeout):
                    await cleanup_resources(client, self.resources, self.record, delete=delete)
            finally:
                self.resources.save(self.record)

    async def _snapshot_vms(self, client: LudusClient) -> set[int]:
        status = await client.status(self.resources.range_id)
        if status.name != self.resources.name or status.description != self.resources.description:
            raise RuntimeError('Ludus range ownership marker differs')
        if status.state != 'SUCCESS':
            raise RuntimeError(f'Cached Ludus range is not ready: {status.state}')
        vmids = {vm.vmid for vm in status.vms}
        if not vmids or len(vmids) != len(status.vms):
            raise RuntimeError('Ludus range has no complete VM inventory')
        return vmids

    async def create_snapshot(self, name: str) -> None:
        """Create and confirm the clean baseline across all range VMs."""
        async with self._client(timeout=SNAPSHOT_TIMEOUT_SEC) as client:
            vmids = await self._snapshot_vms(client)
            self.resources.snapshot_name = name
            self.resources.snapshot_vmids = sorted(vmids)
            self.resources.save(self.record)
            await client.create_snapshot(self.resources.range_id, name, vmids)
            listed = await client.snapshots(self.resources.range_id)
            if not all((vmid, name) in listed for vmid in vmids):
                raise RuntimeError('Ludus clean snapshot is missing from a range VM')

    async def rollback_snapshot(self) -> None:
        """Check ownership and coverage before reverting every VM."""
        name = self.resources.snapshot_name
        expected = set(self.resources.snapshot_vmids)
        if not name or not expected:
            raise RuntimeError('Cached Ludus range has no complete baseline snapshot')
        async with self._client(timeout=SNAPSHOT_TIMEOUT_SEC) as client:
            if await self._snapshot_vms(client) != expected:
                raise RuntimeError('Cached Ludus range VM inventory changed')
            listed = await client.snapshots(self.resources.range_id)
            if not all((vmid, name) in listed for vmid in expected):
                raise RuntimeError('Cached Ludus snapshot is missing from a range VM')
            await client.rollback_snapshot(self.resources.range_id, name, expected)

    async def power_on(self) -> None:
        """Start all VMs after restoring a disk-only snapshot."""
        async with self._client(timeout=SNAPSHOT_TIMEOUT_SEC) as client:
            await client.power_on(self.resources.range_id)
            await self._wait_power(client, powered_on=True)

    async def power_off(self) -> None:
        """Power down a verified cached range after rollback."""
        async with self._client(timeout=SNAPSHOT_TIMEOUT_SEC) as client:
            _ = await self._snapshot_vms(client)
            status = await client.status(self.resources.range_id)
            if any(vm.powered_on for vm in status.vms):
                await client.power_off(self.resources.range_id)
            await self._wait_power(client, powered_on=False)

    async def _wait_power(self, client: LudusClient, *, powered_on: bool) -> None:
        """Ludus power endpoints can return while the transition is in progress."""
        async with asyncio.timeout(600):
            while True:
                status = await client.status(self.resources.range_id)
                if (
                    status.name != self.resources.name
                    or status.description != self.resources.description
                ):
                    raise RuntimeError('Ludus range ownership marker differs')
                expected = set(self.resources.snapshot_vmids)
                if expected and {vm.vmid for vm in status.vms} != expected:
                    raise RuntimeError('Cached Ludus range VM inventory changed')
                if status.vms and all(vm.powered_on == powered_on for vm in status.vms):
                    return
                await asyncio.sleep(5)

    async def inventory(self) -> str:
        """Regenerate the connection inventory for a reused range."""
        async with self._client() as client:
            _ = await self._snapshot_vms(client)
            return await client.inventory(self.resources.range_id)

    async def range_number(self) -> int:
        """Read the owned range's network octet for a sandbox endpoint."""
        async with self._client() as client:
            status = await client.status(self.resources.range_id)
            if (
                status.name != self.resources.name
                or status.description != self.resources.description
                or status.range_number is None
                or not 1 <= status.range_number <= 254
            ):
                raise RuntimeError('Ludus range number or ownership marker is invalid')
            return status.range_number

    async def ensure_testing(self, domains: list[str]) -> None:
        """Require the range firewall before Harbor can hand control to an agent."""
        requested = sorted(domains)
        if (
            self.resources.testing_requested_domains is not None
            and self.resources.testing_requested_domains != requested
        ):
            raise RuntimeError('Cached Ludus testing policy changed; force_build is required')
        async with self._client(timeout=SNAPSHOT_TIMEOUT_SEC) as client:
            status = await client.status(self.resources.range_id)
            if (
                status.name != self.resources.name
                or status.description != self.resources.description
            ):
                raise RuntimeError('Ludus range ownership marker differs')
            if status.testing_enabled is None:
                raise RuntimeError('Ludus did not report testing mode status')
            if not status.testing_enabled:
                if self.resources.testing_allowed_domains is not None:
                    raise RuntimeError('Cached Ludus range left testing mode')
                await client.start_testing(self.resources.range_id)
                allowed = await client.allow_testing_domains(self.resources.range_id, domains)
                status = await client.status(self.resources.range_id)
                if status.allowed_domains is None or _testing_domain_names(
                    status.allowed_domains
                ) != set(allowed):
                    raise RuntimeError('Ludus testing mode does not match the required allowlist')
            if not status.testing_enabled or status.allowed_domains is None:
                raise RuntimeError('Ludus testing mode could not be verified')
            if status.allowed_ips or not set(domains).issubset(
                _testing_domain_names(status.allowed_domains)
            ):
                raise RuntimeError('Ludus testing mode does not match the required allowlist')
            expected = self.resources.testing_allowed_domains
            if expected is not None and set(status.allowed_domains) != set(expected):
                raise RuntimeError('Ludus testing allowlist changed outside the trial')
            if expected is None:
                self.resources.testing_allowed_domains = sorted(status.allowed_domains)
                self.resources.testing_requested_domains = requested
                self.resources.save(self.record)

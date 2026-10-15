"""Create, deploy and release the complete range owned by one Harbor trial."""

import asyncio
import logging
import time
from pathlib import Path
from uuid import uuid4

from .client import LudusAPIError, LudusClient
from .config import ControllerSettings, LudusConfig
from .resources import CLEANUP_TIMEOUT_SEC, LudusResources, cleanup_resources
from .roles import upload_roles


class TrialRange:
    """Own one range and its uploaded roles; persist intent before API writes."""

    def __init__(
        self,
        settings: ControllerSettings,
        config: LudusConfig,
        name: str,
        record: Path,
        logger: logging.Logger,
    ) -> None:
        self.settings: ControllerSettings = settings
        self.config: LudusConfig = config
        self.record: Path = record
        self.logger: logging.Logger = logger
        self.resources: LudusResources = LudusResources(
            url=settings.url,
            verify=settings.verify,
            range_id='SB' + uuid4().hex,
            name=name,
        )
        self.resources.save(record)

    def _client(self, *, timeout: float = 120) -> LudusClient:
        return LudusClient(
            self.settings.url,
            self.settings.api_key.get_secret_value(),
            verify=self.settings.verify,
            timeout=timeout,
        )

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
                client, definition, self.config, directory, resources, self.record
            )
            await client.configure_range(resources.range_id, config)
            await client.deploy(resources.range_id)
            await self._wait(client, resources)
            return await client.inventory(resources.range_id)
        finally:
            await client.close()

    async def cleanup(self, *, delete: bool) -> None:
        """Reconcile recorded resources within one overall deletion deadline."""
        async with self._client(timeout=CLEANUP_TIMEOUT_SEC) as client:
            try:
                async with asyncio.timeout(CLEANUP_TIMEOUT_SEC):
                    await cleanup_resources(client, self.resources, self.record, delete=delete)
            finally:
                self.resources.save(self.record)

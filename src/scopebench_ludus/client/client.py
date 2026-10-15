"""Small asynchronous Ludus 2.x API client, independent of Harbor and Ansible.

Endpoints and multipart formats follow Ludus's OpenAPI specification and CLI:
https://github.com/badsectorlabs/ludus/blob/main/docs/static/api/ludus.openapi.yaml
Range operations deliberately require an explicit range ID.
"""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from .models import AnsibleItem, LudusAPIError, RangeStatus


def validate_url(url: str) -> str:
    """Require HTTPS without credentials, query parameters, or fragments."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != 'https'
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('Expected an HTTPS Ludus URL without embedded credentials')
    return url.rstrip('/')


def range_params(range_id: str) -> dict[str, str]:
    """Prevent accidental requests against the user's default range."""
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', range_id) is None:
        raise ValueError('An explicit Ludus range ID is required')
    return {'rangeID': range_id}


class LudusClient:
    """Use HTTP status codes and validated JSON for the required Ludus endpoints."""

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        verify: bool = True,
        timeout: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = validate_url(url)
        if not api_key:
            raise ValueError('A Ludus API key is required')
        self._timeout: float = timeout
        base = url if url.endswith('/api/v2') else url + '/api/v2'
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            base_url=base + '/',
            headers={'X-API-KEY': api_key},
            verify=verify,
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )

    async def __aenter__(self) -> LudusClient:
        """Open a client scope."""
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Release HTTP connections on success, failure, or cancellation."""
        await self.close()

    async def close(self) -> None:
        """Close controller connections even after a failed operation."""
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, JsonValue] | None = None,
        file: tuple[str, bytes, str] | None = None,
        form: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> JsonValue:
        """Make one request; never retry a potentially completed mutation blindly."""
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or any(part == '..' for part in path.split('/')):
            raise ValueError('API paths must be relative to the configured Ludus server')
        path = path.lstrip('/')
        if path == 'range' or path.startswith('range/'):
            _ = range_params((params or {}).get('rangeID', ''))
        operation = f'{method} /{path}'
        try:
            response = await self._http.request(
                method,
                path.lstrip('/'),
                params=params,
                json=body,
                files={'file': file} if file else None,
                data=form,
                timeout=self._timeout if timeout is None else timeout,
            )
        except httpx.RequestError:
            raise LudusAPIError(operation + ' failed or timed out; check resource state') from None
        if not response.is_success:
            raise LudusAPIError(operation + ' failed', response.status_code)
        try:
            data: JsonValue = (
                TypeAdapter[JsonValue](JsonValue).validate_json(response.content)
                if response.content
                else {}
            )
        except ValidationError:
            raise LudusAPIError(operation + ' returned invalid JSON') from None
        if isinstance(data, dict) and (
            data.get('error') or data.get('errors') or data.get('success') is False
        ):
            raise LudusAPIError(operation + ' returned an API error', response.status_code)
        return data

    async def text(self, path: str, *, range_id: str | None = None) -> str:
        """Read a JSON result envelope such as version or exported inventory."""
        data = await self.request(
            'GET', path, params=range_params(range_id) if range_id is not None else None
        )
        value = data.get('result') if isinstance(data, dict) else None
        if not isinstance(value, str):
            raise LudusAPIError(f'GET {path} returned an invalid result')
        return value

    async def current_user(self) -> str:
        """Resolve the authenticated user without deriving identity from a credential."""
        data = await self.request('GET', 'user')
        if (
            not isinstance(data, list)
            or len(data) != 1
            or not isinstance(data[0], dict)
            or not isinstance(data[0].get('userID'), str)
        ):
            raise LudusAPIError('GET user returned an invalid identity')
        identity = data[0]['userID']
        assert isinstance(identity, str)
        return identity

    async def create_range(self, range_id: str, name: str, description: str, user: str) -> None:
        """Create and assign a new range without changing the default range."""
        _ = range_params(range_id)
        _ = await self.request(
            'POST',
            'ranges/create',
            body={'rangeID': range_id, 'name': name, 'description': description, 'userID': [user]},
        )

    async def status(self, range_id: str, *, timeout: float | None = None) -> RangeStatus:
        """Read the specified range and fail closed on an unexpected identity."""
        data = await self.request('GET', 'range', params=range_params(range_id), timeout=timeout)
        try:
            status = RangeStatus.model_validate(data)
        except ValidationError:
            raise LudusAPIError('GET range returned an invalid status') from None
        if status.range_id != range_id:
            raise LudusAPIError('GET range returned a different range identity')
        return status

    async def configure_range(self, range_id: str, content: bytes) -> None:
        """Upload native Ludus YAML without translating its configuration schema."""
        _ = await self.request(
            'PUT',
            'range/config',
            params=range_params(range_id),
            file=('range-config.yml', content, 'application/yaml'),
            form={'force': 'false'},
        )

    async def deploy(self, range_id: str) -> None:
        """Deploy all VMs and native roles in the specified range."""
        _ = await self.request('POST', 'range/deploy', params=range_params(range_id), body={})

    async def inventory(self, range_id: str) -> str:
        """Export inventory for exactly one range, including connection variables."""
        return await self.text('range/ansibleinventory', range_id=range_id)

    async def abort(self, range_id: str) -> None:
        """Stop a deployment before deleting its range."""
        _ = await self.request('POST', 'range/abort', params=range_params(range_id), body={})

    async def delete_range(self, range_id: str) -> None:
        """Remove the range object and its VMs, rather than only wiping its VMs."""
        _ = await self.request(
            'DELETE', 'range', params={**range_params(range_id), 'force': 'true'}
        )

    async def power_off(self, range_id: str) -> None:
        """Power off every VM in the specified range."""
        _ = await self.request(
            'PUT',
            'range/poweroff',
            params=range_params(range_id),
            body={'machines': ['all']},
        )

    async def upload_role(self, name: str, directory: Path) -> None:
        """Upload a user-scoped role under the supplied name."""
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            archive.add(directory, arcname=name)
        _ = await self.request(
            'PUT',
            'ansible/role/fromtar',
            file=(name, stream.getvalue(), 'application/x-tar'),
            form={'force': 'false', 'global': 'false'},
        )

    async def remove_role(self, name: str) -> None:
        """Remove one user-scoped role."""
        _ = await self.request(
            'POST',
            'ansible/role',
            body={'role': name, 'action': 'remove', 'global': False},
        )

    async def role_exists(self, name: str) -> bool:
        """Reconcile removals; Ludus can report an absent role as 409 or 500."""
        data = await self.request('GET', 'ansible')
        try:
            items = TypeAdapter(list[AnsibleItem]).validate_python(data)
        except ValidationError:
            raise LudusAPIError('GET ansible returned invalid role information') from None
        return any(
            item.name == name and item.type == 'role' and not item.global_scope for item in items
        )

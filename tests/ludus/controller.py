"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import final

import httpx
import yaml
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from scopebench_ludus.ansible.process import ProcessResult
from scopebench_ludus.client import LudusClient

type Document = dict[str, JsonValue]
DOCUMENT = TypeAdapter[Document](Document)


class AllowDomainsBody(BaseModel):
    """Allowlist request issued by the Ludus client."""

    domains: list[str]


class RemoveRoleBody(BaseModel):
    """Role removal request issued by the Ludus client."""

    action: str
    role: str


class CreateRangeBody(BaseModel):
    """Range creation request issued by the Ludus client."""

    range_id: str = Field(alias='rangeID')
    name: str
    description: str
    user_ids: list[str] = Field(alias='userID')


class SnapshotBody(BaseModel):
    """Snapshot mutation request issued by the Ludus client."""

    name: str
    vmids: list[int]
    include_ram: bool | None = Field(default=None, alias='includeRAM')


class RequestTimeout(BaseModel):
    """HTTPX's timeout extension used by the delayed-response fixture."""

    read: float | None


class Play(BaseModel):
    """Ansible play whose task list the transport fixture inspects."""

    tasks: list[Document]


class CopyTask(BaseModel):
    """Ansible copy task used by the transport fixture."""

    content: str
    dest: str


class TransportLoader(yaml.SafeLoader):
    """Read transport literals without evaluating expressions."""


def load_literal(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


def uploaded_file(request: httpx.Request) -> bytes:
    """Decode the actual HTTP multipart upload independently of the client."""
    message = BytesParser().parsebytes(
        f'Content-Type: {request.headers["content-type"]}\r\n\r\n'.encode() + request.content
    )
    parts = TypeAdapter(list[object]).validate_python(message.get_payload())
    for part in parts:
        if not isinstance(part, Message):
            raise AssertionError('Expected multipart section')
        if part.get_param('name', header='content-disposition') == 'file':
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload
            raise AssertionError('Expected binary upload')
    raise AssertionError('No multipart file')


TransportLoader.add_constructor('!unsafe', load_literal)


@final
class Controller:
    """Mock the HTTP server and Ansible results at their external boundaries."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.events: list[str] = []
        self.processes: list[list[str]] = []
        self.plays: list[list[Document]] = []
        self.environments: list[dict[str, str]] = []
        self.raw_playbooks: list[str] = []
        self.state = 'SUCCESS'
        self.fail_create = False
        self.lost_create = False
        self.unobserved_create = False
        self.fail_delete = False
        self.fail_abort = False
        self.fail_role_lookup = False
        self.delete_response_delay = 0.0
        self.fail_play = False
        self.wrong_range = False
        self.fail_role = False
        self.fail_role_remove = False
        self.cancel_deploy = False
        self.windows = True
        self.selected_hosts = ['executor']
        self.ranges: dict[str, dict[str, str]] = {}
        self.range_configs: list[Document] = []
        self.role_tasks: list[str] = []
        self.role_metadata: list[str] = []
        self.removed_roles: list[str] = []
        self.installed_roles: set[str] = set()
        self.entries: list[dict[str, object]] = []
        self.snapshots: dict[str, set[tuple[int, str]]] = {}
        self.powered_on: dict[str, bool] = {}
        self.testing: dict[str, bool] = {}
        self.allowed_domains: dict[str, list[str]] = {}
        self.fail_snapshot = False
        self.fail_rollback = False
        self.partial_testing_allow = False

    def request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path.removeprefix('/api/v2/')
        self.events.append(request.method + ' ' + path)
        assert request.headers['X-API-KEY'] == 'controller-only-secret'
        if path == '':
            return httpx.Response(200, json={'result': 'Ludus Server 2.3.5'})
        if path == 'user':
            return httpx.Response(200, json=[{'userID': 'fixture'}])
        if path == 'ranges/create':
            return self.create_range(request)
        elif path == 'range':
            return self.range_request(request)
        elif path in ('range/config', 'range/logs'):
            return self.range_file(request, path)
        elif path == 'range/deploy' and self.cancel_deploy:
            raise asyncio.CancelledError
        elif path == 'ansible/role/fromtar':
            return self.upload_role(request)
        elif path == 'range/abort' and self.fail_abort:
            return httpx.Response(500, json={'error': 'fixture secret'})
        elif path in ('range/poweroff', 'range/poweron'):
            self.powered_on[request.url.params['rangeID']] = path == 'range/poweron'
        elif path.startswith('testing/'):
            return self.testing_request(request, path)
        elif path.startswith('snapshots/'):
            return self.snapshot_request(request, path)
        elif path == 'ansible':
            return self.list_roles()
        elif path == 'ansible/role':
            return self.remove_role(request)
        elif path == 'range/ansibleinventory':
            connection = 'winrm' if self.windows else 'ssh'
            return httpx.Response(
                200,
                json={
                    'result': f'all:\n  hosts:\n    executor:\n      ansible_connection: {connection}\n      ansible_password: fixture\n'
                },
            )
        return httpx.Response(201, json={'result': 'ok'})

    def range_file(self, request: httpx.Request, path: str) -> httpx.Response:
        """Store range configuration or return its deployment log."""
        if path == 'range/logs':
            return httpx.Response(
                200, json={'result': 'TASK [fixture]\nfatal: fixture deployment error\n'}
            )
        self.range_configs.append(DOCUMENT.validate_python(yaml.safe_load(uploaded_file(request))))
        return httpx.Response(201, json={'result': 'ok'})

    def testing_request(self, request: httpx.Request, path: str) -> httpx.Response:
        identity = request.url.params['rangeID']
        if path == 'testing/start':
            self.testing[identity] = True
            return httpx.Response(201, json={'result': 'ok'})
        assert path == 'testing/allow'
        domains = AllowDomainsBody.model_validate_json(request.content).domains
        accepted = domains[:-1] if self.partial_testing_allow else domains
        self.allowed_domains[identity] = [f'{domain} (203.0.113.10)' for domain in accepted]
        return httpx.Response(200, json={'allowed': domains, 'errors': []})

    def range_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == 'GET':
            return self.status(request)
        assert request.method == 'DELETE'
        assert request.url.params['force'] == 'true'
        if self.fail_delete:
            return httpx.Response(500, json={'error': 'fixture secret'})
        del self.ranges[request.url.params['rangeID']]
        _ = self.snapshots.pop(request.url.params['rangeID'], None)
        _ = self.powered_on.pop(request.url.params['rangeID'], None)
        _ = self.testing.pop(request.url.params['rangeID'], None)
        _ = self.allowed_domains.pop(request.url.params['rangeID'], None)
        # Model a completed synchronous deletion whose response arrives late.
        read_timeout = RequestTimeout.model_validate(request.extensions['timeout']).read
        if read_timeout is not None and read_timeout < self.delete_response_delay:
            raise httpx.ReadTimeout('Delete response did not arrive in time', request=request)
        return httpx.Response(201, json={'result': 'ok'})

    def list_roles(self) -> httpx.Response:
        if self.fail_role_lookup:
            return httpx.Response(500, json={'error': 'fixture secret'})
        return httpx.Response(
            200,
            json=[{'name': name, 'type': 'role', 'global': False} for name in self.installed_roles],
        )

    def remove_role(self, request: httpx.Request) -> httpx.Response:
        data = RemoveRoleBody.model_validate_json(request.content)
        assert data.action == 'remove'
        if self.fail_role_remove:
            return httpx.Response(500, json={'error': 'fixture secret'})
        if data.role not in self.installed_roles:
            return httpx.Response(409, json={'error': 'role not installed'})
        self.removed_roles.append(data.role)
        self.installed_roles.discard(data.role)
        return httpx.Response(201, json={'result': 'removed'})

    def status(self, request: httpx.Request) -> httpx.Response:
        identity = request.url.params['rangeID']
        if identity not in self.ranges:
            return httpx.Response(404, json={'error': 'not found'})
        data = {
            **self.ranges[identity],
            'rangeState': self.state,
            'testingEnabled': self.testing.get(identity, False),
            'allowedDomains': self.allowed_domains.get(identity, []),
            'allowedIPs': None,
            'VMs': [
                {'proxmoxID': vmid, 'poweredOn': self.powered_on.get(identity, True)}
                for vmid in (101, 102)
            ],
        }
        if self.wrong_range:
            data['rangeID'] = 'unrelated'
        return httpx.Response(200, json=data)

    def create_range(self, request: httpx.Request) -> httpx.Response:
        data = CreateRangeBody.model_validate_json(request.content)
        assert data.user_ids == ['fixture']
        if self.fail_create:
            return httpx.Response(409, json={'error': 'fixture secret'})
        if not self.unobserved_create:
            self.ranges[data.range_id] = {
                'rangeID': data.range_id,
                'name': data.name,
                'description': data.description,
            }
            self.powered_on[data.range_id] = True
        if self.lost_create or self.unobserved_create:
            raise httpx.ReadTimeout('fixture secret', request=request)
        return httpx.Response(201, json={'result': 'ok'})

    def snapshot_create(self, request: httpx.Request) -> httpx.Response:
        identity = request.url.params['rangeID']
        body = SnapshotBody.model_validate_json(request.content)
        assert body.include_ram is False
        vmids = body.vmids
        success = vmids[:-1] if self.fail_snapshot else vmids
        self.snapshots.setdefault(identity, set()).update((vmid, body.name) for vmid in success)
        result: dict[str, object] = {'success': success}
        if self.fail_snapshot:
            result['errors'] = [{'vmid': vmids[-1], 'error': 'failed'}]
        return httpx.Response(200, json=result)

    def snapshot_request(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == 'snapshots/create':
            return self.snapshot_create(request)
        if path == 'snapshots/rollback':
            return self.snapshot_rollback(request)
        assert path == 'snapshots/list'
        identity = request.url.params['rangeID']
        return httpx.Response(
            200,
            json={
                'snapshots': [
                    {'vmid': vmid, 'name': name}
                    for vmid, name in self.snapshots.get(identity, set())
                ],
            },
        )

    def snapshot_rollback(self, request: httpx.Request) -> httpx.Response:
        identity = request.url.params['rangeID']
        body = SnapshotBody.model_validate_json(request.content)
        vmids = body.vmids
        if any((vmid, body.name) not in self.snapshots.get(identity, set()) for vmid in vmids):
            return httpx.Response(404, json={'error': 'missing snapshot'})
        if self.fail_rollback:
            return httpx.Response(
                200,
                json={'success': vmids[:-1], 'errors': [{'vmid': vmids[-1], 'error': 'failed'}]},
            )
        self.powered_on[identity] = False
        return httpx.Response(200, json={'success': vmids})

    def upload_role(self, request: httpx.Request) -> httpx.Response:
        with tarfile.open(fileobj=io.BytesIO(uploaded_file(request))) as archive:
            self.installed_roles.add(archive.getmembers()[0].name.split('/')[0])
            tasks = next(
                member for member in archive.getmembers() if member.name.endswith('/tasks/main.yml')
            )
            content = archive.extractfile(tasks)
            assert content is not None
            self.role_tasks.append(content.read().decode())
            meta = next(
                member for member in archive.getmembers() if member.name.endswith('/meta/main.yml')
            )
            content = archive.extractfile(meta)
            assert content is not None
            self.role_metadata.append(content.read().decode())
        if self.fail_role:
            return httpx.Response(500, json={'error': 'fixture secret'})
        return httpx.Response(201, json={'result': 'ok'})

    def client(self, url: str, api_key: str, *, verify: bool, timeout: float = 120) -> LudusClient:
        return LudusClient(
            url,
            api_key,
            verify=verify,
            timeout=timeout,
            transport=httpx.MockTransport(self.request),
        )

    async def run(
        self,
        command: list[str],
        *,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> ProcessResult:
        _ = timeout
        self.processes.append(command)
        self.environments.append(env or {})
        if command[0] == 'ansible-inventory':
            variables = {'executor': {'ansible_connection': 'winrm' if self.windows else 'ssh'}}
            return ProcessResult(
                return_code=0,
                stderr='',
                stdout=json.dumps(
                    {
                        '_meta': {'hostvars': variables},
                        'scopebench': {'hosts': self.selected_hosts},
                    }
                ),
            )
        self.events.append('transport')
        raw = Path(command[-1]).read_text(encoding='utf-8')
        self.raw_playbooks.append(raw)
        if self.fail_play and '--syntax-check' not in command:
            return ProcessResult(stdout='', stderr='fixture secret', return_code=2)
        plays = TypeAdapter(list[Play]).validate_python(yaml.load(raw, Loader=TransportLoader))
        tasks = plays[0].tasks
        self.plays.append(tasks)
        for task in tasks:
            if 'ansible.builtin.copy' in task and 'delegate_to' in task:
                copy = CopyTask.model_validate(task['ansible.builtin.copy'])
                payload: object = (
                    self.entries
                    if 'scopebench_files.files' in copy.content
                    else {
                        'rc': 7,
                        'stdout': 'fixture stdout\r\n',
                        'stderr': 'fixture stderr',
                    }
                )
                _ = Path(copy.dest).write_text(json.dumps(payload), encoding='utf-8')
        return ProcessResult(stdout='PLAY RECAP', stderr='', return_code=0)

    @property
    def inventory(self) -> Path:
        command = next(call for call in self.processes if call[0] == 'ansible-inventory')
        return Path(command[command.index('-i') + 1])

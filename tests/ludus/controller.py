"""Exercise generic range, HTTP ownership, and Ansible transport contracts."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import cast, final

import httpx
import yaml

from scopebench_ludus.ansible.process import ProcessResult
from scopebench_ludus.client import LudusClient


class TransportLoader(yaml.SafeLoader):
    """Read transport literals without evaluating expressions."""


def load_literal(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    return loader.construct_scalar(node)


def uploaded_file(request: httpx.Request) -> bytes:
    """Decode the actual HTTP multipart upload independently of the client."""
    message = BytesParser().parsebytes(
        f'Content-Type: {request.headers["content-type"]}\r\n\r\n'.encode() + request.content
    )
    parts = cast(list[Message], message.get_payload())
    for part in parts:
        if part.get_param('name', header='content-disposition') == 'file':
            return cast(bytes, part.get_payload(decode=True))
    raise AssertionError('No multipart file')


TransportLoader.add_constructor('!unsafe', load_literal)


@final
class Controller:
    """Mock the HTTP server and Ansible results at their external boundaries."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.events: list[str] = []
        self.processes: list[list[str]] = []
        self.plays: list[list[dict[str, object]]] = []
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
        self.range_configs: list[dict[str, object]] = []
        self.role_tasks: list[str] = []
        self.role_metadata: list[str] = []
        self.removed_roles: list[str] = []
        self.installed_roles: set[str] = set()
        self.entries: list[dict[str, object]] = []

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
        elif path == 'range' and request.method == 'GET':
            return self.status(request)
        elif path == 'range' and request.method == 'DELETE':
            assert request.url.params['force'] == 'true'
            if self.fail_delete:
                return httpx.Response(500, json={'error': 'fixture secret'})
            del self.ranges[request.url.params['rangeID']]
            # Model a completed synchronous deletion whose response arrives late.
            timeouts = cast(dict[str, float | None], request.extensions['timeout'])
            read_timeout = timeouts['read']
            if read_timeout is not None and read_timeout < self.delete_response_delay:
                raise httpx.ReadTimeout('Delete response did not arrive in time', request=request)
        elif path == 'range/config':
            self.range_configs.append(
                cast(dict[str, object], yaml.safe_load(uploaded_file(request)))
            )
        elif path == 'range/deploy' and self.cancel_deploy:
            raise asyncio.CancelledError
        elif path == 'ansible/role/fromtar':
            return self.upload_role(request)
        elif path == 'range/abort' and self.fail_abort:
            return httpx.Response(500, json={'error': 'fixture secret'})
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

    def list_roles(self) -> httpx.Response:
        if self.fail_role_lookup:
            return httpx.Response(500, json={'error': 'fixture secret'})
        return httpx.Response(
            200,
            json=[{'name': name, 'type': 'role', 'global': False} for name in self.installed_roles],
        )

    def remove_role(self, request: httpx.Request) -> httpx.Response:
        data = cast(dict[str, str], json.loads(request.content))
        assert data['action'] == 'remove'
        if self.fail_role_remove:
            return httpx.Response(500, json={'error': 'fixture secret'})
        if data['role'] not in self.installed_roles:
            return httpx.Response(409, json={'error': 'role not installed'})
        self.removed_roles.append(data['role'])
        self.installed_roles.discard(data['role'])
        return httpx.Response(201, json={'result': 'removed'})

    def status(self, request: httpx.Request) -> httpx.Response:
        identity = request.url.params['rangeID']
        if identity not in self.ranges:
            return httpx.Response(404, json={'error': 'not found'})
        data = {**self.ranges[identity], 'rangeState': self.state}
        if self.wrong_range:
            data['rangeID'] = 'unrelated'
        return httpx.Response(200, json=data)

    def create_range(self, request: httpx.Request) -> httpx.Response:
        data = cast(dict[str, str], json.loads(request.content))
        assert data['userID'] == ['fixture']
        if self.fail_create:
            return httpx.Response(409, json={'error': 'fixture secret'})
        if not self.unobserved_create:
            self.ranges[data['rangeID']] = {
                'rangeID': data['rangeID'],
                'name': data['name'],
                'description': data['description'],
            }
        if self.lost_create or self.unobserved_create:
            raise httpx.ReadTimeout('fixture secret', request=request)
        return httpx.Response(201, json={'result': 'ok'})

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
        plays = cast(list[dict[str, object]], yaml.load(raw, Loader=TransportLoader))
        tasks = cast(list[dict[str, object]], plays[0]['tasks'])
        self.plays.append(tasks)
        for task in tasks:
            if 'ansible.builtin.copy' in task and 'delegate_to' in task:
                copy = cast(dict[str, str], task['ansible.builtin.copy'])
                payload: object = (
                    self.entries
                    if 'scopebench_files.files' in copy['content']
                    else {
                        'rc': 7,
                        'stdout': 'fixture stdout\r\n',
                        'stderr': 'fixture stderr',
                    }
                )
                _ = Path(copy['dest']).write_text(json.dumps(payload), encoding='utf-8')
        return ProcessResult(stdout='PLAY RECAP', stderr='', return_code=0)

    @property
    def inventory(self) -> Path:
        command = next(call for call in self.processes if call[0] == 'ansible-inventory')
        return Path(command[command.index('-i') + 1])

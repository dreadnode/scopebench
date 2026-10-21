"""Validate API boundaries without infrastructure or controller credentials."""

import asyncio

import httpx
import pytest

from scopebench_ludus.client import LudusAPIError, LudusClient


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(403, json={'error': 'secret-password'}),
        httpx.Response(200, json={'errors': ['secret-password']}),
        httpx.Response(200, json={'success': False, 'result': 'secret-password'}),
        httpx.Response(200, text='invalid JSON secret-password'),
    ],
)
def test_api_failures_never_expose_response_bodies(response: httpx.Response) -> None:
    def request(_request: httpx.Request) -> httpx.Response:
        return response

    async def exercise() -> None:
        client = LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        )
        try:
            with pytest.raises(LudusAPIError, match='Ludus') as failure:
                _ = await client.request('GET', '')
            assert 'secret-password' not in str(failure.value)
            assert failure.value.__cause__ is None
        finally:
            await client.close()

    asyncio.run(exercise())


def test_range_requests_require_an_explicit_valid_id() -> None:
    def unexpected(_request: httpx.Request) -> httpx.Response:
        raise AssertionError('An unscoped range request reached the server')

    async def exercise() -> None:
        client = LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(unexpected)
        )
        try:
            for identity in ('', '../range', 'default?rangeID=other'):
                with pytest.raises(ValueError, match='explicit Ludus range ID'):
                    _ = await client.inventory(identity)
            with pytest.raises(ValueError, match='explicit Ludus range ID'):
                _ = await client.request('DELETE', 'range')
        finally:
            await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize('path', ['https://other.test/user', '//other.test/user', '../user'])
def test_raw_requests_cannot_send_the_key_to_another_server(path: str) -> None:
    def unexpected(_request: httpx.Request) -> httpx.Response:
        raise AssertionError('An out-of-scope request reached HTTP')

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(unexpected)
        ) as client:
            with pytest.raises(ValueError, match='relative'):
                _ = await client.request('GET', path)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    'payload',
    [
        {'rangeID': 'OTHER', 'rangeState': 'SUCCESS', 'name': 'n', 'description': 'd'},
        {'rangeID': 'TEST', 'rangeState': 42, 'name': 'n', 'description': 'd'},
        {'rangeID': 'TEST', 'rangeState': 'SUCCESS'},
    ],
)
def test_status_rejects_unexpected_or_invalid_range_identity(payload: dict[str, object]) -> None:
    def request(request: httpx.Request) -> httpx.Response:
        assert request.url.params['rangeID'] == 'TEST'
        return httpx.Response(200, json=payload)

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        ) as client:
            with pytest.raises(LudusAPIError, match='range'):
                _ = await client.status('TEST')

    asyncio.run(exercise())


def test_range_endpoints_match_the_ludus_v2_wire_contract() -> None:
    import json

    calls: list[httpx.Request] = []

    def request(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers['X-API-KEY'] == 'secret-key'
        path = request.url.path
        if path.endswith('/user'):
            return httpx.Response(200, json=[{'userID': 'JD'}])
        if path.endswith('/ansibleinventory'):
            return httpx.Response(200, json={'result': 'all:\n  hosts: {}'})
        if path.endswith('/range') and request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'rangeID': 'TEST',
                    'rangeState': 'SUCCESS',
                    'name': 'test',
                    'description': 'test',
                },
            )
        return httpx.Response(201, json={'result': 'ok'})

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        ) as client:
            assert await client.current_user() == 'JD'
            await client.create_range('TEST', 'test', 'test', 'JD')
            await client.configure_range('TEST', b'ludus: []\n')
            await client.deploy('TEST')
            assert (await client.status('TEST')).state == 'SUCCESS'
            assert await client.inventory('TEST') == 'all:\n  hosts: {}'
            await client.abort('TEST')
            await client.power_off('TEST')
            await client.delete_range('TEST')

    asyncio.run(exercise())
    assert [(r.method, r.url.path) for r in calls] == [
        ('GET', '/api/v2/user'),
        ('POST', '/api/v2/ranges/create'),
        ('PUT', '/api/v2/range/config'),
        ('POST', '/api/v2/range/deploy'),
        ('GET', '/api/v2/range'),
        ('GET', '/api/v2/range/ansibleinventory'),
        ('POST', '/api/v2/range/abort'),
        ('PUT', '/api/v2/range/poweroff'),
        ('DELETE', '/api/v2/range'),
    ]
    assert json.loads(calls[1].content) == {
        'rangeID': 'TEST',
        'name': 'test',
        'description': 'test',
        'userID': ['JD'],
    }
    assert all(r.url.params['rangeID'] == 'TEST' for r in calls[2:])
    assert calls[-1].url.params['force'] == 'true'
    assert b'ludus: []\n' in calls[2].content
    assert json.loads(calls[-2].content) == {'machines': ['all']}


def test_transport_errors_hide_secrets_and_do_not_retry_mutations() -> None:
    calls = 0

    def request(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout('secret-key secret-password', request=request)

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        ) as client:
            with pytest.raises(LudusAPIError) as failure:
                await client.deploy('TEST')
            assert 'secret' not in str(failure.value)
            assert failure.value.__cause__ is None

    asyncio.run(exercise())
    assert calls == 1


def test_testing_mode_requests_are_scoped_to_the_range() -> None:
    calls: list[httpx.Request] = []

    def request(item: httpx.Request) -> httpx.Response:
        calls.append(item)
        if item.url.path.endswith('/testing/allow'):
            return httpx.Response(200, json={'allowed': ['api.anthropic.com'], 'errors': []})
        return httpx.Response(201, json={'result': 'ok'})

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        ) as client:
            await client.start_testing('TEST')
            await client.allow_testing_domains('TEST', ['api.anthropic.com'])

    asyncio.run(exercise())
    assert [(r.method, r.url.path) for r in calls] == [
        ('PUT', '/api/v2/testing/start'),
        ('POST', '/api/v2/testing/allow'),
    ]
    assert all(r.url.params['rangeID'] == 'TEST' for r in calls)


def test_testing_mode_tolerates_duplicate_certificate_domain_errors() -> None:
    def request(_item: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'allowed': ['api.anthropic.com'],
                'errors': [{'item': 'ocsp.example.test', 'reason': 'already allowed'}],
            },
        )

    async def exercise() -> None:
        async with LudusClient(
            'https://example.test', 'secret-key', transport=httpx.MockTransport(request)
        ) as client:
            await client.allow_testing_domains('TEST', ['api.anthropic.com'])

    asyncio.run(exercise())

"""Check that Mythic v3 callback readiness requires an Apollo task response."""

from __future__ import annotations

import asyncio

import pytest
from mythic import mythic
from mythic.mythic_classes import Mythic
from pydantic import ValidationError

from scopebench_ludus import apollo_probe


def _row(
    callback_id: int,
    *,
    host: str = 'BRK-PROD01',
    user: str = 'review.operator',
    payload_type: str = 'apollo',
) -> apollo_probe.Callback:
    return apollo_probe.Callback(
        id=callback_id,
        display_id=callback_id,
        host=host,
        user=user,
        payload=apollo_probe.Payload(
            payloadtype=apollo_probe.PayloadType(name=payload_type),
        ),
    )


def test_selects_only_apollo_callbacks_newest_first() -> None:
    rows = [_row(4), _row(9, payload_type='other'), _row(8)]
    assert apollo_probe.callback_ids(rows) == [8, 4]


def test_rejects_incomplete_callback_data() -> None:
    with pytest.raises(ValidationError):
        _ = apollo_probe.Callback.model_validate({'id': 1, 'display_id': 1})


def test_readiness_requires_task_output(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Mythic()
    outputs = iter((b'  ', b'BRK-PROD01\\review.operator\r\n'))

    async def task(
        actual_session: Mythic,
        *,
        command_name: str,
        parameters: dict[str, str],
        callback_display_id: int,
        timeout: int,
    ) -> bytes:
        assert actual_session is session
        assert command_name == 'run'
        assert parameters == {'executable': 'whoami.exe', 'arguments': ''}
        assert callback_display_id == 9
        assert timeout == 45
        return next(outputs)

    monkeypatch.setattr(mythic, 'issue_task_and_waitfor_task_output', task)
    assert not asyncio.run(apollo_probe.prove_callback(session, 9))
    assert asyncio.run(apollo_probe.prove_callback(session, 9))


def test_retires_only_stale_callbacks_for_working_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mythic()
    rows = [
        _row(1),
        _row(2),
        _row(3, host='OTHER'),
        _row(4, payload_type='other'),
    ]
    retired: list[int] = []

    async def update(
        actual_session: Mythic,
        *,
        callback_display_id: int,
        active: bool,
    ) -> object:
        assert actual_session is session
        assert active is False
        retired.append(callback_display_id)
        return {'status': 'success'}

    monkeypatch.setattr(mythic, 'update_callback', update)
    asyncio.run(apollo_probe.retire_stale_callbacks(session, rows, 2))
    assert retired == [1]


def test_fails_when_stale_callback_cannot_be_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mythic()

    async def update(
        _session: Mythic,
        *,
        callback_display_id: int,
        active: bool,
    ) -> object:
        assert callback_display_id == 1
        assert active is False
        return {'status': 'error'}

    monkeypatch.setattr(mythic, 'update_callback', update)
    with pytest.raises(RuntimeError, match='Could not retire stale Apollo callback 1'):
        asyncio.run(apollo_probe.retire_stale_callbacks(session, [_row(1), _row(2)], 2))


def test_main_uses_sdk_and_skips_a_dead_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = Mythic()
    rows = [_row(1), _row(2), _row(3)]
    retired: list[int] = []

    async def login(
        *,
        server_ip: str,
        server_port: int,
        username: str,
        password: str,
        ssl: bool,
        timeout: int,
    ) -> Mythic:
        assert (server_ip, server_port, username, password, ssl, timeout) == (
            '127.0.0.1',
            7443,
            'operator',
            'secret',
            True,
            60,
        )
        return session

    async def callbacks(
        actual_session: Mythic,
        *,
        custom_return_attributes: str,
    ) -> list[object]:
        assert actual_session is session
        assert 'id display_id host user' in custom_return_attributes
        return [row.model_dump() for row in rows]

    async def task(
        _session: Mythic,
        *,
        command_name: str,
        parameters: dict[str, str],
        callback_display_id: int,
        timeout: int,
    ) -> bytes:
        assert command_name == 'run'
        assert parameters['executable'] == 'whoami.exe'
        assert timeout == 45
        if callback_display_id == 3:
            raise RuntimeError('callback offline')
        return b'BRK-PROD01\\review.operator'

    async def update(
        _session: Mythic,
        *,
        callback_display_id: int,
        active: bool,
    ) -> object:
        assert active is False
        retired.append(callback_display_id)
        return {'status': 'success'}

    monkeypatch.setattr(
        apollo_probe,
        '_fixture',
        lambda: apollo_probe.Connection(
            url='https://127.0.0.1:7443',
            username='operator',
            password='secret',
        ),
    )
    monkeypatch.setattr(mythic, 'login', login)
    monkeypatch.setattr(mythic, 'get_all_active_callbacks', callbacks)
    monkeypatch.setattr(mythic, 'issue_task_and_waitfor_task_output', task)
    monkeypatch.setattr(mythic, 'update_callback', update)

    assert asyncio.run(apollo_probe.main()) == 0
    assert retired == [1, 3]
    assert capsys.readouterr().out == 'CALLBACK_OK id=2\n'

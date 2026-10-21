"""Prove a pre-ceded Apollo callback is live on the Mythic execution VM."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from mythic import mythic
from mythic.mythic_classes import Mythic
from pydantic import BaseModel, TypeAdapter


@dataclass(frozen=True)
class Connection:
    """Operator connection provided by the Ludus role."""

    url: str
    username: str
    password: str


class PayloadType(BaseModel):
    """Payload type metadata returned by Mythic."""

    name: str


class Payload(BaseModel):
    """Payload metadata returned by Mythic."""

    payloadtype: PayloadType


class Callback(BaseModel):
    """Active callback fields requested from Mythic."""

    id: int
    display_id: int
    host: str
    user: str
    payload: Payload


class UpdateStatus(BaseModel):
    """Result of a Mythic callback update."""

    status: str


def _fixture() -> Connection:
    values: dict[str, str] = {}
    path = Path('/home/agent/.config/brackenridge/mythic.env')
    for line in path.read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator:
            values[key] = value
    return Connection(
        url=values['SBAD_MYTHIC_URL'],
        username=values['SBAD_MYTHIC_USERNAME'],
        password=values['SBAD_MYTHIC_PASSWORD'],
    )


async def _login(connection: Connection) -> Mythic:
    endpoint = urlsplit(connection.url)
    if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname:
        raise ValueError('SBAD_MYTHIC_URL must be an HTTP(S) URL')
    return await mythic.login(
        server_ip=endpoint.hostname,
        server_port=endpoint.port or (7443 if endpoint.scheme == 'https' else 80),
        username=connection.username,
        password=connection.password,
        ssl=endpoint.scheme == 'https',
        timeout=60,
    )


async def _callbacks(session: Mythic) -> list[Callback]:
    response = await mythic.get_all_active_callbacks(
        session,
        custom_return_attributes='id display_id host user payload { payloadtype { name } }',
    )
    return TypeAdapter(list[Callback]).validate_python(response)


def callback_ids(rows: list[Callback]) -> list[int]:
    """Return active Apollo callback IDs, newest first."""
    return sorted(
        (row.display_id for row in rows if row.payload.payloadtype.name.casefold() == 'apollo'),
        reverse=True,
    )


async def prove_callback(session: Mythic, callback_id: int) -> bool:
    """Require a harmless command to complete and produce output."""
    output = await mythic.issue_task_and_waitfor_task_output(
        session,
        command_name='run',
        parameters={'executable': 'whoami.exe', 'arguments': ''},
        callback_display_id=callback_id,
        timeout=45,
    )
    return bool(output.strip())


async def retire_stale_callbacks(
    session: Mythic,
    rows: list[Callback],
    working_id: int,
) -> None:
    """Hide snapshotted callback records for the same host and user."""
    working = next((row for row in rows if row.display_id == working_id), None)
    if working is None:
        raise RuntimeError('Working Apollo callback disappeared')
    for row in rows:
        if (
            row.display_id == working_id
            or row.payload.payloadtype.name.casefold() != 'apollo'
            or row.host.casefold() != working.host.casefold()
            or row.user.casefold() != working.user.casefold()
        ):
            continue
        response = await mythic.update_callback(
            session,
            callback_display_id=row.display_id,
            active=False,
        )
        if UpdateStatus.model_validate(response).status != 'success':
            raise RuntimeError(f'Could not retire stale Apollo callback {row.display_id}')


async def main() -> int:
    """Return success only after an Apollo command produces a response."""
    session = await _login(_fixture())
    rows = await _callbacks(session)
    for callback_id in callback_ids(rows)[:5]:
        try:
            ready = await prove_callback(session, callback_id)
        except Exception:
            # A snapshotted callback can disappear while a newer one is usable.
            continue
        if ready:
            await retire_stale_callbacks(session, rows, callback_id)
            print(f'CALLBACK_OK id={callback_id}')
            return 0
    raise RuntimeError('No live Apollo callback completed a task')


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as error:
        print(f'Apollo callback probe failed: {type(error).__name__}', file=sys.stderr)
        raise SystemExit(1) from None

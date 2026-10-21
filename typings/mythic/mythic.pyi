# Mythic 3.2's scripting client ships without type information.
from .mythic_classes import Mythic


async def login(
    *,
    server_ip: str,
    server_port: int,
    username: str,
    password: str,
    ssl: bool,
    timeout: int,
) -> Mythic: ...


async def get_all_active_callbacks(
    session: Mythic, *, custom_return_attributes: str,
) -> list[object]: ...


async def issue_task_and_waitfor_task_output(
    session: Mythic,
    *,
    command_name: str,
    parameters: dict[str, str],
    callback_display_id: int,
    timeout: int,
) -> bytes: ...


async def update_callback(
    session: Mythic, *, callback_display_id: int, active: bool,
) -> object: ...

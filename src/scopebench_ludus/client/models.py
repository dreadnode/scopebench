"""Validated Ludus response types and API errors."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class RangeStatus(BaseModel):
    """Validate the identity and lifecycle fields used by the provider."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    range_id: str = Field(alias='rangeID')
    state: str = Field(alias='rangeState')
    name: str
    description: str


class LudusAPIError(RuntimeError):
    """Report the operation and HTTP status without exposing response secrets."""

    def __init__(self, operation: str, status_code: int | None = None) -> None:
        self.status_code: int | None = status_code
        super().__init__(f'Ludus {operation}' + (f' (HTTP {status_code})' if status_code else ''))


class AnsibleItem(BaseModel):
    """Fields needed to distinguish a user's role from global installations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    name: str
    type: str
    global_scope: bool = Field(default=False, alias='global')

"""Validated Ludus response types and API errors."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class RangeStatus(BaseModel):
    """Validate the identity and lifecycle fields used by the provider."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    range_id: str = Field(alias='rangeID')
    range_number: int | None = Field(default=None, alias='rangeNumber')
    state: str = Field(alias='rangeState')
    name: str
    description: str
    vms: list['RangeVM'] = Field(default_factory=list, alias='VMs')
    testing_enabled: bool | None = Field(default=None, alias='testingEnabled')
    allowed_domains: list[str] | None = Field(default=None, alias='allowedDomains')
    allowed_ips: list[str] | None = Field(default=None, alias='allowedIPs')


class RangeVM(BaseModel):
    """The Proxmox identity and power state of a range VM."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    vmid: int = Field(alias='proxmoxID')
    powered_on: bool = Field(alias='poweredOn')


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


class APIEnvelope(BaseModel):
    """Optional error fields common to Ludus JSON responses."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    error: JsonValue | None = None
    errors: JsonValue | None = None
    success: bool | None = None


class TextResult(BaseModel):
    """A Ludus string result envelope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    result: str


class UserIdentity(BaseModel):
    """The authenticated Ludus user."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    user_id: str = Field(alias='userID')


class AllowedDomains(BaseModel):
    """Domains accepted by Ludus testing mode."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
    allowed: list[str]


class SnapshotResult(BaseModel):
    """Ludus reports per-VM failures inside an HTTP 200 response."""

    success: list[int]
    errors: list[dict[str, JsonValue]] = Field(default_factory=list)


class SnapshotItem(BaseModel):
    """One VM's named snapshot."""

    vmid: int
    name: str


class SnapshotListing(BaseModel):
    """Snapshot inventory and any per-VM list errors."""

    snapshots: list[SnapshotItem]
    errors: list[dict[str, JsonValue]] = Field(default_factory=list)

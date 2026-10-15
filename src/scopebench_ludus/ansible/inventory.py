"""Typed inventory used to select a host and verify its operating system."""

from __future__ import annotations

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter

from scopebench_contribution.schemas import Payload


class HostVariables(Payload):
    """Only connection-kind fields are needed here; credential fields are discarded."""

    ansible_connection: str | None = None
    ansible_shell_type: str | None = None

    @property
    def windows(self) -> bool:
        """Recognize Windows connection and shell plugins."""
        return self.ansible_connection in ('winrm', 'psrp') or self.ansible_shell_type in (
            'cmd',
            'powershell',
        )


class InventoryGroup(Payload):
    """Hosts and child groups from ansible-inventory --list."""

    hosts: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)


class InventoryMetadata(Payload):
    """Connection information keyed by literal inventory hostname."""

    hostvars: dict[str, HostVariables]


class Inventory(Payload):
    """Ansible's metadata plus its dynamically named, validated inventory groups."""

    metadata: InventoryMetadata
    groups: dict[str, InventoryGroup]

    @classmethod
    def from_json(cls, document: str) -> Inventory:
        """Validate Ansible's metadata and dynamic group names at the JSON boundary."""
        data = TypeAdapter(
            dict[str, JsonValue], config=ConfigDict(strict=True, hide_input_in_errors=True)
        ).validate_json(document)
        return cls.model_validate({'metadata': data.pop('_meta', None), 'groups': data})

    def execution_host(self, selector: str, *, expected_windows: bool) -> str:
        """Resolve one literal host or singleton group, including nested cyclic groups."""
        hostvars = self.metadata.hostvars
        visited: set[str] = set()

        def hosts(name: str) -> set[str]:
            if name in hostvars:
                return {name}
            if name in visited:
                return set()
            visited.add(name)
            group = self.groups.get(name)
            if group is None:
                return set()
            return set(group.hosts).union(*(hosts(child) for child in group.children))

        selected = hosts(selector)
        if len(selected) != 1:
            raise ValueError(
                'The Ludus execution host/group must select exactly one inventory host'
            )
        host = selected.pop()
        variables = hostvars.get(host)
        if variables is None:
            raise ValueError('The Ludus execution host has no connection variables')
        if variables.windows != expected_windows:
            raise ValueError('The selected execution host differs from [environment].os')
        return host

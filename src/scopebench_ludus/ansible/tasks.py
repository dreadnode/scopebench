"""Typed task construction; preserve literal strings until YAML serialization."""

from dataclasses import dataclass
from typing import TypedDict

from pydantic import JsonValue


@dataclass(frozen=True, kw_only=True)
class AnsibleTask:
    """The module call and task options used by Harbor guest operations."""

    module: str
    parameters: JsonValue
    name: str | None = None
    args: dict[str, JsonValue] | None = None
    environment: dict[str, str] | None = None
    register: str | None = None
    failed_when: bool | None = None
    no_log: bool | None = None
    delegate_to: str | None = None
    vars: dict[str, JsonValue] | None = None
    loop: list[str] | None = None

    def document(self) -> dict[str, JsonValue]:
        """Produce Ansible's module-key syntax without stripping !unsafe string tags."""
        result: dict[str, JsonValue] = {self.module: self.parameters}
        for key, value in (
            ('name', self.name),
            ('args', self.args),
            ('register', self.register),
            ('failed_when', self.failed_when),
            ('no_log', self.no_log),
            ('delegate_to', self.delegate_to),
            ('vars', self.vars),
        ):
            if value is not None:
                result[key] = value
        if self.environment is not None:
            result['environment'] = {key: value for key, value in self.environment.items()}
        if self.loop is not None:
            result['loop'] = [value for value in self.loop]
        return result


class Play(TypedDict):
    """The fixed playbook envelope used to execute a group of guest operations."""

    name: str
    hosts: str
    gather_facts: bool
    tasks: list[dict[str, JsonValue]]

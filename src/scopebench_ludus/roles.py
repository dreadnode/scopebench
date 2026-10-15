"""Namespace bundled roles while preserving native Ludus range settings."""

import shutil
from pathlib import Path

import yaml
from pydantic import JsonValue, TypeAdapter

from .client import LudusClient
from .config import LudusConfig
from .resources import LudusResources


def remap_roles(roles: JsonValue, aliases: dict[str, str]) -> None:
    """Point VM and dependency references at the trial-specific uploaded role names."""
    if not isinstance(roles, list):
        raise ValueError('Ludus VM roles must be a list')
    for index, role in enumerate(roles):
        if isinstance(role, str):
            roles[index] = aliases.get(role, role)
        elif isinstance(role, dict):
            for key in ('name', 'role'):
                name = role.get(key)
                if isinstance(name, str):
                    role[key] = aliases.get(name, name)
            dependencies = role.get('depends_on', [])
            if isinstance(dependencies, list):
                for dependency in dependencies:
                    if isinstance(dependency, dict):
                        name = dependency.get('role')
                        if isinstance(name, str):
                            dependency['role'] = aliases.get(name, name)


async def upload_roles(
    client: LudusClient,
    definition: Path,
    config: LudusConfig,
    directory: Path,
    resources: LudusResources,
    record: Path,
) -> bytes:
    """Upload bundled roles under trial names and return native range YAML."""
    source = definition / config.range_config
    if not config.local_roles:
        return source.read_bytes()
    aliases = {
        Path(role).name: f'scopebench_{resources.range_id}_{Path(role).name}'.lower()
        for role in config.local_roles
    }
    for relative in config.local_roles:
        role = definition / relative
        name = aliases[role.name]
        target = directory / 'roles' / name
        _ = shutil.copytree(role, target)
        meta = target / 'meta/main.yml'
        metadata = TypeAdapter(dict[str, JsonValue]).validate_python(
            yaml.safe_load(meta.read_text())
        )
        if 'dependencies' in metadata:
            remap_roles(metadata['dependencies'], aliases)
            _ = meta.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding='utf-8')
        resources.roles.append(name)
        resources.save(record)
        await client.upload_role(name, target)
    native = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load(source.read_text()))
    hosts = native.get('ludus')
    if not isinstance(hosts, list):
        raise ValueError('Native Ludus configuration must contain a ludus VM list')
    for host in hosts:
        if not isinstance(host, dict):
            raise ValueError('Native Ludus VM definitions must be objects')
        remap_roles(host.get('roles', []), aliases)
    return yaml.safe_dump(native, sort_keys=False).encode()

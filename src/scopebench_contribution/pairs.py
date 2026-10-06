"""Validate paired native tasks against their shared environment definitions."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import cast

from harbor.models.task.task import Task  # pyright: ignore[reportMissingTypeStubs]
from scopebench_environments.definitions import (
    binding_for,
    read_binding,
    scenario_config,
    scenario_path,
)
from scopebench_environments.ludus import read_definition

from .builds import sources
from .native import (
    MARKER,
    Compose,
    Document,
    TaskConfig,
    read_compose,
    scenarios,
    task_configs,
    task_metadata,
    version_tag,
)
from .tasks import SLUG, collection_root

TASK_NAMESPACE = 'scopebench-lite'


def check_task_files(manifest: Path) -> None:
    """Require task slugs and commented canaries in authored task files."""
    if not SLUG.fullmatch(manifest.parent.name):
        raise ValueError(f'Invalid task slug (kebab-case): {manifest.parent.name}')
    for name, pattern in (
        ('task.toml', rf'^\s*#.*{re.escape(MARKER)}'),
        ('instruction.md', rf'<!--[^\n]*{re.escape(MARKER)}[^\n]*-->'),
    ):
        path = manifest.parent / name
        if not re.search(pattern, path.read_text(encoding='utf-8'), re.MULTILINE):
            raise ValueError(f'Missing commented canary: {path}')


def runtime_compose(scenario: Path, native: Compose) -> Compose:
    """Validate shared topology using the image versions authored in native Compose."""
    compose = read_compose(scenario / 'environment/docker-compose.yaml')
    if compose.services.keys() != native.services.keys():
        raise ValueError(f'Environment services differ from shared source: {scenario}')
    for name, service in compose.services.items():
        _ = service.pop('build')
        if name != 'main':
            service['image'] = version_tag(native.services[name].get('image'))
        service['platform'] = 'linux/amd64'
    return compose


def check_canaries(root: Path) -> None:
    """Keep shared solutions, rubrics, and verifier code marked as benchmark data."""
    paths = {
        directory / name
        for directory in scenarios(root)
        for name in ('solution/solve.sh', 'solution/solve.py', 'rubric.md')
    }
    paths.update(
        path
        for directory in scenarios(root)
        for path in (directory / 'solution').rglob('*')
        if path.suffix in ('.py', '.sh')
    )
    paths.update(root / 'environments/verifier' / name for name in ('test.sh', 'verify.py'))
    for path in sorted(paths):
        if MARKER not in path.read_text(encoding='utf-8'):
            raise ValueError(f'Missing source canary: {path}')


def check_network(compose: Compose, manifest: Path) -> None:
    """Reject local builds and services that expose the host to a task."""
    for service in compose.services.values():
        if (
            'build' in service
            or 'ports' in service
            or service.get('privileged')
            or service.get('network_mode') == 'host'
        ):
            raise ValueError(f'Unsafe/nonportable service definition: {manifest}')
        volumes = service.get('volumes', [])
        if not isinstance(volumes, list):
            raise ValueError(f'Invalid service volumes: {manifest}')
        for volume in volumes:
            if not isinstance(volume, str) or '/' in volume.split(':')[0] or volume.startswith('.'):
                raise ValueError(f'Host bind mount in {manifest}')


def check_task(root: Path, manifest: Path, config: TaskConfig) -> None:
    """Validate a native task against its shared environment and grading inputs."""
    meta = task_metadata(config)
    relative = manifest.relative_to(root).parts
    domain_parts = relative[1:-2] if relative[0] == 'tasks' else relative[2:-2]
    if '/'.join(domain_parts) != meta.domain:
        raise ValueError(f'Task directory differs from metadata domain: {manifest}')
    collection = collection_root(root, manifest)
    scenario = scenario_path(collection, meta.domain, meta.benchmark)
    definition = scenario_config(scenario)
    if (meta.backend, meta.status) != (definition.backend, definition.status):
        raise ValueError(f'Task backend or status differs from shared scenario: {manifest}')
    variant = 'capability' if meta.scope == 'raw' else 'scope'
    namespace = TASK_NAMESPACE if relative[0] == 'lite' else 'scopebench'
    if (
        meta.variant != variant
        or manifest.parent.name != f'{meta.benchmark}-{variant}'
        or config.task is None
        or config.task.name != f'{namespace}/{manifest.parent.name}'
    ):
        raise ValueError(f'Task name and metadata differ: {manifest}')
    check_task_files(manifest)
    expected = config.verifier.env.get('SCOPEBENCH_EXPECTED_FLAG', '')
    if not expected or expected != expected.strip():
        raise ValueError(f'Expected a nonempty flag value in verifier configuration: {manifest}')
    if config.verifier.env != {
        'SCOPEBENCH_SCENARIO': meta.benchmark,
        'SCOPEBENCH_MODE': meta.scope,
        'SCOPEBENCH_EXPECTED_FLAG': expected,
    }:
        raise ValueError(f'Incorrect verifier configuration: {manifest}')
    if meta.backend == 'docker':
        _ = version_tag(config.environment.docker_image)
    elif config.environment.docker_image is not None:
        raise ValueError(f'VM/cloud task declares a Docker agent image: {manifest}')
    if config.verifier.environment is None:
        raise ValueError(f'Expected a separate verifier environment: {manifest}')
    _ = version_tag(config.verifier.environment.docker_image)
    if config.artifacts != ['/tmp/result.txt', '/logs/agent/trajectory.json']:
        raise ValueError(f'Incorrect submitted artifact: {manifest}')
    files = {
        p.relative_to(manifest.parent).as_posix() for p in manifest.parent.rglob('*') if p.is_file()
    }
    runtime_file = 'docker-compose.yaml' if meta.backend == 'docker' else 'reference.json'
    if files != {'task.toml', 'instruction.md', f'environment/{runtime_file}'}:
        raise ValueError(f'Unexpected task-local implementation files: {manifest}')
    if meta.backend != 'docker':
        if meta.backend == 'ludus':
            _ = read_definition(scenario / 'environment')
        if read_binding(manifest.parent / 'environment') != binding_for(root, scenario):
            raise ValueError(f'Task binding differs from shared runtime definition: {manifest}')
        return
    actual = read_compose(manifest.parent / 'environment/docker-compose.yaml')
    if actual != runtime_compose(scenario, actual):
        raise ValueError(f'Environment differs from shared source: {manifest}')
    check_network(actual, manifest)


def normalized_pair_member(data: Document) -> Document:
    """Remove task identity and the fields that differ between paired conditions."""
    normalized = copy.deepcopy(data)
    del cast(Document, normalized['task'])['name']
    # TaskConfig has already validated these nested mappings before normalization.
    metadata = cast(Document, normalized['metadata'])
    del metadata['scope'], metadata['variant']
    _ = metadata.pop('source_variant', None)
    verifier = cast(Document, normalized['verifier'])
    del cast(Document, verifier['env'])['SCOPEBENCH_MODE']
    return normalized


def check(root: Path) -> None:
    """Validate authored native tasks, shared topology, and paired image versions."""
    check_canaries(root)
    groups: dict[str, list[tuple[Path, TaskConfig, Document]]] = {}
    for manifest, data in task_configs(root):
        config = Task(manifest.parent).config
        check_task(root, manifest, config)
        meta = task_metadata(config)
        scenario = scenario_path(collection_root(root, manifest), meta.domain, meta.benchmark)
        groups.setdefault(scenario.relative_to(root).as_posix(), []).append(
            (manifest, config, data)
        )
    if set(groups) != {p.relative_to(root).as_posix() for p in scenarios(root, None)}:
        raise ValueError('Scenario sources and task pairs differ')
    for name, pair in groups.items():
        if len(pair) != 2 or {task_metadata(config).scope for _, config, _ in pair} != {
            'raw',
            'scoped',
        }:
            raise ValueError(f'{name}: expected exactly one raw and one scoped task')
        runtime_file = (
            'docker-compose.yaml'
            if task_metadata(pair[0][1]).backend == 'docker'
            else 'reference.json'
        )
        if normalized_pair_member(pair[0][2]) != normalized_pair_member(pair[1][2]) or (
            (pair[0][0].parent / 'environment' / runtime_file).read_bytes()
            != (pair[1][0].parent / 'environment' / runtime_file).read_bytes()
        ):
            raise ValueError(f'{name}: pair differs beyond instructions and scoring mode')
    print(f'Validated {len(groups)} pairs and {len(sources(root))} shared images')


def main() -> int:
    """Validate the collection in the current checkout."""
    check(Path.cwd().resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

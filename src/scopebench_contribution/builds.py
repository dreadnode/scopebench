"""Discover shared build contexts and select declared image versions for Docker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from scopebench_environments.definitions import scenario_path

from .native import Build, TaskConfig, read_compose, task_metadata, version_tag
from .publication import published
from .tasks import collection_root, git, git_paths, manifests, task_path

VERIFIER_CONTEXT = 'environments/verifier'


def authored_tree(
    root: Path, revision: str | None
) -> tuple[list[Path], Callable[[Path], str], set[str]]:
    """Read current authoring files or their historical layouts without checking them out."""
    paths: set[str] = (
        set()
        if revision is None
        else git_paths(
            root,
            'ls-tree',
            '-r',
            '--name-only',
            '-z',
            revision,
            '--',
            'lite',
            'tasks',
            'environments',
        )
    )
    tasks = (
        manifests(root)
        if revision is None
        else sorted(root / path for path in paths if task_path(path, legacy=True) is not None)
    )

    def contents(path: Path) -> str:
        return (
            path.read_text(encoding='utf-8')
            if revision is None
            else git(root, 'show', f'{revision}:{path.relative_to(root).as_posix()}', strip=False)
        )

    return tasks, contents, paths


def verifier_specifications(root: Path, *, revision: str | None = None) -> dict[str, str]:
    """Bundle exactly the authored scope prompt and rubric, without actor/answer metadata."""
    result: dict[str, str] = {}
    manifests, contents, paths = authored_tree(root, revision)
    for manifest in manifests:
        config = TaskConfig.model_validate_toml(contents(manifest))
        metadata = task_metadata(config)
        if metadata.scope == 'scoped':
            name = metadata.benchmark
            if name in result:
                raise ValueError(f'Duplicate scoped specification: {name}')
            scenario = scenario_path(collection_root(root, manifest), metadata.domain, name)
            rubric = scenario / 'rubric.md'
            if revision is not None:
                rubric = historical_scenario_file(root, scenario, name, 'rubric.md', paths)
            instruction = contents(manifest.parent / 'instruction.md')
            result[name] = json.dumps(
                {'instruction': instruction, 'rubric': contents(rubric)}, sort_keys=True
            )
    return result


def prepare(root: Path, context: str) -> None:
    """Generate evaluator-only specifications inside the verifier build context."""
    if context != VERIFIER_CONTEXT:
        return
    specifications = verifier_specifications(root)
    directory = root / context / 'specifications'
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    for name, content in specifications.items():
        _ = (directory / f'{name}.json').write_text(content, encoding='utf-8')


def context_tree(root: Path, context: str) -> str:
    """Fingerprint tracked source plus the verifier's external rubric inputs."""
    tree = git(root, 'rev-parse', f'HEAD:{context}')
    if context == VERIFIER_CONTEXT:
        bundled = json.dumps(verifier_specifications(root), sort_keys=True)
        return hashlib.sha256((tree + bundled).encode()).hexdigest()
    return tree


@dataclass(frozen=True)
class BuildTarget:
    """A Docker context and the image reference already declared by its tasks."""

    context: str
    tag: str


def historical_scenario_file(
    root: Path, scenario: Path, name: str, filename: str, paths: set[str]
) -> Path:
    """Read both domain-organized and previous scenario layouts from Git history."""
    directories = (
        scenario,
        root / scenario.relative_to(collection_root(root, scenario)),
        root / 'environments' / name,
    )
    filenames = (
        (filename, 'compose.yaml') if filename == 'environment/docker-compose.yaml' else (filename,)
    )
    candidates = (directory / file for directory in directories for file in filenames)
    return next(
        candidate for candidate in candidates if candidate.relative_to(root).as_posix() in paths
    )


def context_key(relative: Path) -> str:
    """Keep image names stable when scenario contexts gain domain directories."""
    parts = relative.parts
    position = parts.index('environment') if 'environment' in parts else 0
    return '-'.join((parts[position - 1], *parts[position + 1 :]) if position else parts)


def docker_tasks(
    paths: list[Path], read: Callable[[Path], str]
) -> Iterator[tuple[Path, TaskConfig]]:
    """Keep VM definitions out of Docker build planning in current and historical trees."""
    for path in paths:
        config = TaskConfig.model_validate_toml(read(path))
        if task_metadata(config).backend == 'docker':
            yield path, config


def sources(
    root: Path, scenario: str | None = None, *, revision: str | None = None
) -> dict[str, BuildTarget]:
    """Read current or historical native references, deduplicating shared contexts."""
    targets: dict[str, BuildTarget] = {}

    def add(context: Path, reference: str | None) -> None:
        context = context.resolve()
        relative = context.relative_to(collection_root(root, context) / 'environments')
        key = context_key(relative)
        if reference is None:
            raise ValueError(f'Missing image reference: {context}')
        if revision is None:
            _ = version_tag(reference)
            if not (context / 'Dockerfile').is_file() or any(
                path.is_symlink() for path in context.rglob('*')
            ):
                raise ValueError(
                    f'Expected a Dockerfile and self-contained build context: {context}'
                )
        target = BuildTarget(context.relative_to(root).as_posix(), reference)
        if key in targets and targets[key] != target:
            raise ValueError(f'Conflicting image references or context names: {key}')
        if revision is None and any(
            other.tag == target.tag and other != target for other in targets.values()
        ):
            raise ValueError(f'Image tag used by different build contexts: {reference}')
        targets[key] = target

    paths, contents, historical_paths = authored_tree(root, revision)
    for manifest, config in docker_tasks(paths, contents):
        metadata = task_metadata(config)
        name = metadata.benchmark
        if scenario and name != scenario:
            continue
        directory = (
            scenario_path(collection_root(root, manifest), metadata.domain, name) / 'environment'
        )
        path = directory / 'docker-compose.yaml'
        if revision is not None:
            path = historical_scenario_file(
                root, directory.parent, name, 'environment/docker-compose.yaml', historical_paths
            )
            directory = path.parent
        authored = read_compose(path, text=contents(path))
        path = manifest.parent / 'environment/docker-compose.yaml'
        native = read_compose(path, text=contents(path))
        if authored.services.keys() != native.services.keys():
            raise ValueError(f'Environment services differ from shared source: {manifest}')
        for service_name, service in authored.services.items():
            reference = (
                config.environment.docker_image
                if service_name == 'main'
                else native.services[service_name].get('image')
            )
            if reference is not None and not isinstance(reference, str):
                raise ValueError(f'Invalid service image: {manifest}')
            add(directory / Build.model_validate(service['build']).context, reference)
        if config.verifier.environment is None:
            raise ValueError(f'Expected a separate verifier environment: {manifest}')
        add(root / 'environments/verifier', config.verifier.environment.docker_image)
    return dict(sorted(targets.items()))


def same_context(root: Path, current: str, base: str, previous: str) -> bool:
    """Compare moved build inputs with their historical Git blobs and executable modes."""
    expected = set(git(root, 'ls-tree', '-r', '-z', f'{base}:{previous}').split('\0')) - {''}
    algorithm = git(root, 'rev-parse', '--show-object-format')
    directory = root / current
    actual: set[str] = set()
    for path in directory.rglob('*'):
        if path.is_file():
            content = path.read_bytes()
            digest = hashlib.new(algorithm, f'blob {len(content)}\0'.encode() + content).hexdigest()
            mode = '100755' if path.stat().st_mode & 0o111 else '100644'
            actual.add(f'{mode} blob {digest}\t{path.relative_to(directory).as_posix()}')
    return actual == expected


def select(
    root: Path, *, base: str | None = None, scenario: str | None = None
) -> dict[str, BuildTarget]:
    """Select new versions; source changes require a new declared image reference."""
    current = sources(root, scenario)
    if scenario and not current:
        raise ValueError(f'No image sources selected: {scenario}')
    if base is None:
        return current
    previous = sources(root, revision=base)
    previous_tags = {target.tag: target for target in previous.values()}
    changed = git_paths(root, 'diff', '--name-only', '--no-renames', '-z', base)
    previous_specifications = verifier_specifications(root, revision=base)
    # A removed task leaves only an unused specification in the published verifier.
    verifier_changed = any(
        previous_specifications.get(name) != specification
        for name, specification in verifier_specifications(root).items()
    )
    selected: dict[str, BuildTarget] = {}
    for key, target in current.items():
        old = previous.get(key) or previous_tags.get(target.tag)
        source_changed = any(path.startswith(target.context + '/') for path in changed)
        if target.context == VERIFIER_CONTEXT:
            source_changed |= verifier_changed
        if old and old.context != target.context:
            source_changed = not same_context(root, target.context, base, old.context)
        if old and source_changed and old.tag == target.tag:
            raise ValueError(f'Source changed without a new image version: {target.tag}')
        if old is None or old.tag != target.tag or source_changed:
            selected[key] = target
    return selected


def matrix(targets: dict[str, BuildTarget]) -> list[dict[str, str]]:
    """Supply standard Docker Action inputs without an image lock or inventory file."""
    return [
        {'key': key, 'context': target.context, 'tag': target.tag}
        for key, target in targets.items()
    ]


class Arguments(argparse.Namespace):
    """Optional CI comparison and local scenario selection."""

    command: str = ''
    base: str | None = None
    scenario: str | None = None
    context: str | None = None
    image: str | None = None


def run(argv: Sequence[str], *, root: Path) -> int:
    """Print CI inputs or load declared image tags into the local Docker daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        'command', choices=['matrix', 'local', 'prepare', 'fingerprint', 'published']
    )
    _ = parser.add_argument('--base')
    _ = parser.add_argument('--scenario')
    _ = parser.add_argument('--context')
    _ = parser.add_argument('--image')
    args = parser.parse_args(argv, namespace=Arguments())
    if args.command == 'published':
        context = args.context or os.environ['CONTEXT']
        image = args.image or os.environ['IMAGE']
        _ = version_tag(image)
        tree = context_tree(root, context)
        exists = published(image, tree, root=root)
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
            _ = output.write(f'published={str(exists).lower()}\ntree={tree}\n')
        return 0
    if args.command == 'prepare':
        prepare(root, args.context or VERIFIER_CONTEXT)
        return 0
    if args.command == 'fingerprint':
        print(context_tree(root, args.context or VERIFIER_CONTEXT))
        return 0
    targets = select(root.resolve(), base=args.base, scenario=args.scenario)
    if args.command == 'matrix':
        print(json.dumps(matrix(targets)))
    else:
        for target in targets.values():
            prepare(root, target.context)
            _ = subprocess.run(
                [
                    'docker',
                    'buildx',
                    'build',
                    '--platform',
                    'linux/amd64',
                    '--load',
                    '--provenance=false',
                    '--tag',
                    target.tag,
                    target.context,
                ],
                cwd=root,
                check=True,
            )
    return 0


def main() -> int:
    """Operate only on the current checkout."""
    return run(sys.argv[1:], root=Path.cwd())


if __name__ == '__main__':
    raise SystemExit(main())

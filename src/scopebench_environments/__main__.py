"""Package task-local runtime bundles from shared authoring sources."""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from scopebench_contribution.tasks import collection_root, manifests

from .definitions import Backend, binding_for, read_binding, write_bundle
from .ludus import inspect_instance


def package(root: Path, output: Path, backend: Backend) -> int:
    """Prepare portable VM/cloud tasks without changing authored task directories."""
    if backend == 'docker':
        raise ValueError('Docker tasks already reference published images')
    if output.exists():
        raise ValueError('Package destination already exists')
    count = 0
    for manifest in manifests(root):
        data = cast(dict[str, object], tomllib.loads(manifest.read_text(encoding='utf-8')))
        metadata = cast(dict[str, object], data['metadata'])
        if metadata.get('backend') != backend:
            continue
        binding = read_binding(manifest.parent / 'environment')
        scenario = collection_root(root, manifest) / 'environments' / binding.scenario
        if binding != binding_for(root, scenario):
            raise ValueError('Task binding differs from shared runtime definition')
        target = output / manifest.parent.relative_to(root)
        _ = shutil.copytree(manifest.parent, target)
        write_bundle(scenario / 'environment', target / 'environment/definition.tar')
        count += 1
    if not count:
        raise ValueError(f'No {backend} tasks selected')
    return count


class Arguments(argparse.Namespace):
    """Backend and destination for the package command."""

    command: str = ''
    backend: Backend = 'ludus'
    output: Path = Path('dist')
    url: str = ''
    insecure: bool = False


def run(argv: Sequence[str], *, root: Path) -> int:
    """Materialize portable task packages from a verified source checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    packaging = commands.add_parser('package')
    _ = packaging.add_argument('--backend', choices=['ludus', 'cloud'], required=True)
    _ = packaging.add_argument('--output', type=Path, required=True)
    inspection = commands.add_parser('inspect-ludus')
    _ = inspection.add_argument('--url', required=True)
    _ = inspection.add_argument('--insecure', action='store_true')
    args = parser.parse_args(argv, namespace=Arguments())
    if args.command == 'inspect-ludus':
        result = inspect_instance(args.url, verify=not args.insecure)
        print(f'{result["version"]}\nAvailable templates: {result["templates"]}')
        return 0
    print(f'Packaged {package(root, args.output, args.backend)} tasks at {args.output}')
    return 0


def main() -> int:
    """Use the current repository as the canonical authoring source."""
    return run(sys.argv[1:], root=Path.cwd())


if __name__ == '__main__':
    raise SystemExit(main())

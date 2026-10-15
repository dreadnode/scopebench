"""Set up a Ludus controller, inspect its API, or recover a trial."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from .client import LudusClient, validate_url
from .resources import recover
from .setup import setup_ludus


def inspect_instance(url: str, *, verify: bool = True) -> dict[str, str | int]:
    """Check version and templates through the same HTTP client as provisioning."""
    _ = validate_url(url)

    async def inspect() -> dict[str, str | int]:
        client = LudusClient(url, os.environ.get('LUDUS_API_KEY', ''), verify=verify)
        try:
            version = await client.text('')
            templates = TypeAdapter(list[JsonValue]).validate_python(
                await client.request('GET', 'templates')
            )
            return {'version': version, 'templates': len(templates)}
        finally:
            await client.close()

    return asyncio.run(inspect())


class Arguments(argparse.Namespace):
    """Inputs to Ludus controller commands."""

    command: str = ''
    url: str = ''
    insecure: bool = False
    resources: Path = Path()


def run(argv: Sequence[str]) -> int:
    """Run a controller command."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspection = commands.add_parser('inspect')
    _ = inspection.add_argument('--url', default=os.environ.get('LUDUS_URL', ''), required=False)
    _ = inspection.add_argument('--insecure', action='store_true')
    _ = commands.add_parser('setup', help='Install the controller Ansible collection')
    cleanup = commands.add_parser('cleanup', help='Retry cleanup of a recorded trial')
    _ = cleanup.add_argument('--resources', type=Path, required=True)
    args = parser.parse_args(argv, namespace=Arguments())
    if args.command == 'setup':
        setup_ludus()
    elif args.command == 'cleanup':
        asyncio.run(recover(args.resources))
        print(f'Cleaned up trial resources recorded in {args.resources}')
    else:
        result = inspect_instance(args.url, verify=not args.insecure)
        print(f'{result["version"]}\nAvailable templates: {result["templates"]}')
    return 0


def main() -> int:
    """Run the Ludus command line."""
    return run(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())

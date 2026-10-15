"""Install the Ansible Windows modules used for guest commands and files."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def setup_ludus() -> None:
    """Install the pinned controller collection once; never change a Ludus server."""
    for executable in ('ansible-playbook', 'ansible-inventory', 'ansible-galaxy'):
        if not shutil.which(executable):
            raise ValueError('Run uv sync --locked --group ludus before setup')
    destination = Path(sys.prefix) / 'ansible/collections'
    environment = dict(os.environ)
    environment['ANSIBLE_COLLECTIONS_PATH'] = str(destination)
    _ = environment.pop('LUDUS_API_KEY', None)
    _ = environment.pop('LUDUS_JWT_TOKEN', None)
    with tempfile.TemporaryDirectory(prefix='scopebench-ansible-setup-') as directory:
        environment['ANSIBLE_LOCAL_TEMP'] = directory
        environment['ANSIBLE_GALAXY_CACHE_DIR'] = directory
        result = subprocess.run(
            [
                'ansible-galaxy',
                'collection',
                'install',
                'ansible.windows:3.2.0',
                '-p',
                str(destination),
            ],
            env=environment,
            timeout=300,
            check=False,
        )
    if result.returncode:
        raise RuntimeError('Ansible controller collection installation failed')

"""Build inert Ludus definitions beside task bindings for provider tests."""

from pathlib import Path

import yaml

from scopebench_contribution.runtime import runtime_reference
from tests.benchmark_helpers import write

IMPORT_PATH = 'scopebench_ludus.environment:LudusEnvironment'


def prepare_environment(root: Path) -> Path:
    """Generate an inert definition for mocked providers, without runnable tasks."""
    scenario = root / 'environments/netpen/windows/provider-fixture'
    runtime = scenario / 'environment'
    write(scenario / 'scenario.toml', 'backend = "ludus"\nversion = "0.1.0"\n')
    write(
        runtime / 'provider.toml',
        'local_roles = ["ansible/roles/fixture_role"]\nhost = "scopebench"\n'
        + '[interaction]\nmode = "shell"\npreseeded_callback = false\n',
    )
    write(
        runtime / 'ludus/range-config.yml',
        yaml.safe_dump(
            {
                'ludus': [
                    {
                        'vm_name': '{{ range_id }}-windows',
                        'template': 'unavailable-test-template',
                        'roles': ['fixture_role'],
                        'role_vars': {'fixture_marker': 'inert'},
                    }
                ]
            }
        ),
    )
    role = runtime / 'ansible/roles/fixture_role'
    write(role / 'meta/main.yml', 'dependencies: []\n')
    write(role / 'tasks/main.yml', '- name: Inert role fixture\n  ansible.builtin.debug:\n')
    target = root / 'tasks/netpen/windows/provider-fixture/environment'
    write(target / 'reference.json', runtime_reference(root, scenario).model_dump_json())
    return target

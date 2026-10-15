"""Validate inventory shape once, then select hosts using typed connection data."""

import json

import pytest
from pydantic import JsonValue, ValidationError

from scopebench_ludus.ansible.inventory import Inventory


def inventory_document() -> dict[str, JsonValue]:
    return {
        '_meta': {
            'hostvars': {
                'linux': {'ansible_connection': 'ssh', 'ansible_password': 'fixture-password'},
                'windows': {'ansible_connection': 'winrm'},
            }
        },
        'executor': {'children': ['nested']},
        'nested': {'hosts': ['linux'], 'children': ['executor']},
        'all': {'hosts': ['linux', 'windows']},
    }


def test_inventory_resolves_nested_cycles_and_keeps_only_connection_kind() -> None:
    inventory = Inventory.from_json(json.dumps(inventory_document()))
    assert inventory.execution_host('executor', expected_windows=False) == 'linux'
    assert inventory.execution_host('windows', expected_windows=True) == 'windows'
    assert inventory.groups['executor'].children == ['nested']
    assert 'fixture-password' not in inventory.model_dump_json()


@pytest.mark.parametrize('selector', ['all', 'missing', 'linux:windows', '*'])
def test_inventory_requires_one_literal_execution_host(selector: str) -> None:
    inventory = Inventory.from_json(json.dumps(inventory_document()))
    with pytest.raises(ValueError, match='exactly one'):
        _ = inventory.execution_host(selector, expected_windows=False)


def test_inventory_rejects_execution_os_mismatch() -> None:
    inventory = Inventory.from_json(json.dumps(inventory_document()))
    with pytest.raises(ValueError, match='differs'):
        _ = inventory.execution_host('windows', expected_windows=False)


@pytest.mark.parametrize(
    'invalid',
    [
        {'_meta': {'hostvars': []}},
        {'_meta': {'hostvars': {'linux': {'ansible_connection': 7}}}},
        {'_meta': {'hostvars': {}}, 'group': {'hosts': ['linux', 7]}},
        {'_meta': {'hostvars': {}}, 'group': {'children': 'not-a-list'}},
        {'group': {'hosts': ['linux']}},
    ],
)
def test_inventory_rejects_malformed_connection_and_group_data(
    invalid: dict[str, JsonValue],
) -> None:
    with pytest.raises(ValidationError):
        _ = Inventory.from_json(json.dumps(invalid))


def test_inventory_parse_errors_hide_credential_bearing_input() -> None:
    with pytest.raises(ValidationError) as error:
        _ = Inventory.from_json('{"_meta": "fixture-password" INVALID}')
    assert 'fixture-password' not in str(error.value)


def test_inventory_rejects_group_host_without_connection_metadata() -> None:
    inventory = Inventory.from_json(
        json.dumps({'_meta': {'hostvars': {}}, 'executor': {'hosts': ['missing']}})
    )
    with pytest.raises(ValueError, match='no connection variables'):
        _ = inventory.execution_host('executor', expected_windows=False)

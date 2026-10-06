"""Static validation of the complete collection and repository-specific file rules."""

from pathlib import Path

import pytest

from scopebench_contribution import pairs
from scopebench_contribution.native import MARKER, TaskConfig, read_task
from tests.benchmark_helpers import ROOT, task_path, write


def test_complete_collection_passes_static_validation() -> None:
    pairs.check(ROOT)


@pytest.fixture
def canary_task(tmp_path: Path) -> Path:
    directory = tmp_path / 'example-capability'
    write(directory / 'task.toml', f'# scopebench-canary:{MARKER}\n')
    write(directory / 'instruction.md', f'<!-- scopebench-canary:{MARKER} -->\n')
    return directory


@pytest.mark.parametrize('name', ['task.toml', 'instruction.md'])
@pytest.mark.parametrize('defect', ['missing', 'uncommented', 'wrong-marker'])
def test_invalid_task_canaries_fail(canary_task: Path, name: str, defect: str) -> None:
    path = canary_task / name
    if defect == 'missing':
        path.unlink()
        error = FileNotFoundError
    else:
        text = (
            f'scopebench-canary:{MARKER}\n'
            if defect == 'uncommented'
            else path.read_text().replace(MARKER, 'other-marker')
        )
        write(path, text)
        error = ValueError
    with pytest.raises(error):
        pairs.check_task_files(canary_task / 'task.toml')


def test_commented_canaries_pass(canary_task: Path) -> None:
    pairs.check_task_files(canary_task / 'task.toml')


@pytest.mark.parametrize('slug', ['Example', 'example--scope'])
def test_invalid_slugs_fail(canary_task: Path, slug: str) -> None:
    directory = canary_task.with_name(slug)
    _ = canary_task.rename(directory)
    with pytest.raises(ValueError, match='Invalid task slug'):
        pairs.check_task_files(directory / 'task.toml')


@pytest.mark.parametrize('field', ['task', 'verifier.environment'])
def test_harbor_optional_fields_required_by_scopebench(image_root: Path, field: str) -> None:
    manifest = task_path(image_root) / 'task.toml'
    config = TaskConfig.model_validate(read_task(manifest))
    if field == 'task':
        config.task = None
    else:
        config.verifier.environment = None
    with pytest.raises(ValueError, match='Task name and metadata|separate verifier environment'):
        pairs.check_task(image_root, manifest, config)


@pytest.mark.parametrize('value', [None, '', '   ', 'flag\n', ' flag'])
def test_invalid_verifier_flags_fail(image_root: Path, value: str | None) -> None:
    manifest = task_path(image_root) / 'task.toml'
    config = TaskConfig.model_validate(read_task(manifest))
    if value is None:
        del config.verifier.env['SCOPEBENCH_EXPECTED_FLAG']
    else:
        config.verifier.env['SCOPEBENCH_EXPECTED_FLAG'] = value
    with pytest.raises(ValueError, match='Expected a nonempty flag value'):
        pairs.check_task(image_root, manifest, config)


@pytest.mark.parametrize('matching', [True, False])
def test_pair_flags_are_declared_in_manifests(image_root: Path, matching: bool) -> None:
    for scope in ('raw', 'scoped') if matching else ('raw',):
        manifest = task_path(image_root, scope) / 'task.toml'
        config = TaskConfig.model_validate(read_task(manifest))
        expected = config.verifier.env['SCOPEBENCH_EXPECTED_FLAG']
        write(manifest, manifest.read_text().replace(expected, 'other-answer'))
    if matching:
        pairs.check(image_root)
    else:
        with pytest.raises(ValueError, match='pair differs beyond'):
            pairs.check(image_root)


def test_task_slug_has_no_arbitrary_token_limit(canary_task: Path) -> None:
    directory = canary_task.with_name('one-two-three-four-five-six-seven-capability')
    _ = canary_task.rename(directory)
    pairs.check_task_files(directory / 'task.toml')

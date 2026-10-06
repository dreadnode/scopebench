"""Only new image versions enter CI; unpublished versions can be built locally."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from scopebench_contribution import builds, pairs
from scopebench_contribution.native import (
    DOCUMENT,
    REGISTRY,
    TaskConfig,
    read_compose,
    read_task,
    version_tag,
)
from tests.benchmark_helpers import SCENARIO, native_files, task_path, write
from tests.conftest import GitRepo


@pytest.fixture
def benchmark_git(image_root: Path) -> GitRepo:
    repo = GitRepo(image_root)
    _ = repo.git('init', '-q', '--initial-branch=main')
    _ = repo.git('config', 'user.name', 'Build test')
    _ = repo.git('config', 'user.email', 'build@example.invalid')
    repo.commit()
    repo.base = repo.git('rev-parse', 'HEAD')
    return repo


def bump(root: Path, role: str) -> None:
    for path in (root / 'lite/tasks/web').rglob('*'):
        if path.is_file():
            _ = path.write_text(path.read_text().replace(f'{role}-v1', f'{role}-v2'))


def change_source(repo: GitRepo, context: str) -> None:
    folder = 'lite/environments' if context.startswith('web/') else 'environments'
    path = repo.root / folder / context / 'Dockerfile'
    _ = path.write_text(path.read_text() + '\n# Changed build input\n')


def test_pairs_share_one_target_per_context(image_root: Path) -> None:
    before = native_files(image_root)
    targets = builds.sources(image_root)
    assert len(targets) == 4
    assert targets['agent'].tag == f'{REGISTRY}:agent-v1'
    assert (
        targets[f'{SCENARIO}-challenge'].context
        == f'lite/environments/web/{SCENARIO}/environment/challenge'
    )
    assert native_files(image_root) == before


def test_benchmark_release_and_metadata_changes_do_not_rebuild_images(
    benchmark_git: GitRepo,
) -> None:
    _ = benchmark_git.git('tag', 'benchmark-v2')
    for path in (benchmark_git.root / 'lite/tasks/web').glob('*/task.toml'):
        _ = path.write_text(path.read_text().replace('version = "1.1.1"', 'version = "2.0.0"'))
    benchmark_git.write('README.md', 'Benchmark v2\n')
    benchmark_git.commit()
    assert builds.select(benchmark_git.root, base=benchmark_git.base) == {}


def test_runtime_topology_changes_do_not_rebuild_images(benchmark_git: GitRepo) -> None:
    root = benchmark_git.root
    paths = [root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml']
    paths.extend((root / 'lite/tasks/web').glob('*/environment/docker-compose.yaml'))
    for path in paths:
        _ = path.write_text(path.read_text().replace('interval: 5s', 'interval: 10s'))
    benchmark_git.commit()
    pairs.check(root)
    assert builds.select(root, base=benchmark_git.base) == {}


@pytest.mark.parametrize('change', ['none', 'runtime', 'permissions', 'rubric', 'instruction'])
def test_lite_relocation_preserves_only_unchanged_image_inputs(
    benchmark_git: GitRepo, change: str
) -> None:
    root = benchmark_git.root
    current = root / 'lite/environments/web' / SCENARIO
    previous = root / 'environments/web' / SCENARIO
    previous.parent.mkdir()
    _ = current.rename(previous)
    compose = previous / 'environment/docker-compose.yaml'
    write(
        compose, compose.read_text().replace('../../../../../environments/agent', '../../../agent')
    )
    for task in (root / 'lite/tasks/web').iterdir():
        _ = task.rename(root / 'lite' / task.name)
    (root / 'lite/tasks/web').rmdir()
    benchmark_git.commit()
    base = benchmark_git.git('rev-parse', 'HEAD')
    assert builds.sources(root, revision=base)[f'{SCENARIO}-challenge'].context.startswith(
        'environments/web/'
    )
    specifications = builds.verifier_specifications(root, revision=base)

    _ = previous.rename(current)
    compose = current / 'environment/docker-compose.yaml'
    write(
        compose, compose.read_text().replace('../../../agent', '../../../../../environments/agent')
    )
    (root / 'lite/tasks/web').mkdir()
    for task in (root / 'lite').glob(f'{SCENARIO}-*'):
        _ = task.rename(root / 'lite/tasks/web' / task.name)
    if change == 'runtime':
        change_source(benchmark_git, f'web/{SCENARIO}/environment/challenge')
    elif change == 'permissions':
        (current / 'environment/challenge/requirements.txt').chmod(0o755)
    elif change in ('rubric', 'instruction'):
        target = (
            current / 'rubric.md'
            if change == 'rubric'
            else task_path(root, 'scoped') / 'instruction.md'
        )
        write(target, target.read_text() + '\nChanged scope boundary\n')
    benchmark_git.commit()
    if change == 'none':
        assert builds.verifier_specifications(root) == specifications
        assert builds.select(root, base=base) == {}
        pairs.check(root)
    else:
        with pytest.raises(ValueError, match='Source changed without a new image version'):
            _ = builds.select(root, base=base)


@pytest.mark.parametrize(
    'change', ['none', 'content', 'permissions', 'addition', 'deletion', 'version']
)
def test_moved_contexts_preserve_versions_only_for_unchanged_inputs(
    benchmark_git: GitRepo, change: str
) -> None:
    root = benchmark_git.root
    scenario = root / 'lite/environments' / 'web' / SCENARIO
    environment = scenario / 'environment'
    for name in ('challenge', 'gateway'):
        _ = (environment / name).rename(scenario / name)
    compose = (environment / 'docker-compose.yaml').rename(scenario / 'compose.yaml')
    _ = compose.write_text(
        compose.read_text().replace(
            'context: ../../../../../environments/agent', 'context: ../../../../environments/agent'
        )
    )
    environment.rmdir()
    benchmark_git.commit()
    base = benchmark_git.git('rev-parse', 'HEAD')

    environment.mkdir()
    for name in ('challenge', 'gateway'):
        _ = (scenario / name).rename(environment / name)
    compose = compose.rename(environment / 'docker-compose.yaml')
    _ = compose.write_text(
        compose.read_text().replace(
            'context: ../../../../environments/agent', 'context: ../../../../../environments/agent'
        )
    )
    before = native_files(root)
    requirements = environment / 'challenge/requirements.txt'
    if change == 'content':
        change_source(benchmark_git, f'web/{SCENARIO}/environment/challenge')
    elif change == 'permissions':
        requirements.chmod(0o755)
    elif change == 'addition':
        benchmark_git.write(
            f'lite/environments/web/{SCENARIO}/environment/challenge/new-input.txt', 'new\n'
        )
    elif change == 'deletion':
        requirements.unlink()
    elif change == 'version':
        bump(root, 'challenge')

    if change in ('content', 'permissions', 'addition', 'deletion'):
        with pytest.raises(ValueError, match='Source changed without a new image version'):
            _ = builds.select(root, base=base)
    else:
        selected = builds.select(root, base=base)
        assert list(selected) == ([f'{SCENARIO}-challenge'] if change == 'version' else [])
    if change != 'version':
        assert native_files(root) == before


def test_adding_a_pair_builds_new_contexts_and_verifier(benchmark_git: GitRepo) -> None:
    root = benchmark_git.root
    new = 'additional-example'
    _ = shutil.copytree(
        root / 'lite/environments' / 'web' / SCENARIO, root / 'lite/environments' / 'web' / new
    )
    for variant in ('capability', 'scope'):
        original = root / 'lite/tasks/web' / f'auth-user-export-{variant}'
        target = root / 'lite/tasks/web' / f'additional-example-{variant}'
        _ = shutil.copytree(original, target)
        manifest = target / 'task.toml'
        _ = manifest.write_text(
            manifest.read_text()
            .replace(SCENARIO, new)
            .replace('scopebench-lite/auth-user-export-', 'scopebench-lite/additional-example-')
        )
        path = target / 'environment/docker-compose.yaml'
        _ = path.write_text(path.read_text().replace('auth-user-export-', 'additional-example-'))
    bump(root, 'verifier')
    before = native_files(root)
    benchmark_git.commit()
    selected = builds.select(root, base=benchmark_git.base)
    assert set(selected) == {f'{new}-challenge', f'{new}-gateway', 'verifier'}
    assert selected['verifier'].tag.endswith('-v2')
    assert native_files(root) == before
    pairs.check(root)


@pytest.mark.parametrize('change', ['none', 'content', 'permissions', 'addition', 'deletion'])
def test_renamed_scenarios_reuse_only_unchanged_images(benchmark_git: GitRepo, change: str) -> None:
    root = benchmark_git.root
    renamed = 'renamed-example'
    _ = (root / 'lite/environments' / 'web' / SCENARIO).rename(
        root / 'lite/environments' / 'web' / renamed
    )
    for manifest in (root / 'lite/tasks/web').glob('*/task.toml'):
        _ = manifest.write_text(manifest.read_text().replace(f'"{SCENARIO}"', f'"{renamed}"'))
    bump(root, 'verifier')
    challenge = root / 'lite/environments' / 'web' / renamed / 'environment/challenge'
    requirements = challenge / 'requirements.txt'
    if change == 'content':
        change_source(benchmark_git, f'web/{renamed}/environment/challenge')
    elif change == 'permissions':
        requirements.chmod(0o755)
    elif change == 'addition':
        write(challenge / 'new-input.txt', 'new\n')
    elif change == 'deletion':
        requirements.unlink()

    before = native_files(root)
    benchmark_git.commit()
    if change == 'none':
        selected = builds.select(root, base=benchmark_git.base)
        assert set(selected) == {'verifier'}
        assert selected['verifier'].tag.endswith('-v2')
        assert len(builds.sources(root, renamed)) == 4
    else:
        with pytest.raises(ValueError, match='Source changed without a new image version'):
            _ = builds.select(root, base=benchmark_git.base)
    assert native_files(root) == before


@pytest.mark.parametrize('context', [f'web/{SCENARIO}/environment/challenge', 'agent', 'verifier'])
def test_source_change_selects_only_its_new_version(benchmark_git: GitRepo, context: str) -> None:
    change_source(benchmark_git, context)
    role = context.rsplit('/', 1)[-1]
    bump(benchmark_git.root, role)
    benchmark_git.commit()
    before = native_files(benchmark_git.root)
    selected = builds.select(benchmark_git.root, base=benchmark_git.base)
    assert list(selected) == [
        context.removeprefix('web/').replace('/environment/', '/').replace('/', '-')
    ]
    assert next(iter(selected.values())).tag.endswith('-v2')
    assert native_files(benchmark_git.root) == before


def test_changed_source_requires_a_new_declared_version(benchmark_git: GitRepo) -> None:
    change_source(benchmark_git, f'web/{SCENARIO}/environment/challenge')
    benchmark_git.commit()
    with pytest.raises(ValueError, match='Source changed without a new image version'):
        _ = builds.select(benchmark_git.root, base=benchmark_git.base)


def test_changed_image_reference_is_selected(benchmark_git: GitRepo) -> None:
    bump(benchmark_git.root, 'challenge')
    benchmark_git.commit()
    assert list(builds.select(benchmark_git.root, base=benchmark_git.base)) == [
        f'{SCENARIO}-challenge'
    ]


def test_historical_digest_references_can_migrate_to_named_versions(benchmark_git: GitRepo) -> None:
    root = benchmark_git.root
    before = native_files(root)
    targets = builds.sources(root)
    for path in before:
        text = path.read_text()
        for target in targets.values():
            text = text.replace(target.tag, f'{REGISTRY}@sha256:' + '1' * 64)
        _ = path.write_text(text)
    # This is a legacy baseline: different contexts may have the same image digest.
    benchmark_git.commit()
    base = benchmark_git.git('rev-parse', 'HEAD')
    for path, contents in before.items():
        _ = path.write_bytes(contents)
    assert len(builds.select(root, base=base)) == 4


@pytest.mark.parametrize('revision', [None, 'HEAD'])
def test_scenario_selection_is_read_only(benchmark_git: GitRepo, revision: str | None) -> None:
    assert len(builds.sources(benchmark_git.root, SCENARIO, revision=revision)) == 4
    assert builds.sources(benchmark_git.root, 'other', revision=revision) == {}


@pytest.mark.parametrize('collection', ['web', 'netpen'])
def test_retired_collection_packages_are_rejected(collection: str) -> None:
    reference = f'ghcr.io/dreadnode/scopebench-{collection}:agent-v1'
    with pytest.raises(ValueError, match='Expected a named image version'):
        _ = version_tag(reference)


@pytest.mark.parametrize('reference', [None, 1, 'invalid', f'{REGISTRY}:latest'])
def test_versions_are_readable(reference: object) -> None:
    with pytest.raises(ValueError, match='Expected a named image version'):
        _ = version_tag(reference)


def test_build_selection_requires_a_separate_verifier(image_root: Path) -> None:
    manifest = task_path(image_root) / 'task.toml'
    _ = manifest.write_text(manifest.read_text().split('[verifier.environment]')[0])
    with pytest.raises(ValueError, match='Expected a separate verifier environment'):
        _ = builds.sources(image_root)


@pytest.mark.parametrize(
    'defect',
    [
        'missing',
        'number',
        'conflict',
        'shared-tag',
        'escape',
        'symlink',
        'dockerfile',
        'build-option',
    ],
)
def test_invalid_build_bindings_are_rejected(image_root: Path, defect: str) -> None:
    manifest = task_path(image_root) / 'task.toml'
    path = task_path(image_root) / 'environment/docker-compose.yaml'
    native = read_compose(path)
    if defect == 'missing':
        del native.services['app']['image']
    elif defect == 'number':
        native.services['app']['image'] = 3
    elif defect == 'conflict':
        _ = manifest.write_text(manifest.read_text().replace('agent-v1', 'agent-v2'))
    elif defect == 'shared-tag':
        native.services['app']['image'] = f'{REGISTRY}:agent-v1'
    elif defect == 'symlink':
        (image_root / 'environments/agent/link').symlink_to('Dockerfile')
    elif defect == 'dockerfile':
        (image_root / 'environments/agent/Dockerfile').unlink()
    else:
        recipe = (
            image_root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml'
        )
        text = recipe.read_text()
        if defect == 'escape':
            _ = recipe.write_text(text.replace('./challenge', '../../../outside'))
        else:
            _ = recipe.write_text(
                text.replace(
                    'context: ./challenge', 'context: ./challenge\n      args: {EXTRA: value}'
                )
            )
    _ = path.write_text(yaml.safe_dump(native.document(), sort_keys=False))
    with pytest.raises((ValueError, ValidationError)):
        _ = builds.sources(image_root)


def test_conflicting_flattened_context_names_fail(image_root: Path) -> None:
    original = image_root / 'lite/environments' / 'web' / SCENARIO / 'environment/challenge'
    _ = shutil.copytree(original, image_root / 'lite/environments' / f'{SCENARIO}-challenge')
    recipe = image_root / 'lite/environments' / 'web' / SCENARIO / 'environment/docker-compose.yaml'
    text = recipe.read_text().replace(
        'context: ./gateway', f'context: ../../../{SCENARIO}-challenge'
    )
    _ = recipe.write_text(text)
    with pytest.raises(ValueError, match='Conflicting image references or context names'):
        _ = builds.sources(image_root)


def test_local_build_loads_unpublished_tags_without_registry_access(
    image_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bump(image_root, 'challenge')
    before = native_files(image_root)
    calls: list[list[str]] = []

    def run(args: list[str], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[str]:
        assert cwd == image_root
        assert check
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr('scopebench_contribution.builds.subprocess.run', run)
    assert builds.run(['local', '--scenario', SCENARIO], root=image_root) == 0
    assert len(calls) == 4
    assert all('--load' in call and '--push' not in call for call in calls)
    assert any(f'{REGISTRY}:auth-user-export-challenge-v2' in call for call in calls)
    assert native_files(image_root) == before


def test_installed_commands_use_the_selected_checkout(image_root: Path) -> None:
    result = subprocess.run(
        ['scopebench-build', 'matrix'], cwd=image_root, capture_output=True, text=True, check=True
    )
    assert len(TypeAdapter(list[dict[str, str]]).validate_json(result.stdout)) == 4
    result = subprocess.run(
        ['scopebench-pairs'], cwd=image_root, capture_output=True, text=True, check=True
    )
    assert 'Validated 1 pairs and 4 shared images' in result.stdout


def test_empty_ci_matrix_is_valid(
    benchmark_git: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert builds.run(['matrix', '--base', benchmark_git.base], root=benchmark_git.root) == 0
    assert capsys.readouterr().out.strip() == '[]'


def test_unknown_base_is_an_error(benchmark_git: GitRepo) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _ = builds.select(benchmark_git.root, base='f' * 40)


def test_native_reader_preserves_fields(image_root: Path) -> None:
    doc = read_task(task_path(image_root) / 'task.toml')
    assert 'metadata' in doc
    assert 'solution' in doc
    assert len(builds.sources(image_root)) == 4


def test_distinct_scenarios_with_the_same_flag_keep_separate_rubrics(
    image_root: Path,
) -> None:
    original = task_path(image_root, 'scoped')
    other = image_root / 'lite/tasks/web/other-scope'
    _ = shutil.copytree(original, other)
    other_scenario = 'other-scenario'
    manifest = other / 'task.toml'
    write(manifest, manifest.read_text(encoding='utf-8').replace(SCENARIO, other_scenario))
    write(other / 'instruction.md', 'Review only the other workspace.')
    write(
        image_root / 'lite/environments' / 'web' / other_scenario / 'rubric.md',
        'Other scope boundaries.',
    )
    first = TaskConfig.model_validate(read_task(original / 'task.toml'))
    second = TaskConfig.model_validate(read_task(manifest))
    assert (
        first.verifier.env['SCOPEBENCH_EXPECTED_FLAG']
        == second.verifier.env['SCOPEBENCH_EXPECTED_FLAG']
    )
    builds.prepare(image_root, builds.VERIFIER_CONTEXT)
    context = image_root / builds.VERIFIER_CONTEXT
    original_spec = DOCUMENT.validate_json(
        (context / 'specifications' / f'{SCENARIO}.json').read_bytes()
    )
    other_spec = DOCUMENT.validate_json(
        (context / 'specifications' / f'{other_scenario}.json').read_bytes()
    )
    assert original_spec == {
        'instruction': (original / 'instruction.md').read_text(encoding='utf-8'),
        'rubric': (image_root / 'lite/environments' / 'web' / SCENARIO / 'rubric.md').read_text(
            encoding='utf-8'
        ),
    }
    assert other_spec == {
        'instruction': 'Review only the other workspace.',
        'rubric': 'Other scope boundaries.',
    }


def test_duplicate_scoped_specifications_are_rejected(image_root: Path) -> None:
    _ = shutil.copytree(
        task_path(image_root, 'scoped'), image_root / 'lite/tasks/web/duplicate-scope'
    )
    with pytest.raises(ValueError, match='Duplicate scoped specification'):
        _ = builds.verifier_specifications(image_root)


@pytest.mark.parametrize('kind', ['rubric', 'scoped-instruction'])
def test_scope_inputs_require_a_new_verifier_version(benchmark_git: GitRepo, kind: str) -> None:
    root = benchmark_git.root
    target = (
        root / 'lite/environments' / 'web' / SCENARIO / 'rubric.md'
        if kind == 'rubric'
        else task_path(root, 'scoped') / 'instruction.md'
    )
    initial = builds.context_tree(root, builds.VERIFIER_CONTEXT)
    write(target, target.read_text(encoding='utf-8') + '\nChanged scope boundary\n')
    assert builds.context_tree(root, builds.VERIFIER_CONTEXT) != initial
    with pytest.raises(ValueError, match='Source changed without a new image version'):
        _ = builds.select(root, base=benchmark_git.base)
    bump(root, 'verifier')
    assert set(builds.select(root, base=benchmark_git.base)) == {'verifier'}


def test_raw_instruction_does_not_change_verifier_inputs(benchmark_git: GitRepo) -> None:
    root = benchmark_git.root
    initial = builds.context_tree(root, builds.VERIFIER_CONTEXT)
    write(task_path(root) / 'instruction.md', 'Changed raw objective')
    assert builds.context_tree(root, builds.VERIFIER_CONTEXT) == initial
    assert builds.select(root, base=benchmark_git.base) == {}


def test_removing_a_scoped_specification_reuses_the_verifier(benchmark_git: GitRepo) -> None:
    root = benchmark_git.root
    shutil.rmtree(task_path(root, 'scoped'))
    benchmark_git.commit()
    assert builds.verifier_specifications(root) == {}
    assert builds.select(root, base=benchmark_git.base) == {}


def test_prepare_replaces_stale_bundles_and_omits_oracles(image_root: Path) -> None:
    directory = image_root / builds.VERIFIER_CONTEXT / 'specifications'
    write(directory / 'stale.json', '{}')
    assert builds.run(['prepare'], root=image_root) == 0
    expected = builds.verifier_specifications(image_root)
    assert {path.stem: path.read_text() for path in directory.glob('*.json')} == expected
    assert not list((image_root / builds.VERIFIER_CONTEXT).rglob('solve.sh'))


@pytest.mark.parametrize('context', ['environments/agent', builds.VERIFIER_CONTEXT])
def test_fingerprint_command_includes_external_verifier_inputs(
    benchmark_git: GitRepo, capsys: pytest.CaptureFixture[str], context: str
) -> None:
    assert builds.run(['fingerprint', '--context', context], root=benchmark_git.root) == 0
    assert capsys.readouterr().out.strip() == builds.context_tree(benchmark_git.root, context)

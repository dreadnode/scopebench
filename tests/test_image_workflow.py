"""Publication guards skip existing versions and never receive task-write permission."""

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

from scopebench_contribution import builds
from scopebench_contribution.native import DOCUMENT, REGISTRY, Document
from tests.benchmark_helpers import ROOT, write
from tests.conftest import GitRepo


@pytest.mark.parametrize(
    'case',
    [
        'missing',
        'matching',
        'multi-platform',
        'conflict',
        'missing-label',
        'unavailable',
        'unauthorized',
        'malformed',
    ],
)
def test_publication_guard_only_inspects_existing_versions(
    git_repo: GitRepo, tmp_path: Path, case: str
) -> None:
    git_repo.write('environments/agent/Dockerfile', 'FROM python:3.13-slim\n')
    git_repo.commit()
    tree = git_repo.git('rev-parse', 'HEAD:environments/agent')
    image: dict[str, object] = {
        'config': {
            'Labels': {'io.scopebench.context-tree': tree if case != 'conflict' else 'different'}
        }
    }
    if case == 'missing-label':
        image = {'config': {}}
    if case == 'multi-platform':
        image = {'linux/amd64': image}
    errors = {
        'missing': 'manifest unknown',
        'unavailable': 'connection refused',
        'unauthorized': '403 Forbidden',
    }
    fake = tmp_path / 'bin/docker'
    write(
        fake,
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "$CALLS_FILE"
if [[ -n "$INSPECT_ERROR" ]]; then
  printf '%s\\n' "$INSPECT_ERROR" >&2
  exit 1
fi
printf '%s\\n' "$IMAGE_RECORD"
""",
    )
    fake.chmod(0o755)
    output = tmp_path / 'output'
    output.touch()
    calls = tmp_path / 'calls'
    env = {
        **os.environ,
        'PATH': str(fake.parent) + os.pathsep + os.environ['PATH'],
        'CONTEXT': 'environments/agent',
        'IMAGE': f'{REGISTRY}:agent-v2',
        'CALLS_FILE': str(calls),
        'INSPECT_ERROR': errors.get(case, ''),
        'IMAGE_RECORD': 'invalid JSON' if case == 'malformed' else json.dumps({'image': image}),
        'GITHUB_OUTPUT': str(output),
    }
    result = subprocess.run(
        ['scopebench-build', 'published', '--context', 'environments/agent'],
        cwd=git_repo.root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert calls.read_text().splitlines() == [
        'buildx',
        'imagetools',
        'inspect',
        f'{REGISTRY}:agent-v2',
        '--format',
        '{{json .}}',
    ]
    if case in ('missing', 'matching', 'multi-platform'):
        assert result.returncode == 0, result.stderr
        expected = 'false' if case == 'missing' else 'true'
        assert output.read_text() == f'published={expected}\ntree={tree}\n'
    else:
        assert result.returncode != 0
        assert output.read_text() == ''


def test_workflow_builds_prs_without_publication_or_task_mutation() -> None:
    path = ROOT / '.github/workflows/harbor-images.yml'
    text = path.read_text()
    doc = DOCUMENT.validate_python(cast(object, yaml.load(text, Loader=yaml.BaseLoader)))
    jobs = cast(Document, doc['jobs'])
    build = cast(Document, jobs['build'])
    publish = cast(Document, jobs['publish'])
    events = cast(Document, doc['on'])
    assert 'tags' not in cast(Document, events['push'])
    assert cast(Document, doc['permissions']) == {'contents': 'read'}
    assert cast(Document, publish['permissions']) == {'contents': 'read', 'packages': 'write'}
    assert "github.event_name == 'pull_request'" in str(build['if'])
    assert "github.event_name != 'pull_request'" in str(publish['if'])
    assert "github.ref == 'refs/heads/main'" in str(publish['if'])
    assert "needs.plan.outputs.images != '[]'" in str(build['if'])
    for job in (build, publish):
        steps = cast(list[Document], job['steps'])
        action = next(
            step
            for step in steps
            if str(step.get('uses', '')).startswith('docker/build-push-action@')
        )
        settings = cast(Document, action['with'])
        assert settings['context'] == '${{ matrix.image.context }}'
        assert any(
            '--group benchmark scopebench-build prepare' in str(step.get('run', ''))
            for step in steps
        )
        assert settings['tags'] == '${{ matrix.image.tag }}'
        assert settings['push'] == ('false' if job is build else 'true')
    for operation in ('git push', 'git commit', 'contents: write', 'scopebench-images'):
        assert operation not in text


@pytest.mark.parametrize('case', ['matching', 'changed-rubric', 'legacy-tree'])
def test_verifier_publication_guard_includes_the_rubric_bundle(
    image_root: Path, tmp_path: Path, case: str
) -> None:
    repo = GitRepo(image_root)
    _ = repo.git('init', '-q', '--initial-branch=main')
    _ = repo.git('config', 'user.name', 'Publication test')
    _ = repo.git('config', 'user.email', 'publication@example.invalid')
    repo.commit()
    tree = builds.context_tree(image_root, builds.VERIFIER_CONTEXT)
    published = tree
    if case == 'legacy-tree':
        published = repo.git('rev-parse', 'HEAD:environments/verifier')
    elif case == 'changed-rubric':
        rubric = next((image_root / 'lite/environments').rglob('rubric.md'))
        write(rubric, rubric.read_text() + '\nUpdated scope boundary\n')
    binaries = tmp_path / 'bin'
    write(
        binaries / 'uv',
        '#!/usr/bin/env bash\nset -euo pipefail\nshift 5\nexec "$@"\n',
    )
    write(
        binaries / 'docker',
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$IMAGE_RECORD"\n',
    )
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    output = tmp_path / 'output'
    output.touch()
    result = subprocess.run(
        ['scopebench-build', 'published', '--context', builds.VERIFIER_CONTEXT],
        cwd=image_root,
        env={
            **os.environ,
            'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
            'CONTEXT': builds.VERIFIER_CONTEXT,
            'IMAGE': f'{REGISTRY}:verifier-v2',
            'IMAGE_RECORD': json.dumps(
                {'image': {'config': {'Labels': {'io.scopebench.context-tree': published}}}}
            ),
            'GITHUB_OUTPUT': str(output),
        },
        capture_output=True,
        text=True,
    )
    if case == 'matching':
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f'published=true\ntree={tree}\n'
    else:
        assert result.returncode != 0
        assert 'different build inputs' in result.stderr
        assert output.read_text() == ''

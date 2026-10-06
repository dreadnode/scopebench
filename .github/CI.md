# ScopeBench CI

ScopeBench has two CI workflows. Add another when a specific maintenance job needs automation.

| Workflow | Responsibility |
|---|---|
| **Task validation** (`static-checks.yml`) | Lint shared tooling, run the benchmark test suite once, and validate native task/config schemas, complete capability/scope pairs, shared runtime bindings, image planning, grading, and provider contracts. |
| **Harbor images** (`harbor-images.yml`) | Build changed shared image versions on PRs without publishing. Publish on main pushes or a maintainer dispatch on main. |

Maintainers handle proposal discussion and task-quality review, triage repairs, and assess live validation, using the discussion, issue, and PR templates to gather the evidence for those decisions. These decisions remain manual. CI does not automate proposal approval, contribution-policy statuses, author notifications, or issue assignments.

The [Task Proposals form](DISCUSSION_TEMPLATE/task-proposals.yml) asks for relevant experience, domain, and a short pitch, with a link to the contribution appendix for the acceptance criteria. The filename must match the category slug. GitHub applies the form to `task-proposals` once it reaches the default branch.

## Local validation

```bash
uv sync --locked
uv run --locked scopebench-pairs
uv run --locked pytest
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

Check image planning with `uv run --locked pytest tests/test_builds.py`. `uv run --locked basedpyright` is an optional local type check, while local Ruff configs limit linting and formatting to tooling and tests. Neither typing nor coverage percentages gate CI.

The suite uses inert task fixtures. Fake provider operations and mocked Docker/Harbor processes keep tests from starting environments, provisioning infrastructure, calling model providers, or publishing images. Static validation reads your current checkout, including uncommitted changes in both task sets. Remove one pair member and validation fails.

Harbor defines native task/config/result schemas. ScopeBench checks pairing, shared definitions, versioned image references, verifier settings, artifacts, and commented canaries, without imposing an arbitrary token limit on kebab-case task names. Authors choose the networks. Maintainers review each environment's isolation, and CI rejects local builds, host bind mounts, published host ports, privileged containers, and host networking in task-local Compose files.

## Image publication

Main build sources live under `environments/`; Lite scenarios live under `lite/environments/`. Both collections use the root agent and verifier sources. Task-local Compose files and native descriptors point to published versions, and the build plan lists each shared context once even when several scenarios or both conditions use it.

```bash
uv run --locked scopebench-build matrix
uv run --locked scopebench-build matrix --scenario <scenario>
uv run --locked scopebench-build local --scenario <scenario>
```

Changed inputs need a new version. Publication reuses an existing version only when its recorded source fingerprint matches, and cannot overwrite a conflicting version or edit task descriptors. Scoped prompts and rubrics count as inputs. Both are bundled into the shared verifier image for evaluator use, so a change to either needs a new verifier version too.

PR builds have read-only permissions. Validation uses the same permissions, and neither receives model credentials, while the publish job gets package-write permission, runs only on main, and checks package visibility. Actions are pinned to revisions. Python dependencies are locked, and dispatching the workflow on another branch does not publish images.

The planner also compares historical sources, so moving a build context in a PR cannot conceal changed inputs behind a previously published version.

## Live validation and other backends

Use `scopebench-controls --scenario <scenario>` for local Docker oracle/no-op controls. These check mechanical rewards only. Validate the trajectory judge separately, start each live trial from a fresh environment, and bring the run evidence to maintainer review outside CI.

Package Ludus definitions locally with `scopebench-environments package --backend ludus --output dist/ludus`. CI does not provision VM/cloud environments. It does not package them either, while the environment package handles bindings, portable definitions, and provider lifecycles independently of the CI setup. The shared `tasks.manifests` and `schemas.Payload` imports remain available. Cloud automation needs a selected provider.

## Required checks after merging this reset

Keep the validation job's `checks` requirement, and remove any retired `Contribution policy`, `workflow-tests`, `proposal-link`, and `detect` requirements from branch protection or rulesets when the reset merges. Missing statuses can block merges. Make this settings change on GitHub, since edits in the checkout cannot update the remote repository's branch protection or rulesets.

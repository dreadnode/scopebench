# Shared scenario environments

Define each main benchmark scenario once. Its home is `environments/<domain>/<scenario>/`, with `scenario.toml`, runtime `environment/`, and reference `solution/` in one shared tree. Paired scenarios keep `rubric.md` in that shared tree; a scoped-only Ludus task keeps its rubric beside `instruction.md` in `tasks/<domain>/`. Capability and scope tasks live under `tasks/<domain>/`, with the same literal expected artifact value in `SCOPEBENCH_EXPECTED_FLAG` under `[verifier.env]`. Pilot scenarios live separately under [`lite/environments/<domain>/<scenario>/`](../lite/environments/README.md), with their pairs in [`lite/tasks/`](../lite/tasks/).

## Definition identity

Record backend, readiness, and version in `scenario.toml`, then match native task metadata to the same domain, scenario, backend, and state so static checks can identify the pair. Exactly two conditions are required. They must agree on resources and runtime inputs, including after a shared scenario change.

Keep both runtimes in sync. Docker definitions contain build contexts and Compose topology, while task-local Compose files reference images and the build plan lists each shared context once. Shared agent and verifier sources live in `agent/` and `verifier/`, with the verifier's evaluator-only bundle built separately.

Ludus and cloud bindings are identical. Their `environment/reference.json` files use schema version 1 and record a domain-qualified scenario identity, backend, definition version, and SHA-256 fingerprint of runtime file paths, contents, and executable modes. A runtime change needs a new version and updated bindings in both tasks, with review.

## Portable runtime packages

`scopebench-package` copies native tasks under `tasks/` (or `lite/tasks/`) and their shared definitions under the sibling `environments/` tree. Each definition stays as ordinary files in its scenario's `environment/` folder, with `scenario.toml` beside it. Shared definitions are copied once for both task variants. Environment exports exclude scenario-level solutions and rubrics.

The Ludus environment reads the task's `environment/reference.json`, resolves its scenario from the sibling `environments/` tree, and verifies the fingerprint after copying the raw files into provider-owned trial storage. Checkouts and portable folder copies use the same layout and loader.

The concrete [`scopebench_ludus`](../src/scopebench_ludus/README.md) package creates an isolated native range for each Harbor trial and provides guest commands and files. Runtime references and portable exports live in `scopebench_contribution.runtime`.

## Domain build guides

| Domain | Guide |
|---|---|
| Web | [Web / Docker](web/README.md) |
| Linux and network | [Linux / Docker](netpen/linux/README.md) |
| Windows and AD | [Windows / Ludus](netpen/windows/README.md) |
| Cloud | [Cloud](cloud/README.md) |

Start with [CONTRIBUTING.md](../CONTRIBUTING.md) for the contribution process.

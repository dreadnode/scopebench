# Shared scenario environments

Define each main benchmark scenario once. Its home is `environments/<domain>/<scenario>/`, with `scenario.toml`, runtime `environment/`, reference `solution/`, and scope `rubric.md` in one shared tree. Its capability and scope tasks live under `tasks/<domain>/`, with the same literal expected artifact value in `SCOPEBENCH_EXPECTED_FLAG` under `[verifier.env]`. Pilot scenarios live separately under [`lite/environments/<domain>/<scenario>/`](../lite/environments/README.md), with their pairs in [`lite/tasks/`](../lite/tasks/).

## Definition identity

Record backend, readiness, and version in `scenario.toml`, then match native task metadata to the same domain, scenario, backend, and state so static checks can identify the pair. Exactly two conditions are required. They must agree on resources and runtime inputs, including after a shared scenario change.

Keep both runtimes in sync. Docker definitions contain build contexts and Compose topology, while task-local Compose files reference images and the build plan lists each shared context once. Shared agent and verifier sources live in `agent/` and `verifier/`, with the verifier's evaluator-only bundle built separately.

Ludus and cloud bindings are identical. Their `environment/reference.json` files use schema version 1 and record a domain-qualified scenario identity, backend, definition version, and SHA-256 fingerprint of runtime file paths, contents, and executable modes. A runtime change needs a new version and updated bindings in both tasks, with review.

## Portable runtime packages

`scopebench-environments package` copies native tasks to an output directory and embeds a deterministic `definition.tar` made only from the shared runtime `environment/`, leaving authoring sources untouched. Solutions and root rubrics stay out.

The provider base verifies the fingerprint. It finds the shared definition in a checkout or embedded bundle and puts it in provider-owned trial storage, supporting repository runs and portable archives without external relative links or duplicate task-local sources in Git.

Extend the bases in [`src/scopebench_environments/`](../src/scopebench_environments/README.md) to supply Ludus/cloud instance lifecycles, command execution, and file transport, using each trial's unique Harbor session identity for a fresh isolated baseline. Trials can share definitions, but each must have its own mutable instance.

## Domain build guides

| Domain | Guide |
|---|---|
| Web | [Web / Docker](web/README.md) |
| Linux and network | [Linux / Docker](netpen/linux/README.md) |
| Windows and AD | [Windows / Ludus](netpen/windows/README.md) |
| Cloud | [Cloud](cloud/README.md) |

Start with [CONTRIBUTING.md](../CONTRIBUTING.md) for the contribution process.

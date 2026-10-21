# Windows and AD Ludus environments

Follow [CONTRIBUTING.md](../../../CONTRIBUTING.md) and the [shared environment contract](../../README.md) when authoring Windows scenarios. Add new task pairs under [`tasks/netpen/windows/`](../../../tasks/netpen/windows/) with shared definitions under this directory. Docker evaluation configs exclude Ludus tasks.

Keep each scenario's Ludus definitions, roles, and reference solution together. Keep Ludus scoped prompts, rubrics, and verifier specifications in the scoped task directory. `task_set = "scoped"` in `scenario.toml` is available for a task that intentionally has no capability pair; paired tasks use `task_set = "pair"`.

## Authoring

Put VM/range definitions and provisioning assets under `<scenario>/environment/`, keeping oracle and evaluator-only files in their dedicated locations and setting `backend = "ludus"` for the shared scenario. Both bindings must be identical.

Generate `reference.json` with `scopebench_contribution.runtime.runtime_reference`, which lets CI check each native task's binding against the shared source fingerprint, and update both bindings with a new semantic version whenever the definition changes.

## Packaging and provider integration

Package definitions locally from the repository root with `uv run --locked scopebench-package --backend ludus --output dist/ludus`, while CI validates bindings without packaging, deploying, or restoring ranges. The Ludus environment verifies runtime references and can deploy arbitrary native ranges.

`scopebench_ludus.environment:LudusEnvironment` implements the Harbor lifecycle directly. It verifies the task reference, snapshots runtime files, deploys a trial-owned native range and uses its exported inventory for guest commands and files. `provider.toml` selects the execution host and optional interaction requirements.

Prompts that require Mythic/Apollo declare `mode = "mythic-apollo"` and `preseeded_callback = true`; shell environments can use `mode = "shell"` with callbacks disabled. Match each task's instructions to its adapter. Ludus does not require Mythic or Apollo.

Keep lifecycle/controller credentials outside agent-visible assets, and validate fresh per-trial baselines, interaction, artifacts, separate grading, failure cleanup, and complete capability/scope evidence before configuring a concrete provider import path in a job config under `configs/`. Until then, descriptors stay in development.

## Instance inspection

Keep the Ludus API key in the controller environment, using your secret manager and excluding credentials from source control, shared runtime files, task manifests, and agent-visible files. Certificate verification is on by default.

`uv run --locked --group ludus scopebench-ludus inspect --url https://ludus.example.test:8080` checks the version and available templates without changing ranges, with `--insecure` available to opt out only for a locally trusted self-signed instance. Provider settings describe interface requirements.

The concrete provider uses HTTP for Ludus operations and Ansible for Linux/Windows command and file transport. Select one execution host/group in `provider.toml`; the range itself can contain any native Ludus topology.

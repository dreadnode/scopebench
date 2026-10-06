# Windows and AD Ludus environments

Follow [CONTRIBUTING.md](../../../CONTRIBUTING.md) and the [shared environment contract](../../README.md) when authoring Windows scenarios. Add new task pairs under [`tasks/netpen/windows/`](../../../tasks/netpen/windows/) with shared definitions under this directory. Docker evaluation configs exclude Ludus tasks.

Keep each scenario's Ludus definitions, roles, prompts, reference solution, and verification assets together. Record the selected scoped prompt in `scenario.toml`.

## Authoring

Put VM/range definitions and provisioning assets under `<scenario>/environment/`, keeping oracle and evaluator-only files in their dedicated locations and setting `backend = "ludus"` for the shared scenario. Both bindings must be identical.

Generate `reference.json` with `binding_for`, which lets CI check each native task's binding against the shared source fingerprint, and update both bindings with a new semantic version whenever the definition changes.

## Packaging and provider integration

Package definitions locally from the repository root with `uv run --locked scopebench-environments package --backend ludus --output dist/ludus`, while CI validates bindings without packaging, deploying, or restoring ranges. The base is abstract.

`LudusEnvironmentBase` verifies and resolves definitions, then delegates lifecycle operations to a concrete provider that must implement fresh-baseline provisioning, release, and Harbor command and file transport. Read `provider.toml` through `definition`. Its `[interaction]` settings specify adapter mode, whether a pre-seeded callback is required, and the names of external environment variables the adapter needs.

Prompts that require Mythic/Apollo declare `mode = "mythic-apollo"` and `preseeded_callback = true`; shell environments can use `mode = "shell"` with callbacks disabled. Match each task's instructions to its adapter. Ludus does not require Mythic or Apollo.

Keep lifecycle/controller credentials outside agent-visible assets, and validate fresh per-trial baselines, interaction, artifacts, separate grading, failure cleanup, and complete capability/scope evidence before configuring a concrete provider import path in a job config under `configs/`. Until then, descriptors stay in development.

## Instance inspection

Keep the Ludus API key in the controller environment, using your secret manager and excluding credentials from source control, runtime bundles, task manifests, and agent-visible files. Certificate verification is on by default.

`uv run --locked scopebench-environments inspect-ludus --url https://192.168.0.48:8080` checks the version and available templates without changing ranges, with `--insecure` available to opt out only for a locally trusted self-signed instance. Provider settings describe interface requirements only. They do not create C2 payloads or callbacks.

Transport implementations can live in `src/scopebench_environments/` once their lifecycle and grading contracts have been verified.

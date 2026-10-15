# ScopeBench Ludus environment

Harbor loads `scopebench_ludus.environment:LudusEnvironment`.

The HTTP API client is included in `scopebench_ludus.client`, with its tests under
`tests/ludus/test_client.py`. It uses HTTPX and Pydantic and has no Harbor or
Ansible dependencies. The `ludus` dependency group installs its public Python
dependencies, Harbor and the Ansible controller tools.

Ludus deploys the native range YAML and roles on the server. The `ansible` folder
handles Harbor's commands and file transfers using the exported guest inventory.
It supports Linux and Windows execution hosts inside arbitrary ranges.

The authored files have separate roles:

| File | Model | Purpose |
|---|---|---|
| `tasks/.../environment/reference.json` | `RuntimeReference` | Pin a task to a scenario version and runtime fingerprint. |
| `environments/.../environment/provider.toml` | `LudusConfig` | Choose range assets, bundled roles, execution host and hooks. |
| `environments/.../environment/ludus/range-config.yml` | Native Ludus YAML | Define VMs, networks, roles and variables. |

Bundled roles are uploaded under trial-specific names. `remap_roles` updates the
VM and dependency references to those names, keeping each trial's cleanup isolated.

Benchmark authoring lives in `scopebench_contribution`: `scenarios.py` discovers
scenario manifests; `runtime.py` fingerprints, snapshots and exports runtime files.

Provider tests generate inert definitions in temporary directories and mock the
Ludus API. The local Ansible test uses only the controller's temporary files.

Install controller dependencies with `uv sync --locked --group ludus`, then run
`uv run --locked --group ludus scopebench-ludus setup` to install the Windows
Ansible collection. Keep `LUDUS_URL` and `LUDUS_API_KEY` in the controller
environment. TLS verification is on by default; `LUDUS_VERIFY=false` opts out for
a trusted local server. `LUDUS_DEPLOY_TIMEOUT_SEC` sets the deployment deadline
(1800 seconds by default); `LUDUS_WINRM_PROXY` optionally selects a WinRM proxy.

Use `scopebench-ludus inspect --url <https-url>` for read-only server inspection.
To retry interrupted cleanup, run
`scopebench-ludus cleanup --resources <trial-directory>/ludus-resources/<session-hash>.json`
with the controller credentials. Each environment has its own record, including
agent and verifier environments sharing a trial directory. Cleanup errors identify
the record to retry. Recovery checks ownership before deleting its range and bundled roles.

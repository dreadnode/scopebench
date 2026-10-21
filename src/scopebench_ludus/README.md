# ScopeBench Ludus environment

Harbor loads `scopebench_ludus.environment:LudusEnvironment` for a direct
provider check. For a native task with a separate Docker verifier, use
`scopebench_ludus.routing:ScopeBenchEnvironment` so Harbor selects Ludus for
the agent and Docker for the verifier.

The HTTP API client is included in `scopebench_ludus.client`, with its tests under
`tests/ludus/test_client.py`. It uses HTTPX and Pydantic and has no Harbor or
Ansible dependencies. The `ludus` dependency group installs its public Python
dependencies, Harbor and the Ansible controller tools.

Ludus deploys the native range YAML and roles on the server. The `ansible` folder
handles controller commands and file transfers using the exported guest inventory.
For a task with `[sandbox]` in `provider.toml`, Harbor's Docker environment runs
the agent. The provider resolves the assigned Mythic IP, adds it to the task's
agent allowlist, and copies only the operator connection and API guide into the
sandbox. Tasks without `[sandbox]` retain direct guest execution.

Tasks can set Harbor's `[environment].network_mode = "allowlist"` with exact
hostnames in `allowed_hosts`. The sandbox enforces this policy through Harbor's
Docker egress control and adds only the range's Mythic IP. For Ludus ranges,
the provider validates that every VM uses `testing.block_internet: true`
and at least one VM has `testing.snapshot: true`, then starts
Ludus testing mode and confirms its domain allowlist before Harbor installs or
runs the agent. Testing mode remains active while a cached range is reset and
powered off. Ludus testing mode requires a snapshot target, so the C2 guest
gets one disk-only testing snapshot. The existing disk-only range snapshot
still owns restoration between trials. If testing
is disabled or the allowlist changes outside the trial, the next trial fails before agent
execution. Range provisioning still needs outbound access before testing begins.
Ludus implements domain allowances through pinned IPs and also permits the
certificate revocation endpoints for those domains, so this is a restricted
egress policy rather than a literal zero-internet environment.

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

`LUDUS_LIFECYCLE=delete` is the default and retains the existing Harbor cleanup
behavior. Set `LUDUS_LIFECYCLE=snapshot` to keep one range per Ludus user,
scenario runtime fingerprint, and execution OS. The provider takes a disk-only
snapshot of every VM after setup, rolls back before each reuse, and rolls back
and powers off after each trial. A local lock prevents concurrent use on one
controller host; share one state directory and locking filesystem if multiple
controllers will run the same cache. `force_build` deletes the cached range and
constructs a new one. The credential-free range record and lock live under
`~/.local/state/scopebench/ludus` by default. Set `LUDUS_STATE_DIR` to an
absolute private directory to move them. If a reset fails, the cache is marked
dirty and reuse is blocked until `force_build` or explicit cleanup. For
`mythic-apollo` tasks with a pre-ceded callback, the provider also requires a
successful Apollo command round trip before handing over a new or reused range.

Use `scopebench-ludus inspect --url <https-url>` for read-only server inspection.
To retry interrupted cleanup, run
`scopebench-ludus cleanup --resources <trial-directory>/ludus-resources/<session-hash>.json`
with the controller credentials. Each environment has its own record, including
agent and verifier environments sharing a trial directory. Cleanup errors identify
the record to retry. Recovery checks ownership before deleting its range and bundled roles.
In snapshot mode, use the persistent record named in cleanup errors; cleanup
rejects a cache that is currently in use.

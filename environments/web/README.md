# Web Docker environments

Define the shared scenario here. Put capability and scope task descriptors in the appropriate task set, following the [contribution guide](../../CONTRIBUTING.md) so both conditions use the same topology and versions.

## Build inputs

Compose belongs in `<scenario>/environment/docker-compose.yaml`, with build contexts beside it and relative paths to shared `environments/agent/` sources resolved from that runtime directory. Task-local Compose must reference images. Local build contexts and host bind mounts are forbidden there, and maintainers review isolation between the agent and backend on the networks you choose.

CI does not prescribe network names. Use readable versioned tags in `ghcr.io/dreadnode/scopebench`, starting at `-v1`, and declare a new version whenever build inputs change. Scoped prompts and rubrics count too. Changes to either require a new shared verifier version, with the planner reading image references directly from authored tasks.

## Validate

Run `uv run --locked scopebench-pairs` and `uv run --locked pytest` from the repository root, then inspect the build plan with `uv run --locked scopebench-build matrix --scenario <scenario>`. CI leaves benchmark tasks stopped.

Maintainers can build with `uv run --locked scopebench-build local --scenario <scenario>` and check mechanical rewards with `uv run --locked scopebench-controls --scenario <scenario>`. Trajectory judging needs separate evidence. Review it before marking the pair ready.

Keep reference solutions in shared `<scenario>/solution/` and evaluator scope specifications in `rubric.md`, with leading comments as required by the [benchmark data canary policy](../../CONTRIBUTING.md#benchmark-data-canaries). Set the same actual flag or expected artifact text in `SCOPEBENCH_EXPECTED_FLAG` under `[verifier.env]` in both task manifests. The verifier compares the submitted text directly after stripping surrounding whitespace.

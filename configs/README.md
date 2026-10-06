# Benchmark jobs

## Run with Harbor

Set `JOB_CONFIG` to the configuration path for the selected task set, then run it from the repository root with Harbor:

```bash
uvx harbor run -c "$JOB_CONFIG" \
  --ve 'ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}'
```

Published Lite images use `ghcr.io/dreadnode/scopebench`.

The job config defines the task set, backend, agent, and model. Ensure Docker can pull any images the selected tasks require. A paired job runs both conditions: capability measures objective completion, while scope also judges the agent's trajectory against the scenario's authorization boundary. The `--ve` argument passes model credentials to the verifier separately from the actor.

## Datasets and backends

The current Docker pilot configuration is `paired_lite.json`; the [repository README](../README.md) gives a concrete run command. Each job uses one backend. Add provider-specific VM/cloud job configs here when their task pairs are validated.

The [Windows/AD guide](../environments/netpen/windows/README.md) covers Ludus authoring and provider requirements, while the [Cloud guide](../environments/cloud/README.md) sets the requirements for the first cloud tasks and a concrete provider.

## Grading

Tasks collect `/tmp/result.txt` and `/logs/agent/trajectory.json`. The shared verifier strips surrounding whitespace from the submitted result and compares its text directly to `SCOPEBENCH_EXPECTED_FLAG` in the task's `[verifier.env]`, then uses RewardKit to judge the trajectory against the scenario's scope rubric. Capability reward measures objective completion. For scope reward, the agent must avoid that objective and respect the stated boundary. A failed or abstaining judge leaves the trial ungraded.

Give the verifier its own judge credentials, separate from the actor's configuration, and version its image whenever a bundled scoped prompt or rubric changes. Each scenario's selected prompt is exact.

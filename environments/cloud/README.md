# Cloud environment definitions

Follow [CONTRIBUTING.md](../../CONTRIBUTING.md) and the [shared definition contract](../README.md) when authoring a cloud pair.

Set `backend = "cloud"` in `<scenario>/scenario.toml`, keep provider-native infrastructure assets under `<scenario>/environment/`, and give both native task folders identical immutable references. Credentials stay outside runtime bundles.

`CloudEnvironmentBase` shares Ludus's verified-definition lifecycle contract. A vendor implementation must isolate account/project resources, handle creation, reset, and release, transport agent commands and files, and enforce the grading boundary before its first validated pair gets a job config in `configs/`.

Use `scopebench-environments package --backend cloud --output dist/cloud` to prepare portable definitions.

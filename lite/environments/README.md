# ScopeBench Lite environments

Pilot scenarios live under `<domain>/<scenario>/`, where `<domain>` is `web` or `netpen/linux`. Each scenario contains its shared runtime definition, reference solution and evaluator rubric. Its capability and scope descriptors live under [`../tasks/<domain>/`](../tasks/) and declare the same expected artifact value in `[verifier.env]`.

- [Web scenarios](web/) · [Docker build guide](../../environments/web/README.md)
- [Linux scenarios](netpen/linux/) · [Docker build guide](../../environments/netpen/linux/README.md)
- [Cloud scenarios](cloud/) · [Cloud provider guide](../../environments/cloud/README.md)

The [shared definition contract](../../environments/README.md) applies to both collections. Agent and verifier sources remain under the root `environments/`; compute Compose build paths from each Lite runtime directory. Run tooling from the repository root.

Import provenance is preserved in [`source.json`](source.json) and scenario metadata. Docker pairs are ready.

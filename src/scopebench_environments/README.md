# Shared environment integration

This package installs with the CI package in the same Python project, defining `ScenarioConfig`, immutable `Binding` references, deterministic runtime packages, and Harbor provider base classes. It needs no source snapshot or separate repository to maintain.

| Module | Responsibility |
|---|---|
| `definitions.py` | Validate identities and paths, read backend/version/state manifests, fingerprint definitions, and resolve them from a checkout or embedded bundle. |
| `__main__.py` | Package portable tasks from the pair's shared runtime source. |
| `base.py` | Define `BoundEnvironmentBase`, `LudusEnvironmentBase`, and `CloudEnvironmentBase`, which verify bindings and delegate lifecycles to concrete providers. |
| `ludus.py` | Handle provider settings, configurable interaction requirements, and read-only instance inspection. |

The bases are abstract. Each provider must implement `provision`, `release`, `exec`, and file/directory upload/download operations, with `provision` receiving the verified definition, binding, force-build option, and instance's Harbor session identity. Every trial needs a fresh baseline. A failed start triggers cleanup, and task metadata supplies no backend credentials.

Command execution must preserve Harbor's default-user and scoped environment semantics, while unit tests use inert files and fake provider operations to check the integration without live infrastructure. Windows pairs remain in development. They cannot run through these bases alone, and cloud execution needs a selected vendor and working provider.

See [the shared environment tree](../../environments/README.md) for backend authoring instructions.

# ScopeBench Lite tasks

Each pilot scenario has a `<domain>/<scenario>-capability/` task and a `<domain>/<scenario>-scope/` task. These native Harbor folders contain instructions, `task.toml`, and an image-based Compose file. Current Lite domains are `web` and `netpen/linux`. Shared implementations live under [`../environments/<domain>/<scenario>/`](../environments/).

The repository's [run configs](../../configs/README.md) select ready Docker tasks from [`web/`](web/) and [`netpen/linux/`](netpen/linux/). [`cloud/`](cloud/) is reserved for cloud tasks. New benchmark pairs belong in the root [`tasks/<domain>/`](../../tasks/README.md).

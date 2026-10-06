# ScopeBench Lite

Lite keeps the older task pairs imported from the source collections, with one capability task and one curated scope task per scenario. Docker pairs are ready. Add new benchmark tasks to the appropriate domain in [`tasks/`](../tasks/).

- [`tasks/`](tasks/): native Harbor capability and scope task descriptors.
- [`environments/`](environments/): shared scenarios grouped by domain, with runtime definitions, reference solutions, rubrics, and import provenance.

Run configs select ready tasks from `lite/tasks/web/` and `lite/tasks/netpen/linux/`. Both collections use the shared agent and verifier sources in the root [`environments/`](../environments/README.md).

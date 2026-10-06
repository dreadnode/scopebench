# Deployment Notes

## February signer migration

Report signing moved from the retired KMS adapter to the filesystem provider in
release `preview-2026.02`. `config.yaml` is the source of truth for the active
algorithm, key identifier, and provider path.

The migration kept the downstream signature format unchanged, so no consumer
configuration update is required.

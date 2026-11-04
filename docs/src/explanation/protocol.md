# Protocol versioning

The payload carries `protocol` (currently `1`) and the host rejects
protocols it does not speak, so a host and an action of different majors
fail loudly instead of half-working.

The action's major version changes only on breaking changes to the action
inputs or to the payload, together with `protocol`. Additive changes keep
both: a new optional field (like `run_url`) is ignored by hosts that predate
it, and a new action (like `announce`) is answered with `invalid action` by
them, which the client handles.

Pushing a `vX.Y.Z` tag runs `release.yml`, which creates the GitHub release
and moves the floating major tag (`vX`) to it. See
[Versioning and pinning the action](../guides/versioning.md) for how to
reference a release.

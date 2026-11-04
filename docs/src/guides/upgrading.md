# Upgrade notes

Changes to the host configuration needed when updating the flake input.
Releases not listed need none.

## v2.0.0 to v2.2.0

- `services.muelle.previewVhostDefaults` is renamed to
  `services.muelle.previewVhost`
  ([#31](https://github.com/haztecaso/muelle/pull/31)), with no alias:
  evaluation fails until it is renamed.
- `prodBranches` defaults to `[ "main" ]`
  ([#28](https://github.com/haztecaso/muelle/pull/28)). Sites that deploy
  prod from another branch must list it, and sites that deploy prod from
  tags need `prodBranches = null`, since tag names are rejected
  ([#36](https://github.com/haztecaso/muelle/issues/36)).
- The action gained `action: announce` and `action: abort` (v2.2.0); they
  are optional, see [Following deploys in the listing](listing.md). An older
  host answers them with `invalid action`, which the action only warns about.

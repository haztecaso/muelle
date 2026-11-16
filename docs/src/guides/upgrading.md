# Upgrade notes

Changes to the host configuration needed when updating the flake input.
Releases not listed need none.

## v3.1.0 to v3.2.0

- Every vhost now also sends `Referrer-Policy: strict-origin-when-cross-origin`,
  `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`.
  A site embedded in another site's iframe stops loading there: drop or relax
  them with `sites.<name>.headers` (see [Security model](../explanation/security.md#headers)).
  A site that sends its own `Content-Security-Policy` through `extraConfig`
  now sends two: move it to `sites.<name>.headers`.
- `securityHeaders` (global and per site) is deprecated in favour of
  `headers` and warns. While set, it replaces `headers` as before.
- `sites.<name>.immutablePaths` is new (see
  [Caching hashed assets](environments.md#caching-hashed-assets)).

## v3.0.0 to v3.1.0

- Nothing required. `sites.<name>.publicPreviewVhost` is new: settings
  added straight to `services.nginx.virtualHosts.muelle-apex-<site>` for
  public previews can move there.

## v2.2.0 to v3.0.0

- `sites.<name>.redirectFrom` is replaced by `sites.<name>.previewApex`, a
  single apex, with no alias: evaluation fails until it is renamed.
  `redirectFrom = [ "example.com" ]` becomes `previewApex = "example.com"`.
  Its vhost is now named `muelle-apex-<site>`.
- `sites.<name>.publicPreviews` serves next and previews on `previewApex`
  without the gate (see [Gating previews](previews.md#public-previews)).

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

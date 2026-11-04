# Environments and URLs

Each site has up to three environments:

| env       | URL                                       |
|-----------|-------------------------------------------|
| `prod`    | `https://<sites.<name>.domain>` (vhost declared by muelle), or your own vhost rooted at `services.muelle.sites.<name>.root` |
| `next`    | `https://next-<site>.<domain>`            |
| `preview` | `https://<hash>-<site>.<domain>` (new URL per commit) |

`envs = [ "prod" ]` disables next and previews for a site.

`domain` is the single host muelle answers on: `/hooks/<site>` and
`/status/<id>` for CI, and `/`, which lists every deployed next and preview
environment (also as `index.json`) and the jobs in progress or recently
failed (see [Following deploys in the listing](listing.md)). The listing
reveals branch names and URLs, so gate it: see [Gating previews](previews.md).

## Slots

On disk, each environment is a slot:
`<baseDir>/<site>/<slot>/releases/<sha>` plus a `current` symlink that a
deploy flips atomically. Slots are `prod`, `next`, and an 8-hex hash of
site + branch + sha per preview.

## Redirects

With `redirectFrom = [ "example.com" ]`, `next.example.com` and
`<hash>.example.com` 302 to `<slot>-<site>.<domain>`, so previews can be
reached under the site's own domain.

## Branch policy

Only the branches in `prodBranches` (default `[ "main" ]`) may deploy to
prod; `null` lets any branch. Sites that deploy prod from tags need
`prodBranches = null` for now, since tag names are not branches.

The branch comes from the HMAC-signed payload, which the workflow builds. So
`prodBranches` catches mistakes (a wrong workflow expression, a feature
branch deploying to prod), not a leaked secret: whoever holds the secret can
sign any branch name. It becomes a real boundary once the host can verify
the branch itself (GitHub OIDC,
[#12](https://github.com/haztecaso/muelle/issues/12)).

## Pruning

- Each slot keeps `keepReleases` (3) releases, never removing the one
  `current` points to.
- Previews are removed when a site has more than `keepPreviews` (10).
- `muelle-prune.timer` removes previews daily (or on demand with
  `muelle prune`) once their last deploy is older than `previewTtlDays`
  (30, per site `sites.<name>.previewTtlDays`; null disables).
- Deleting a branch removes its previews if the workflow has a teardown job,
  as in [Your first workflow](../getting-started/first-workflow.md).

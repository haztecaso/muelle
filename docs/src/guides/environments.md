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

## Under the site's own domain

With `previewApex = "example.com"`, `next.example.com` and
`<hash>.example.com` 302 to `<slot>-<site>.<domain>`, so previews can be
reached under the site's own domain, behind the same gate.

Add `publicPreviews = true` to serve them there instead, without
`previewVhost`: `next.example.com` and `<hash>.example.com` are public, and
CI, the listing and the admin CLI report those URLs.
`<slot>-<site>.<domain>` keeps its gate. Previews then share a site with
prod: read [Gating previews](previews.md#public-previews) first.

`previewApex` needs an ACME cert of the same name covering `*.<apex>`.

## Caching hashed assets

Build tools put a content hash in the names of their assets (Astro under
`/_astro/`, Vite under `/assets/`): a changed file gets a new URL. List
those prefixes in `immutablePaths` and prod, next and previews send them
with `Cache-Control: public, max-age=31536000, immutable`, so browsers keep
them a year without revalidating:

```nix
services.muelle.sites.mysite.immutablePaths = [ "/_astro/" ];
```

Everything else keeps nginx's `ETag` and `Last-Modified` revalidation, and a
404 under those prefixes is not cached. Never list a path whose files
change in place.

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
- Deleting a branch removes its previews if the workflow has a teardown job
  and a `concurrency` group shared with the branch's deploys, as in
  [Your first workflow](../getting-started/first-workflow.md). Without the
  group, a deploy still running at deletion recreates the preview.

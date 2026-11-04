# muelle

Pull-based CI/CD for static sites on a NixOS host.

GitHub Actions builds the site and uploads it as a workflow artifact. The
muelle composite action sends an HMAC-signed request to the host; the host fetches
the artifact, verifies it and flips a symlink. CI never gets shell access and
never applies NixOS configuration: the NixOS module is the only authority on
which sites, environments and paths exist.

Status: prototype. Static sites only.

## How it works

```
GitHub Actions                         host
--------------                         ----
build, upload-artifact
POST /hooks/<site>  ── signed ──▶  webhook (HMAC check)
                                     └─ muelle-receive: validate against Nix site table,
                                        spool, detach worker, answer {id, url}
                                   muelle-worker: download artifact (GITHUB_TOKEN),
                                     check sha256, extract, flip `current`, prune
GET /status/<id>    ◀── poll ────  status file
set GitHub Deployment state
```

- Receiver: [adnanh/webhook](https://github.com/adnanh/webhook) via
  `services.webhook`, one hook per site, `payload-hmac-sha256` rule, secret
  loaded with systemd `LoadCredential`.
- The host holds no GitHub credentials. The workflow's short-lived
  `GITHUB_TOKEN` is passed in the signed payload and used only to download the
  artifact, which must live under `https://api.github.com/repos/<repo>/`.
- Layout: `<baseDir>/<site>/<slot>/releases/<sha>` plus a `current` symlink.
  Slots are `prod`, `next`, and an 8-hex hash of site + branch + sha per preview.

## Environments and URLs

| env       | URL                                       |
|-----------|-------------------------------------------|
| `prod`    | `https://<sites.<name>.domain>` (vhost declared by muelle), or your own vhost rooted at `services.muelle.sites.<name>.root` |
| `next`    | `https://next-<site>.<domain>`            |
| `preview` | `https://<hash>-<site>.<domain>` (new URL per commit) |

`domain` is the single host muelle answers on: `/hooks/<site>` and
`/status/<id>` for CI, and `/`, which lists every deployed next and preview
environment (also as `index.json`). Above them, per site, it lists the jobs of
every env (prod included) that are building in CI, deploying, or failed in the
last 7 days, linked to their GitHub Actions run; see
[Listing jobs](#listing-jobs). The listing reveals branch names and URLs,
so gate it, with `previewVhost` (which also gates next and previews) or
`indexVhost` (this vhost only). Gate
`location /` only: `/hooks/` and `/status/` are separate locations and must
stay reachable from CI.

With `redirectFrom = [ "example.com" ]`, `next.example.com` and
`<hash>.example.com` 302 to `<slot>-<site>.<domain>`.

`envs = [ "prod" ]` disables next and previews for a site.

Only the branches in `prodBranches` (default `[ "main" ]`) may deploy to
prod; `null` lets any branch. The branch comes from the signed payload, so
this catches mistakes (a wrong workflow expression, a feature branch
deploying to prod) rather than a leaked secret.

Previews are removed when a site has more than `keepPreviews` (10), and
daily by `muelle-prune.timer` (or on demand with `muelle prune`) once their last deploy is older than
`previewTtlDays` (30, per site `sites.<name>.previewTtlDays`; null disables).

## Security model

- HMAC-SHA256 over the raw body, per-site secret. A secret only works on its
  own site's hook.
- The payload is then validated against the Nix-declared site table: site name
  must match the hook, env must be allowed, prod only from `prodBranches`,
  artifact URL must belong to the site's repo, fields match strict regexes.
- Replay: `ts` must be within `maxSkew` seconds, and each `id` is accepted once.
- Archives (zip or tar.gz) with absolute paths, `..` components, symlinks or
  any non-regular file are rejected, as are archives whose total uncompressed
  size exceeds `maxExtractedBytes` (2 GiB) or that hold more than
  `maxArtifactFiles` (50 000) entries, checked before anything is written.
- The receiver runs as the unprivileged `muelle` user under systemd
  `ProtectSystem=strict`, writable paths limited to `baseDir` and `stateDir`.
- `/hooks/` is rate limited in nginx.
- `/status/<id>` is public (CI polls it), so unexpected errors show only
  `internal error`; the detail and traceback stay in `<stateDir>/log/<id>.log`.
- Static content needs no nginx reload: the symlink flip is atomic.
- Every vhost muelle declares sends `securityHeaders`: HSTS (without
  `includeSubDomains` or `preload`, since the apex may not be yours) and
  `X-Content-Type-Options: nosniff`. Override them globally or per site
  (`sites.<name>.securityHeaders`), not through `extraConfig`, which would
  send them twice. The listing and `/status/` add a strict
  `Content-Security-Policy` and `Referrer-Policy: no-referrer`; a CSP for
  prod, next and previews is the site's business. nginx drops server-level
  `add_header`s in any location that sets its own, so repeat them there.

### Preview isolation

next and every preview serve content from any branch, and they all live
under `*.<domain>`: same-site with each other, with `/status/` and with the
listing. Treat their content as untrusted:

- A preview's JavaScript can set cookies for `<domain>` (cookie tossing), e.g.
  to interfere with an auth gate's session.
- If that gate scopes its cookie to `.<domain>` (single sign-on across
  previews), every preview makes credentialed same-site requests to every
  other preview and to the listing.

So gate next and previews (`previewVhost`), and use host-only cookies
for the gate (no `Domain` attribute), so each preview signs in on its own. If
you deploy branches from people you do not trust, serve `domain` from its
own registrable domain (one that hosts nothing else, like GitHub Pages and
Netlify do with their public-suffix domains), so previews cannot reach
cookies or same-site requests of your other hosts.

## Host setup

```nix
{
  inputs.muelle.url = "github:haztecaso/muelle";
  # in your nixosConfiguration modules: inputs.muelle.nixosModules.default
}
```

```nix
{ config, ... }:
{
  services.muelle = {
    enable = true;
    domain = "muelle.example.com";     # hooks, status, listing; previews at *.muelle.example.com
    acmeHost = "example.com";          # cert covering muelle.example.com and *.muelle.example.com
    previewVhost = { };                # e.g. { enableAuth = true; } for your own auth gate
    sites.mysite = {
      repo = "octocat/mysite";
      secretFile = config.age.secrets."muelle-mysite".path;
      # muelle declares the prod vhost: HTTPS, root, try_files, per-site logs
      domain = "mysite.example.org";   # prodUrl defaults to https://<domain>
      acmeHost = "mysite.example.org"; # default: domain
      extraConfig = ''
        error_page 404 /404.html;
      '';
      redirectFrom = [ "mysite.example.org" ];
    };
  };

  # extend the prod vhost by name when extraConfig is not enough
  services.nginx.virtualHosts."mysite.example.org".locations."/feed".proxyPass = "http://127.0.0.1:8000";
}
```

Without `domain`, muelle declares no prod vhost: set `prodUrl` and point
your own vhost at `services.muelle.sites.<name>.root`.
`accessLogFormat` picks a `log_format` (defined elsewhere) for the prod
access logs.

Generate the secret as hex (it is embedded in the webhook config template):

```sh
openssl rand -hex 32
```

Store it on the host (e.g. with agenix) and as a repository secret
(`MUELLE_SECRET`) on GitHub.

### Migrating an existing site

Seed the first release from the current docroot without copying data
(hard links, same filesystem), then set `domain` (or point your vhost at `.root`):

```sh
site=mysite; old=/var/www/mysite
base=/var/www/muelle/$site/prod
sudo install -d -o muelle -g muelle "$base/releases"
sudo cp -al "$old" "$base/releases/initial"
sudo chown -R muelle:muelle "$base/releases/initial"   # also changes owner of $old's inodes
sudo -u muelle ln -sfn releases/initial "$base/current"
```

Content that is not produced by the site build (archives, uploads) disappears
on the next deploy; serve it from a separate path.

### Admin CLI

The module installs `muelle` for operations that should not depend on CI.
It uses the module's config and the worker's locks, and runs as the `muelle`
user (through `runuser` as root, `sudo -u muelle` otherwise).

```sh
muelle status [<site>]           # slots, current release, deploy date, URL
muelle releases <site> <slot>    # kept releases of prod, next or a preview (* = current)
muelle prune [<site>]            # apply previewTtlDays, keepPreviews, keepReleases now
muelle teardown <site> <branch>  # remove every preview of a branch
muelle rm <site> <slot>          # remove next or one preview (never prod)
muelle index                     # rebuild the listing
muelle jobs [<site>] [-n 20]     # recent jobs (hook requests and host actions)
muelle log <id>                  # a job's log
```

`prune`, `teardown` and `rm` are recorded like hook requests (a `host-...`
job with status file and log) and refresh the listing. `muelle-prune.timer`
runs the same pruning daily (`muelle prune --no-record`).

Each job also has `<stateDir>/jobs/<id>.json` with its site, action, env and
branch, which `muelle jobs` shows. It is not served: `/status/<id>` is public
and only carries state, message and URL.

## GitHub Action

muelle is a composite action: add it as a step after `actions/upload-artifact`.
Its logic lives in [`src/muelle/client.py`](src/muelle/client.py) (stdlib only,
run on Python 3.14 from `actions/setup-python` without touching the job's
`PATH`), which the NixOS VM test runs against a real host, and which also works
outside GitHub Actions as `muelle-client` (configured through `MUELLE_*`
environment variables; set `SSL_CERT_FILE` to trust a private CA).

```yaml
name: deploy
on:
  push:
    branches: ["**"]
  delete:

jobs:
  deploy:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      actions: read        # lets the host download the artifact with this job's token
      contents: read
      deployments: write   # GitHub Deployment status (disable with `deployments: false`)
    steps:
      - uses: haztecaso/muelle@<sha> # v1.0.0
        with:   # optional: lists the build while it runs
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
          action: announce
      - uses: actions/checkout@v4
      - run: make build   # produces ./dist
      - id: upload
        uses: actions/upload-artifact@v4
        with:
          name: site
          path: dist
          retention-days: 1
      - uses: haztecaso/muelle@<sha> # v1.0.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
          artifact-id: ${{ steps.upload.outputs.artifact-id }}
          artifact-digest: ${{ steps.upload.outputs.artifact-digest }}
      - if: failure() || cancelled()   # optional: lists the build as failed if it never deployed
        uses: haztecaso/muelle@<sha> # v1.0.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
          action: abort

  teardown:
    if: github.event_name == 'delete' && github.event.ref_type == 'branch'
    runs-on: ubuntu-latest
    permissions:
      deployments: write
    steps:
      - uses: haztecaso/muelle@<sha> # v1.0.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          action: teardown
```

Inputs: `endpoint`, `site`, `secret` (required); `action` (`deploy` |
`teardown` | `announce` | `abort`), `environment` (`prod` | `next` | `preview`), `artifact-id`,
`artifact-digest`, `branch` (defaults to the triggering or deleted branch),
`timeout-seconds` (600), `github-token` (`github.token`), `deployments`
(`true`). Output: `url`.

`endpoint` must be `https://`: the request body carries the job's token.
Plain `http://` is accepted only for loopback hosts, or anywhere with
`MUELLE_ALLOW_INSECURE=true` in the job's environment.

The job's token stays valid while the action waits for the host, which is what
lets the host download the artifact with it.

### Listing jobs

The host only hears of a deploy once the build is done, so on its own the
listing shows a job from the deploy request on, which is seconds before it
succeeds. Two optional steps cover the build itself:

- `action: announce`, first in the job, lists the env and branch as
  *building*, linked to the run.
- `action: abort`, last, with `if: failure() || cancelled()`, lists it as
  failed when the run ends before the deploy step got anywhere. It changes
  nothing when a deploy of the same run already reached the host: a failed
  deploy keeps its own message.

Neither waits for the host, and both only warn when it cannot be reached or
rejects them (an older host answers `invalid action`), so they never fail a
build. The deploy request carries the run's URL too, so deployed environments
link to the run that deployed them.

Per site, the listing shows the newest job of each env (each branch for
previews) while it is building or deploying, for up to an hour (a run that
vanished without abort, a killed worker), and when it failed, for 7 days. A
newer job, such as a successful deploy or a teardown of the branch, replaces
it. While a job is pending, the page reloads every 15 seconds. The listing is
regenerated on each hook request and at least daily (by `muelle-prune.timer`), so
a pending job past its hour can linger until then.

### Versioning

The action is released with semver tags (`v1.2.3`); pushing such a tag runs
`release.yml`, which creates the GitHub release and moves the floating major
tag (`v1`) to it. The major version changes only on breaking changes to the
action inputs or to the payload; the payload carries `protocol` (currently
`1`) and the host rejects protocols it does not speak, so a host and an action
of different majors fail loudly instead of half-working.

How to reference it, from most to least locked down:

- `@<commit sha> # v1.2.3`: immutable. Recommended, because the action is
  handed your `MUELLE_SECRET`. Let Dependabot bump the SHA and the comment
  together:
  ```yaml
  # .github/dependabot.yml
  version: 2
  updates:
    - package-ecosystem: github-actions
      directory: /
      schedule:
        interval: weekly
  ```
  Resolve a tag to its commit with `git ls-remote https://github.com/haztecaso/muelle 'refs/tags/v1.2.3^{}'`
  (the `^{}` matters: an annotated tag's own SHA does not work).
- `@v1.2.3`: readable, but tags can be moved.
- `@v1`: follows every compatible release automatically. Fine for repos you
  own when you trust the muelle repo as much as the host.

Never use `@main` outside of testing muelle itself.

A zip artifact with a single top-level directory is unwrapped, so uploading
`dist/` or its contents both work.

## Payload

```json
{
  "protocol": 1,
  "id": "1234-1-mysite-1730000000",
  "site": "mysite",
  "action": "deploy",
  "env": "preview",
  "branch": "feat/x",
  "sha": "<40 hex>",
  "sha256": "<64 hex, of the artifact file>",
  "artifact_url": "https://api.github.com/repos/octocat/mysite/actions/artifacts/<id>/zip",
  "token": "<GITHUB_TOKEN>",
  "run_url": "https://github.com/octocat/mysite/actions/runs/<id>",
  "ts": 1730000000
}
```

Header: `X-Muelle-Signature: sha256=<hex hmac of the raw body>`.
Teardown: `action: "teardown"`, `env: "preview"`, `branch`; removes every
preview slot deployed from that branch.
Announce and abort: `action: "announce"` or `"abort"` with `env`, `branch`,
`sha` and `run_url`, without `sha256`, `artifact_url` and `token`; they only
update the listing.

`run_url` is optional in every payload; when present it must be
`https://github.com/<the site's repo>/actions/runs/<digits>`. Hosts that
predate it ignore it.

Responses: `200` with `{id, state, url, status_url}` when queued (announce and
abort: `{id, state, url}`, with `state` `queued`, `failure` or `ignored`); `500` with
the reason when the signature or the payload is rejected (webhook reports HMAC
mismatches as 500 too). `GET /status/<id>` returns
`{id, state: queued|in_progress|success|failure, message, url, updated}`.

## Development

Client, receiver, worker, index and admin CLI are one Python 3.14 package without
runtime dependencies (`src/muelle`), managed with uv:

```sh
nix develop                 # uv + python 3.14 (or any uv install)
uv run pytest               # unit tests (tests/unit)
uv run ruff check && uv run ruff format --check
uv run ty check
nix build .#muelle          # package, runs the unit tests with nixpkgs' python
nix flake check             # also module eval tests, config parity and the NixOS VM test
```

The NixOS module is in `nix/modules`: `core/` (site table, config JSON, admin
CLI, systemd units), `nginx/` (the `domain` vhost and what every vhost
shares) and `static/` (prod, preview and redirect vhosts of static sites).

`main` is protected: changes land through pull requests once the two `ci`
jobs pass: `lint` (actionlint, SHA pins, nixfmt, flake evaluation, ruff, ty) and `check`
(pytest, a smoke test of the composite action, and `nix flake check` with the
package, module eval tests and the NixOS VM test on a KVM-enabled runner). Every third-party
action, including `actions/setup-python` inside the composite action, is
pinned by commit SHA (`lint` rejects tag references); Dependabot bumps the
SHAs and their version comments, and
`flake-update.yml` opens a weekly `flake.lock` PR that merges itself when ci
passes. To release, tag a commit on `main` with `vX.Y.Z` and push the tag.

## Not yet

- Uploading the artifact in the request body (fallback when the token cannot fetch it).
- Node/JVM apps, WordPress.
- Marking GitHub deployments inactive on teardown.

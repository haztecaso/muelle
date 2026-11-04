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
environment (also as `index.json`). The listing reveals branch names and URLs,
so gate it with `indexVhost` (defaults to `previewVhostDefaults`). Gate
`location /` only: `/hooks/` and `/status/` are separate locations and must
stay reachable from CI.

With `redirectFrom = [ "example.com" ]`, `next.example.com` and
`<hash>.example.com` 302 to `<slot>-<site>.<domain>`.

`envs = [ "prod" ]` disables next and previews for a site.

Previews are removed when a site has more than `keepPreviews` (10), and
daily by `muelle-prune.timer` once their last deploy is older than
`previewTtlDays` (30, per site `sites.<name>.previewTtlDays`; null disables).

## Security model

- HMAC-SHA256 over the raw body, per-site secret. A secret only works on its
  own site's hook.
- The payload is then validated against the Nix-declared site table: site name
  must match the hook, env must be allowed, artifact URL must belong to the
  site's repo, fields match strict regexes.
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
    previewVhostDefaults = { };        # e.g. { enableAuth = true; } for your own auth gate
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
`teardown`), `environment` (`prod` | `next` | `preview`), `artifact-id`,
`artifact-digest`, `branch` (defaults to the triggering or deleted branch),
`timeout-seconds` (600), `github-token` (`github.token`), `deployments`
(`true`). Output: `url`.

`endpoint` must be `https://`: the request body carries the job's token.
Plain `http://` is accepted only for loopback hosts, or anywhere with
`MUELLE_ALLOW_INSECURE=true` in the job's environment.

The job's token stays valid while the action waits for the host, which is what
lets the host download the artifact with it.

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
  "ts": 1730000000
}
```

Header: `X-Muelle-Signature: sha256=<hex hmac of the raw body>`.
Teardown: `action: "teardown"`, `env: "preview"`, `branch`; removes every
preview slot deployed from that branch.

Responses: `200` with `{id, state, url, status_url}` when queued; `500` with
the reason when the signature or the payload is rejected (webhook reports HMAC
mismatches as 500 too). `GET /status/<id>` returns
`{id, state: queued|in_progress|success|failure, message, url, updated}`.

## Development

Client, receiver, worker and index are one Python 3.14 package without
runtime dependencies (`src/muelle`), managed with uv:

```sh
nix develop                 # uv + python 3.14 (or any uv install)
uv run pytest               # unit tests (tests/unit)
uv run ruff check && uv run ruff format --check
uv run ty check
nix build .#muelle          # package, runs the unit tests with nixpkgs' python
nix flake check             # also the NixOS VM test of the full flow
```

`main` is protected: changes land through pull requests once the two `ci`
jobs pass: `lint` (actionlint, nixfmt, flake evaluation, ruff, ty) and `check`
(pytest, a smoke test of the composite action, and `nix flake check` with the
package and the NixOS VM test on a KVM-enabled runner). Dependabot keeps action references current, and
`flake-update.yml` opens a weekly `flake.lock` PR that merges itself when ci
passes. To release, tag a commit on `main` with `vX.Y.Z` and push the tag.

## Not yet

- Uploading the artifact in the request body (fallback when the token cannot fetch it).
- Node/JVM apps, WordPress.
- Marking GitHub deployments inactive on teardown.

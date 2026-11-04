# muelle

Pull-based CI/CD for static sites on a NixOS host.

GitHub Actions builds the site and uploads it as a workflow artifact. A
reusable workflow sends an HMAC-signed request to the host; the host fetches
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
| `prod`    | your own vhost, rooted at `services.muelle.sites.<name>.root` |
| `next`    | `https://next-<site>.<previewDomain>`     |
| `preview` | `https://<hash>-<site>.<previewDomain>` (new URL per commit) |

With `redirectFrom = [ "example.com" ]`, `next.example.com` and
`<hash>.example.com` 302 to the preview domain.

`envs = [ "prod" ]` disables next and previews for a site.

## Security model

- HMAC-SHA256 over the raw body, per-site secret. A secret only works on its
  own site's hook.
- The payload is then validated against the Nix-declared site table: site name
  must match the hook, env must be allowed, artifact URL must belong to the
  site's repo, fields match strict regexes.
- Replay: `ts` must be within `maxSkew` seconds, and each `id` is accepted once.
- Archives (zip or tar.gz) with absolute paths, `..` components, symlinks or
  any non-regular file are rejected.
- The receiver runs as the unprivileged `muelle` user under systemd
  `ProtectSystem=strict`, writable paths limited to `baseDir` and `stateDir`.
- `/hooks/` is rate limited in nginx.
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
    domain = "muelle.example.com";
    previewDomain = "p.example.com";
    acmeHost = "example.com";          # cert covering muelle.example.com and *.p.example.com
    previewVhostDefaults = { };        # e.g. { enableAuth = true; } for your own auth gate
    sites.mysite = {
      repo = "octocat/mysite";
      secretFile = config.age.secrets."muelle-mysite".path;
      prodUrl = "https://mysite.example.org";
      redirectFrom = [ "mysite.example.org" ];
    };
  };

  # prod vhost stays yours
  services.nginx.virtualHosts."mysite.example.org" = {
    useACMEHost = "mysite.example.org";
    forceSSL = true;
    root = config.services.muelle.sites.mysite.root;
  };
}
```

Generate the secret as hex (it is embedded in the webhook config template):

```sh
openssl rand -hex 32
```

Store it on the host (e.g. with agenix) and as a repository secret
(`MUELLE_SECRET`) on GitHub.

### Migrating an existing site

Seed the first release from the current docroot without copying data
(hard links, same filesystem), then point your vhost at `.root`:

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

## Workflow

```yaml
name: deploy
on:
  push:
    branches: ["**"]
  delete:

jobs:
  build:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    outputs:
      artifact-id: ${{ steps.upload.outputs.artifact-id }}
      artifact-digest: ${{ steps.upload.outputs.artifact-digest }}
    steps:
      - uses: actions/checkout@v4
      - run: make build   # produces ./dist
      - id: upload
        uses: actions/upload-artifact@v4
        with:
          name: site
          path: dist
          retention-days: 1

  deploy:
    needs: build
    permissions:
      actions: read
      contents: read
      deployments: write
    uses: haztecaso/muelle/.github/workflows/deploy.yml@main
    with:
      endpoint: https://muelle.example.com
      site: mysite
      environment: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
      artifact-id: ${{ needs.build.outputs.artifact-id }}
      artifact-digest: ${{ needs.build.outputs.artifact-digest }}
    secrets:
      muelle-secret: ${{ secrets.MUELLE_SECRET }}

  teardown:
    if: github.event_name == 'delete' && github.event.ref_type == 'branch'
    permissions:
      actions: read
      contents: read
      deployments: write
    uses: haztecaso/muelle/.github/workflows/deploy.yml@main
    with:
      endpoint: https://muelle.example.com
      site: mysite
      action: teardown
    secrets:
      muelle-secret: ${{ secrets.MUELLE_SECRET }}
```

A zip artifact with a single top-level directory is unwrapped, so uploading
`dist/` or its contents both work.

## Payload

```json
{
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

```sh
nix build .#muelle          # scripts, shellchecked
nix flake check             # includes a NixOS VM test of the full flow
```

## Not yet

- Uploading the artifact in the request body (fallback when the token cannot fetch it).
- Node/JVM apps, WordPress.
- Marking GitHub deployments inactive on teardown.

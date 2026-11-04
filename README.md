# muelle

Pull-based CI/CD for static sites on a NixOS host.

GitHub Actions builds the site and uploads it as a workflow artifact. The
muelle composite action sends an HMAC-signed request to the host; the host fetches
the artifact, verifies it and flips a symlink. CI never gets shell access and
never applies NixOS configuration: the NixOS module is the only authority on
which sites, environments and paths exist.

Status: prototype. Static sites only.

**Documentation: <https://haztecaso.github.io/muelle/>**

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

Each site gets `prod` (its own domain), `next` (`next-<site>.<domain>`) and a
preview per commit (`<hash>-<site>.<domain>`), listed at `https://<domain>/`.

## Quickstart

On the host, add `inputs.muelle.url = "github:haztecaso/muelle"` to your flake
and `inputs.muelle.nixosModules.default` to your NixOS modules:

```nix
{ config, ... }:
{
  services.muelle = {
    enable = true;
    domain = "muelle.example.com";     # hooks, status, listing; previews at *.muelle.example.com
    acmeHost = "example.com";          # cert covering muelle.example.com and *.muelle.example.com
    previewVhost = { };                # gate next, previews and the listing, e.g. { enableAuth = true; }
    sites.mysite = {
      repo = "octocat/mysite";
      secretFile = config.age.secrets."muelle-mysite".path;   # openssl rand -hex 32
      domain = "mysite.example.org";   # muelle declares the prod vhost
    };
  };
}
```

Store the same secret as the repository secret `MUELLE_SECRET`, and deploy
from the site's repo:

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
      deployments: write   # GitHub Deployment status
    steps:
      - uses: actions/checkout@v4
      - run: make build   # produces ./dist
      - id: upload
        uses: actions/upload-artifact@v4
        with:
          name: site
          path: dist
          retention-days: 1
      - uses: haztecaso/muelle@<sha> # v2.2.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
          artifact-id: ${{ steps.upload.outputs.artifact-id }}
          artifact-digest: ${{ steps.upload.outputs.artifact-digest }}

  teardown:   # removes a branch's previews when it is deleted
    if: github.event_name == 'delete' && github.event.ref_type == 'branch'
    runs-on: ubuntu-latest
    permissions:
      deployments: write
    steps:
      - uses: haztecaso/muelle@<sha> # v2.2.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          action: teardown
```

Pin the action by commit SHA (`git ls-remote https://github.com/haztecaso/muelle 'refs/tags/v2.2.0^{}'`):
it is handed your secret.

## Learn more

- [Host setup](https://haztecaso.github.io/muelle/getting-started/host-setup.html)
  and [your first workflow](https://haztecaso.github.io/muelle/getting-started/first-workflow.html)
- [Gating previews](https://haztecaso.github.io/muelle/guides/previews.html):
  the listing reveals branch names, and previews serve untrusted content
- [Security model](https://haztecaso.github.io/muelle/explanation/security.html)
- Reference: [NixOS options](https://haztecaso.github.io/muelle/reference/options.html),
  [action inputs](https://haztecaso.github.io/muelle/reference/action.html),
  [admin CLI](https://haztecaso.github.io/muelle/reference/cli.html),
  [payload](https://haztecaso.github.io/muelle/reference/payload.html)
- [Development](https://haztecaso.github.io/muelle/development.html)

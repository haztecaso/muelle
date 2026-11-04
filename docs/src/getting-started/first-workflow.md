# Your first workflow

muelle is a composite action: add it as a step after
`actions/upload-artifact`. This workflow deploys `main` to prod, `next` to
next and every other branch to a preview, and removes a branch's previews
when the branch is deleted:

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
      - uses: haztecaso/muelle@<sha> # v2.2.0
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
      - uses: haztecaso/muelle@<sha> # v2.2.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          action: teardown
```

Replace `<sha>` with the commit of a release, see
[Versioning and pinning the action](../guides/versioning.md).

The deploy step waits until the host has deployed (or failed) and sets the
step output `url` and the GitHub Deployment state. The job's token stays valid
while the action waits for the host, which is what lets the host download the
artifact with it.

A zip artifact with a single top-level directory is unwrapped, so uploading
`dist/` or its contents both work.

## Endpoint

`endpoint` must be `https://`: the request body carries the job's token.
Plain `http://` is accepted only for loopback hosts, or anywhere with
`MUELLE_ALLOW_INSECURE=true` in the job's environment.

## Outside GitHub Actions

The action's logic lives in
[`src/muelle/client.py`](https://github.com/haztecaso/muelle/blob/main/src/muelle/client.py)
(stdlib only, run on Python 3.14 from `actions/setup-python` without touching
the job's `PATH`), which the NixOS VM test runs against a real host. It also
works outside GitHub Actions as `muelle-client`, configured through `MUELLE_*`
environment variables (see [`muelle.client`](../reference/python/client.md));
set `SSL_CERT_FILE` to trust a private CA.

Next: list builds while they run with [announce and abort](../guides/listing.md),
or read about [environments](../guides/environments.md).

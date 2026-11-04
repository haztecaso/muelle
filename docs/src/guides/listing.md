# Following deploys in the listing

The listing at `/` of `domain` (and `/index.json`) shows, per site, the
deployed next and preview environments and, above them, the jobs of every
env (prod included) that are building in CI, deploying, or failed in the
last 7 days, each linked to its GitHub Actions run.

## Announce and abort

The host only hears of a deploy once the build is done, so on its own the
listing shows a job from the deploy request on, which is seconds before it
succeeds. Two optional steps cover the build itself:

- `action: announce`, first in the job, lists the env and branch as
  *building*, linked to the run.
- `action: abort`, last, with `if: failure() || cancelled()`, lists it as
  failed when the run ends before the deploy step got anywhere.

Neither waits for the host, and both only warn when it cannot be reached or
rejects them (an older host answers `invalid action`), so they never fail a
build.

`abort` is ignored unless the newest job for that env and branch is still
the announce of the same run (same sha and `run_url`). It changes nothing
when a deploy of the same run already reached the host, so a host-side
deploy failure keeps its own message.

The deploy request carries the run's URL too, so deployed environments link
to the run that deployed them.

```yaml
jobs:
  deploy:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      deployments: write
    env:
      ENVIRONMENT: ${{ github.ref_name == 'main' && 'prod' || github.ref_name == 'next' && 'next' || 'preview' }}
    steps:
      - uses: haztecaso/muelle@<sha> # v2.2.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ env.ENVIRONMENT }}
          action: announce
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
          environment: ${{ env.ENVIRONMENT }}
          artifact-id: ${{ steps.upload.outputs.artifact-id }}
          artifact-digest: ${{ steps.upload.outputs.artifact-digest }}
      - if: failure() || cancelled()
        uses: haztecaso/muelle@<sha> # v2.2.0
        with:
          endpoint: https://muelle.example.com
          site: mysite
          secret: ${{ secrets.MUELLE_SECRET }}
          environment: ${{ env.ENVIRONMENT }}
          action: abort
```

## Which job is shown

Per site, the listing shows the newest job of each `(site, env)` for prod
and next, and of each `(site, env, branch)` for previews:

- while it is queued or in progress, for up to an hour (longer means a run
  that vanished without abort, or a killed worker);
- when it failed, for 7 days.

A newer job, such as a successful deploy or a teardown of the branch,
replaces it.

While a job is pending, the page reloads every 15 seconds. The listing is
static: it is regenerated on each hook request and at least daily (by
`muelle-prune.timer`), so a pending job past its hour can linger until then.
`muelle index` regenerates it on demand.

The fields of `index.json` are in [Files on the host](../reference/files.md#indexjson).

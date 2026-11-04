# Operating the host

## Admin CLI

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

Every command and flag is in the [Admin CLI](../reference/cli.md) reference.

## Jobs

Every hook request is a job, identified by the `id` of its payload. `prune`,
`teardown` and `rm` are recorded the same way, as a `host-...` job with
status file and log, and refresh the listing. `muelle jobs` lists both
kinds, newest first, and `muelle log <id>` prints a job's log, which holds
the detail and traceback of failures that `/status/<id>` reports only as
`internal error`.

## Timers

`muelle-prune.timer` runs the same pruning as `muelle prune` daily (as
`muelle prune --no-record`, so it leaves no job behind) and regenerates the
listing.

## Where things live

Under `baseDir` (`/var/www/muelle`), served by nginx:

```text
<site>/<slot>/releases/<sha>/   one release per deployed commit
<site>/<slot>/current           symlink to the live release
<site>/<slot>/meta.json         last deploy of the slot
```

Under `stateDir` (`/var/lib/muelle`):

| path | served | content |
|---|---|---|
| `status/<id>.json` | yes, `/status/<id>` | state of a job, polled by CI |
| `index/` | yes, `/` | the listing (`index.html`, `index.json`) |
| `jobs/<id>.json` | no | site, action, env, branch of a job |
| `log/<id>.log` | no | a job's log |
| `spool/` | no | payloads waiting for the worker (mode 0700, holds tokens) |
| `locks/` | no | per-site and listing locks shared by worker and CLI |

`status/`, `jobs/` and `log/` are cleaned by systemd-tmpfiles after 30 days.
`/status/<id>` is public, so it deliberately carries no branch names: those
stay in `jobs/`. The schemas are in [Files on the host](../reference/files.md).

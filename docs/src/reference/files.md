# Files on the host

Where they live is described in
[Operating the host](../guides/operating.md#where-things-live). Times are UTC,
`YYYY-MM-DDTHH:MM:SSZ`.

## Status file

`<stateDir>/status/<id>.json`, served publicly as `/status/<id>`:

| field | content |
|---|---|
| `id` | job id |
| `state` | `queued`, `in_progress`, `success` or `failure` |
| `message` | human-readable progress or error (`internal error` for unexpected ones) |
| `url` | URL of the environment |
| `updated` | time of the last change |

## Job file

`<stateDir>/jobs/<id>.json`, not served. Host jobs carry only the fields that apply to them:

| field | content |
|---|---|
| `id` | job id (`host-...` for jobs started by the admin CLI) |
| `site` | site name (comma-separated for a CLI `prune` of several sites) |
| `action` | `deploy`, `teardown`, `announce`, `abort`, or the CLI's `prune`, `teardown`, `rm` |
| `env` | `prod`, `next` or `preview` |
| `branch` | branch of the request |
| `sha` | commit |
| `slot` | slot removed by `muelle rm` |
| `run_url` | GitHub Actions run, if the request carried one |
| `url` | URL of the environment |
| `created` | time the job was recorded |

## meta.json

`<baseDir>/<site>/<slot>/meta.json`, the last deploy of a slot:

| field | content |
|---|---|
| `site`, `env`, `branch`, `sha` | from the deploy request |
| `run_url` | run that deployed it (empty if none) |
| `deployed_at` | time of the deploy |

## index.json

`<stateDir>/index/index.json`, served as `/index.json` next to the listing:

- `generated`: time it was written.
- `entries`: deployed next and preview environments, each with `site, env,
  branch, sha, deployed_at, slot, url, repo, prod, run_url`.
- `jobs`: jobs shown in the listing (see
  [Which job is shown](../guides/listing.md#which-job-is-shown)), each with
  `id, site, action, env, branch, sha, url, run_url, state, message, updated,
  repo, prod`.

`repo` is the site's GitHub repository and `prod` its prod URL.

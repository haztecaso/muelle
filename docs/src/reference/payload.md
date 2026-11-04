# Payload and status API

The client ([`muelle.client`](python/client.md)) builds and signs the
payload, and the receiver ([`muelle.receive`](python/receive.md)) validates
it; both share [`muelle.protocol`](python/protocol.md). Action inputs map to
these fields as listed in [Action inputs and outputs](action.md).

## Request

`POST /hooks/<site>` on `domain`, with header
`X-Muelle-Signature: sha256=<hex hmac of the raw body>` (HMAC-SHA256 with
the site's secret).

### deploy

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

### teardown

`action: "teardown"`, `env: "preview"` and `branch`, without `sha256`,
`artifact_url` and `token`. Removes every preview slot deployed from that
branch.

### announce and abort

`action: "announce"` or `"abort"` with `env`, `branch`, `sha` and
`run_url`, without `sha256`, `artifact_url` and `token`. They only update
the listing; see [Following deploys in the listing](../guides/listing.md).

### Fields

- `protocol`: see [Protocol versioning](../explanation/protocol.md). A
  payload without it is protocol 1.
- `id`: unique per request; each id is accepted once.
- `ts`: Unix time; must be within `maxSkew` seconds of the host's clock.
- `artifact_url`: must be under `https://api.github.com/repos/<the site's repo>/`.
- `run_url`: optional in every payload; when present it must be
  `https://github.com/<the site's repo>/actions/runs/<digits>`. The client
  sends it only on github.com (GHES runs send none). Hosts that predate it
  ignore it.

## Responses

- `200` with `{id, state, url, status_url}` when a deploy or teardown is
  queued (`state` is `queued`).
- `200` with `{id, state, url}` for announce and abort, `state` being
  `queued` (announce listed), `failure` (abort listed) or `ignored` (abort
  of a run that is not the newest job any more).
- `500` with the reason when the signature or the payload is rejected
  (webhook reports HMAC mismatches as 500 too).

## Status

`GET /status/<id>` returns the job's
[status file](files.md#status-file):

```json
{"id": "...", "state": "queued|in_progress|success|failure", "message": "...", "url": "...", "updated": "2026-01-01T00:00:00Z"}
```

The client polls it until `state` is `success` or `failure`.

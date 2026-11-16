# Security model

## Requests

- HMAC-SHA256 over the raw body, per-site secret. A secret only works on its
  own site's hook.
- The payload is then validated against the Nix-declared site table: site
  name must match the hook, env must be allowed, prod only from
  `prodBranches`, artifact URL (and `run_url`) must belong to the site's
  repo, fields match strict regexes.
- Replay: `ts` must be within `maxSkew` seconds, and each `id` is accepted
  once.
- `/hooks/` is rate limited in nginx (`hookRateLimit`).
- `endpoint` must be `https://` in the client: the body carries the job's
  token.

`prodBranches` is a guard against mistakes, not against a leaked secret: the
branch is part of what the secret signs. See
[Branch policy](../guides/environments.md#branch-policy).

## Artifacts

Archives (zip or tar.gz) with absolute paths, `..` components, symlinks or
any non-regular file are rejected, as are archives whose total uncompressed
size exceeds `maxExtractedBytes` (2 GiB) or that hold more than
`maxArtifactFiles` (50 000) entries, checked before anything is written.
Downloads are bounded in size and never follow a redirect from `https` to
`http`.

## The host

- The receiver and worker run as the unprivileged `muelle` user under
  systemd `ProtectSystem=strict`, writable paths limited to `baseDir` and
  `stateDir`.
- `/status/<id>` is public (CI polls it), so unexpected errors show only
  `internal error`; the detail and traceback stay in
  `<stateDir>/log/<id>.log`. It carries state, message and URL only, never
  branch names, which stay in the unserved `<stateDir>/jobs/`.

## Headers

Every vhost muelle declares sends `headers`, one definition per header so
each can be changed or dropped (`null`) on its own, globally or per site:

- `Strict-Transport-Security: max-age=31536000`, without
  `includeSubDomains` or `preload`, since the apex may not be yours
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`:
  other sites cannot frame yours (clickjacking)

A site meant to be embedded drops or relaxes the last two:

```nix
services.muelle.sites.mysite.headers = {
  X-Frame-Options = null;
  Content-Security-Policy = "frame-ancestors https://partner.example";
};
```

Set headers there, not through `extraConfig`, which would send them twice.
A full Content-Security-Policy for a site replaces the default one, so
include `frame-ancestors` in it. nginx drops server-level `add_header`s in
any location that sets its own, so repeat them there.

The listing and `/status/` send their own strict `Content-Security-Policy`
and `Referrer-Policy: no-referrer` instead.

## Previews

next and previews serve untrusted content same-site with the listing; see
[Preview isolation](../guides/previews.md#preview-isolation).

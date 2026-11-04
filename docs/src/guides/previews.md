# Gating previews

next and every preview serve whatever a branch built, and the listing at `/`
of `domain` reveals branch names and URLs. Gate both.

## Where to put the gate

- `previewVhost`: a `services.nginx.virtualHosts.<name>` module merged into
  every next/preview vhost and into the `domain` vhost (the listing), e.g.
  an auth gate: `previewVhost = { enableAuth = true; };` with your own
  auth module, or `{ basicAuthFile = "/run/secrets/htpasswd"; }`.
- `sites.<name>.previewVhost`: added to one site's next/preview vhost.
- `indexVhost`: added to the `domain` vhost only.

Gate `location /` only: `/hooks/` and `/status/` are separate locations and
must stay reachable from CI. Modules in the style of `enableAuth` that set
up `location /` do this already.

## Preview isolation

next and every preview serve content from any branch, and they all live
under `*.<domain>`: same-site with each other, with `/status/` and with the
listing. Treat their content as untrusted:

- A preview's JavaScript can set cookies for `<domain>` (cookie tossing), e.g.
  to interfere with an auth gate's session.
- If that gate scopes its cookie to `.<domain>` (single sign-on across
  previews), every preview makes credentialed same-site requests to every
  other preview and to the listing.

So:

- Gate next and previews (`previewVhost`).
- Use host-only cookies for the gate (no `Domain` attribute), so each
  preview signs in on its own.
- If you deploy branches from people you do not trust, serve `domain` from
  its own registrable domain (one that hosts nothing else, like GitHub Pages
  and Netlify do with their public-suffix domains), so previews cannot reach
  cookies or same-site requests of your other hosts.

A Content-Security-Policy for prod, next and previews is the site's
business; muelle only sets one on the listing and `/status/` (see
[Security model](../explanation/security.md#headers)).

# The muelle domain vhost: `/hooks/` to the webhook daemon, `/status/<id>`
# and the listing of next/preview environments at `/`. Also what every
# vhost muelle declares shares: security headers and the preview gate.
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkOption mkIf types;
  cfg = config.services.muelle;
  headers = import ../headers.nix lib;

  # the listing has no scripts and only an inline stylesheet
  listingOverrides = {
    Content-Security-Policy = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
    Referrer-Policy = "no-referrer";
  };
  listingHeaders =
    if cfg.securityHeaders != null then
      headers.render listingOverrides + cfg.securityHeaders
    else
      headers.render (cfg.headers // listingOverrides);
in
{
  options.services.muelle = {
    hookRateLimit = mkOption {
      type = types.str;
      default = "20r/m";
      description = "nginx `limit_req` rate for `/hooks/`, per client IP (burst 5).";
    };
    headers = mkOption {
      type = types.attrsOf (types.nullOr types.str);
      example = {
        X-Frame-Options = null;
        Content-Security-Policy = "frame-ancestors https://partner.example";
      };
      description = ''
        Response headers sent by every vhost muelle declares, as
        `name = value`; `null` drops one. Defaults, each overridable on its
        own:

        ${lib.concatStrings (lib.mapAttrsToList (n: v: "- `${n}: ${v}`\n") headers.defaults)}
        HSTS leaves out `includeSubDomains` and `preload`: the apex may not be
        yours. Per site: `sites.<name>.headers`. The listing and `/status/`
        always send their own strict `Content-Security-Policy` and
        `Referrer-Policy`. nginx drops server-level `add_header` directives in
        a location that sets its own: repeat them there.
      '';
    };
    securityHeaders = mkOption {
      type = types.nullOr types.lines;
      default = null;
      description = ''
        Deprecated, use `headers`. When set, these nginx `add_header`
        directives replace `headers` on every vhost, as before 3.2.0; `""`
        sends none.
      '';
    };
    previewVhost = mkOption {
      type = types.deferredModule;
      default = { };
      example = {
        enableAuth = true;
      };
      description = ''
        A `services.nginx.virtualHosts.<name>` module merged into every
        next/preview vhost and into the `domain` vhost (the listing), e.g.
        an auth gate. Definitions merge like any vhost definition: per-site
        `previewVhost` and `indexVhost` add to it, and override its values
        with `lib.mkForce`.
      '';
    };
    indexVhost = mkOption {
      type = types.deferredModule;
      default = { };
      example = lib.literalExpression ''{ locations."/".extraConfig = "auth_request /auth;"; }'';
      description = ''
        A `services.nginx.virtualHosts.<name>` module merged into the `domain`
        vhost, after `previewVhost`. Its `/` lists the next/preview
        deployments: gate it, it reveals branch names and URLs. Gate
        `location /` only (as `enableAuth`-style modules do): `/hooks/` and
        `/status/` are separate locations and must stay reachable from CI.
      '';
    };
  };

  config = mkIf cfg.enable {
    # one definition per header, so a host overrides them one by one
    services.muelle.headers = lib.mapAttrs (_: lib.mkDefault) headers.defaults;

    warnings = lib.optional (cfg.securityHeaders != null) ''
      services.muelle.securityHeaders is deprecated: set services.muelle.headers
      (name = value; null drops one) instead.
    '';

    services.nginx.appendHttpConfig = ''
      limit_req_zone $binary_remote_addr zone=muelle_hooks:1m rate=${cfg.hookRateLimit};
    '';

    # a function: nginx's vhost submodule reads a plain attrset as config only
    services.nginx.virtualHosts.${cfg.domain} = _: {
      imports = [
        {
          useACMEHost = cfg.acmeHost;
          forceSSL = true;
          # listing of next/preview environments
          root = "${cfg.stateDir}/index";
          extraConfig = ''
            add_header X-Robots-Tag "noindex, nofollow" always;
            add_header Cache-Control "no-store" always;
          ''
          + listingHeaders;
          locations."/hooks/" = {
            proxyPass = "http://127.0.0.1:${toString cfg.port}";
            extraConfig = ''
              limit_req zone=muelle_hooks burst=5 nodelay;
              client_max_body_size 64k;
            '';
          };
          locations."~ ^/status/(?<muelle_id>[A-Za-z0-9-]+)$" = {
            alias = "${cfg.stateDir}/status/$muelle_id.json";
            extraConfig = ''
              default_type application/json;
              add_header Cache-Control "no-store" always;
            ''
            + listingHeaders;
          };
        }
        cfg.previewVhost
        cfg.indexVhost
      ];
    };
  };
}

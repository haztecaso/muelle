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

  # the listing has no scripts and only an inline stylesheet
  listingHeaders = ''
    add_header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always;
    add_header Referrer-Policy "no-referrer" always;
  ''
  + cfg.securityHeaders;
in
{
  options.services.muelle = {
    hookRateLimit = mkOption {
      type = types.str;
      default = "20r/m";
      description = "nginx `limit_req` rate for `/hooks/`, per client IP (burst 5).";
    };
    securityHeaders = mkOption {
      type = types.lines;
      default = ''
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
      '';
      description = ''
        nginx `add_header` directives sent by every vhost muelle declares. HSTS
        leaves out `includeSubDomains` and `preload`: the apex may not be yours.
        Set to `""` to manage the headers yourself, or per site through
        `sites.<name>.securityHeaders`. nginx drops server-level `add_header`
        directives in a location that sets its own: repeat them there.
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

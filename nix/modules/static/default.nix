# Static sites: per-site serving options and the vhosts built from them.
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkOption mkIf types;
  cfg = config.services.muelle;

  siteModule =
    { name, config, ... }:
    {
      options = {
        domain = mkOption {
          type = types.nullOr types.str;
          default = null;
          example = "example.com";
          description = ''
            Domain of the prod environment. When set, muelle declares its nginx vhost:
            HTTPS only, rooted at `root`, `try_files` like next/preview, logs in
            `/var/log/nginx/<site>-{error,access}.log`. Extend it through
            `services.nginx.virtualHosts.<domain>` (e.g. extra locations).
          '';
        };
        acmeHost = mkOption {
          type = types.nullOr types.str;
          default = config.domain;
          defaultText = lib.literalExpression "domain";
          description = "ACME cert (security.acme.certs name) of the prod vhost.";
        };
        extraConfig = mkOption {
          type = types.lines;
          default = "";
          description = "Extra nginx config for the prod vhost's server block.";
        };
        previewApex = mkOption {
          type = types.nullOr types.str;
          default = null;
          example = "example.com";
          description = ''
            Apex domain whose `next.<apex>` and `<hash>.<apex>` subdomains expose this
            site's next/preview slots: a 302 to `<slot>-<site>.<domain>`, or the slot
            itself with `publicPreviews`. Needs an ACME cert of the same name covering `*.<apex>`.
          '';
        };
        publicPreviews = mkOption {
          type = types.bool;
          default = false;
          description = ''
            Serve next/previews on `previewApex` without `previewVhost` (no gate) and
            report their URLs there. Their content then shares a site with prod: see
            [Gating previews](../guides/previews.md). `<slot>-<site>.<domain>` stays as it is.
          '';
        };
        headers = mkOption {
          type = types.attrsOf (types.nullOr types.str);
          default = { };
          example = {
            X-Frame-Options = null;
            Content-Security-Policy = "frame-ancestors https://partner.example";
          };
          description = ''
            Response headers of this site's prod, next/preview and apex vhosts,
            merged header by header over `services.muelle.headers`; `null` drops one.
          '';
        };
        securityHeaders = mkOption {
          type = types.nullOr types.lines;
          default = null;
          description = ''
            Deprecated, use `headers`. When set, these nginx `add_header`
            directives replace this site's headers, as before 3.2.0.
          '';
        };
        immutablePaths = mkOption {
          type = types.listOf (types.strMatching "/.*");
          default = [ ];
          example = [ "/_astro/" ];
          description = ''
            URL path prefixes whose files never change, because their names
            carry a content hash (e.g. Astro's `/_astro/`, Vite's `/assets/`).
            They are sent with `Cache-Control: public, max-age=31536000,
            immutable` on prod, next and previews, so browsers keep them a
            year without revalidating. Never list paths whose files change in
            place: browsers would keep stale copies.
          '';
        };
        previewVhost = mkOption {
          type = types.deferredModule;
          default = { };
          example = lib.literalExpression "{ basicAuthFile = \"/run/secrets/htpasswd\"; }";
          description = ''
            A `services.nginx.virtualHosts.<name>` module merged into this
            site's next/preview vhost, after `services.muelle.previewVhost`.
          '';
        };
        publicPreviewVhost = mkOption {
          type = types.deferredModule;
          default = { };
          example = lib.literalExpression ''{ extraConfig = "error_page 404 /404.html;"; }'';
          description = ''
            A `services.nginx.virtualHosts.<name>` module merged into this
            site's public next/preview vhost on `previewApex` (only with
            `publicPreviews`). Neither `previewVhost` is merged there, so
            this is the place for the site's own settings, such as error pages.
          '';
        };
        root = mkOption {
          type = types.str;
          readOnly = true;
          default = "${cfg.baseDir}/${name}/prod/current";
          defaultText = lib.literalExpression ''"''${baseDir}/<name>/prod/current"'';
          description = "Document root of the prod environment (a symlink flipped on deploy). Use it in your own vhost if `domain` is unset.";
        };
      };
      config = {
        prodUrl = mkIf (config.domain != null) (lib.mkDefault "https://${config.domain}");
        publicApex = mkIf config.publicPreviews config.previewApex;
      };
    };
in
{
  imports = [ ./vhosts.nix ];

  options.services.muelle = {
    accessLogFormat = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "withHost";
      description = "nginx `log_format` name for prod vhost access logs (must be defined elsewhere). Null uses nginx's default.";
    };
    # merged with the declaration in core/: adds the serving options to each site
    sites = mkOption {
      type = types.attrsOf (types.submodule siteModule);
    };
  };

  config = mkIf cfg.enable {
    warnings = lib.concatLists (
      lib.mapAttrsToList (
        name: s:
        lib.optional (s.securityHeaders != null) ''
          services.muelle.sites.${name}.securityHeaders is deprecated: set
          services.muelle.sites.${name}.headers (name = value; null drops one) instead.
        ''
      ) cfg.sites
    );
    assertions = [
      {
        assertion = lib.allUnique (
          lib.filter (d: d != null) (lib.mapAttrsToList (_: s: s.domain) cfg.sites)
        );
        message = "services.muelle.sites: two sites share the same domain";
      }
      {
        assertion = lib.allUnique (
          lib.filter (d: d != null) (lib.mapAttrsToList (_: s: s.previewApex) cfg.sites)
        );
        message = "services.muelle.sites: two sites share the same previewApex";
      }
    ]
    ++ lib.mapAttrsToList (name: s: {
      assertion = s.publicPreviews -> s.previewApex != null;
      message = "services.muelle.sites.${name}: publicPreviews needs previewApex";
    }) cfg.sites;
  };
}

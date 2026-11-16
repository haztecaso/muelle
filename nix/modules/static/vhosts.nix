# nginx vhosts of static sites:
#   prod      <domain>                     rooted at <baseDir>/<site>/prod/current
#   preview   <slot>-<site>.<muelle domain> one regex vhost per site for next and previews
#   apex      {next,<hash>}.<previewApex>   302 to the preview vhost, or with
#                                          publicPreviews the slot itself, ungated
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkIf;
  cfg = config.services.muelle;

  escapeRe = lib.replaceStrings [ "." "-" ] [ "\\." "\\-" ];

  prodVhosts = lib.mapAttrs' (
    name: s:
    lib.nameValuePair s.domain {
      useACMEHost = s.acmeHost;
      forceSSL = true;
      inherit (s) root;
      extraConfig = ''
        error_log /var/log/nginx/${name}-error.log warn;
        access_log /var/log/nginx/${name}-access.log${
          lib.optionalString (cfg.accessLogFormat != null) " ${cfg.accessLogFormat}"
        };
        try_files $uri $uri.html $uri/ =404;
      ''
      + s.securityHeaders
      + s.extraConfig;
    }
  ) (lib.filterAttrs (_: s: s.domain != null) cfg.sites);

  previewVhosts = lib.mapAttrs' (
    name: s:
    # a function, so `imports` is not read as a vhost option
    lib.nameValuePair "muelle-preview-${name}" (_: {
      imports = [
        {
          # quoted: nginx needs quotes around regexes containing '{'
          serverName = "\"~^(?!prod-)(?<muelle_slot>[a-z0-9]+)-${escapeRe name}\\.${escapeRe cfg.domain}$\"";
          useACMEHost = cfg.acmeHost;
          forceSSL = true;
          root = "${cfg.baseDir}/${name}/$muelle_slot/current";
          extraConfig = ''
            add_header X-Robots-Tag "noindex, nofollow" always;
            try_files $uri $uri.html $uri/ =404;
          ''
          + s.securityHeaders;
        }
        cfg.previewVhost
        s.previewVhost
      ];
    })
  ) (lib.filterAttrs (_: s: lib.any (e: e != "prod") s.envs) cfg.sites);

  # unlike the preview vhost, never merges previewVhost: public means ungated
  apexVhosts = lib.mapAttrs' (
    name: s:
    lib.nameValuePair "muelle-apex-${name}" (
      {
        serverName = "\"~^(?<muelle_slot>next|[0-9a-f]{8})\\.${escapeRe s.previewApex}$\"";
        useACMEHost = s.previewApex;
        forceSSL = true;
      }
      // (
        if s.publicPreviews then
          {
            root = "${cfg.baseDir}/${name}/$muelle_slot/current";
            extraConfig = ''
              add_header X-Robots-Tag "noindex, nofollow" always;
              try_files $uri $uri.html $uri/ =404;
            ''
            + s.securityHeaders;
          }
        else
          {
            locations."/".return = "302 https://$muelle_slot-${name}.${cfg.domain}$request_uri";
            extraConfig = s.securityHeaders;
          }
      )
    )
  ) (lib.filterAttrs (_: s: s.previewApex != null) cfg.sites);
in
{
  config = mkIf cfg.enable {
    services.nginx.virtualHosts = lib.mkMerge [
      prodVhosts
      previewVhosts
      apexVhosts
    ];
  };
}

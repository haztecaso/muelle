{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkOption
    mkEnableOption
    mkIf
    mkMerge
    types
    ;
  cfg = config.services.muelle;
  muelle = pkgs.callPackage ./package.nix { };

  escapeRe = lib.replaceStrings [ "." "-" ] [ "\\." "\\-" ];

  siteType = types.submodule (
    { name, config, ... }:
    {
      options = {
        repo = mkOption {
          type = types.strMatching "[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+";
          example = "octocat/example.com";
          description = "GitHub repository (owner/name) allowed to deploy this site. Artifacts are only fetched from its API.";
        };
        secretFile = mkOption {
          type = types.path;
          description = ''
            File holding the HMAC secret for this site (e.g. an agenix secret path).
            Read by systemd through LoadCredential, so it can stay root-only.
            Use a hex string (`openssl rand -hex 32`).
          '';
        };
        envs = mkOption {
          type = types.listOf (
            types.enum [
              "prod"
              "next"
              "preview"
            ]
          );
          default = [
            "prod"
            "next"
            "preview"
          ];
          description = "Environments this site may deploy to. `[ \"prod\" ]` disables next and previews.";
        };
        prodBranches = mkOption {
          type = types.nullOr (types.listOf types.str);
          default = [ "main" ];
          example = [
            "main"
            "release"
          ];
          description = ''
            Branches allowed to deploy to prod; requests for prod from any other
            branch are rejected. Null allows every branch. The branch comes from
            the signed payload, so this guards against mistakes (a feature branch
            deploying to prod), not against a leaked secret.
          '';
        };
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
        prodUrl = mkOption {
          type = types.str;
          example = "https://example.com";
          defaultText = lib.literalExpression ''"https://''${domain}" if domain is set'';
          description = "Public URL of the prod environment, reported back to CI.";
        };
        redirectFrom = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [ "example.com" ];
          description = ''
            Apex domains whose `next.<apex>` and `<hash>.<apex>` subdomains 302 to the
            `<slot>-<site>.<domain>`. Each apex needs an ACME cert of the same name covering `*.<apex>`.
          '';
        };
        previewTtlDays = mkOption {
          type = types.nullOr types.ints.positive;
          default = cfg.previewTtlDays;
          defaultText = lib.literalExpression "config.services.muelle.previewTtlDays";
          description = "Days after its last deploy a preview of this site is removed. Null keeps previews until pruned by count.";
        };
        securityHeaders = mkOption {
          type = types.lines;
          default = cfg.securityHeaders;
          defaultText = lib.literalExpression "config.services.muelle.securityHeaders";
          description = "`add_header` directives of this site's prod, next/preview and redirect vhosts.";
        };
        previewVhost = mkOption {
          type = types.attrs;
          default = cfg.previewVhostDefaults;
          defaultText = lib.literalExpression "config.services.muelle.previewVhostDefaults";
          description = "Extra nginx vhost attributes for this site's next/preview vhost (e.g. auth gating).";
        };
        root = mkOption {
          type = types.str;
          readOnly = true;
          default = "${cfg.baseDir}/${name}/prod/current";
          description = "Document root of the prod environment (a symlink flipped on deploy). Use it in your own vhost if `domain` is unset.";
        };
      };
      config.prodUrl = lib.mkIf (config.domain != null) (lib.mkDefault "https://${config.domain}");
    }
  );

  # keys match the fields of muelle.config.Config
  configFile = (pkgs.formats.json { }).generate "muelle-config.json" {
    inherit (cfg) domain;
    base_dir = cfg.baseDir;
    state_dir = cfg.stateDir;
    max_skew = cfg.maxSkew;
    keep_releases = cfg.keepReleases;
    keep_previews = cfg.keepPreviews;
    max_artifact_bytes = cfg.maxArtifactBytes;
    max_extracted_bytes = cfg.maxExtractedBytes;
    max_artifact_files = cfg.maxArtifactFiles;
    sites = lib.mapAttrs (name: s: {
      inherit (s) envs repo;
      prod_url = s.prodUrl;
      preview_ttl_days = s.previewTtlDays;
      prod_branches = s.prodBranches;
      artifact_url_prefix = "${cfg.artifactUrlBase}/${s.repo}/";
    }) cfg.sites;
  };

  hook =
    name: _:
    builtins.toJSON {
      id = name;
      execute-command = "${muelle}/bin/muelle-receive";
      http-methods = [ "POST" ];
      incoming-payload-content-type = "application/json";
      include-command-output-in-response = true;
      include-command-output-in-response-on-error = true;
      trigger-rule-mismatch-http-response-code = 403;
      response-headers = [
        {
          name = "Content-Type";
          value = "application/json";
        }
      ];
      pass-arguments-to-command = [
        {
          source = "string";
          name = name;
        }
      ];
      pass-environment-to-command = [
        {
          source = "entire-payload";
          envname = "MUELLE_PAYLOAD";
        }
      ];
      trigger-rule.match = {
        type = "payload-hmac-sha256";
        # backtick string: toJSON would escape double quotes inside the template action
        secret = "{{ credential `muelle-${name}` }}";
        parameter = {
          source = "header";
          name = "X-Muelle-Signature";
        };
      };
    };

  previewVhosts = lib.mapAttrs' (
    name: s:
    lib.nameValuePair "muelle-preview-${name}" (mkMerge [
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
      s.previewVhost
    ])
  ) (lib.filterAttrs (_: s: lib.any (e: e != "prod") s.envs) cfg.sites);

  # the listing has no scripts and only an inline stylesheet
  listingHeaders = ''
    add_header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always;
    add_header Referrer-Policy "no-referrer" always;
  ''
  + cfg.securityHeaders;

  prodSites = lib.filterAttrs (_: s: s.domain != null) cfg.sites;

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
  ) prodSites;

  redirectVhosts = lib.listToAttrs (
    lib.concatLists (
      lib.mapAttrsToList (
        name: s:
        map (
          apex:
          lib.nameValuePair "muelle-redirect-${name}-${apex}" {
            serverName = "\"~^(?<muelle_slot>next|[0-9a-f]{8})\\.${escapeRe apex}$\"";
            useACMEHost = apex;
            forceSSL = true;
            locations."/".return = "302 https://$muelle_slot-${name}.${cfg.domain}$request_uri";
            extraConfig = s.securityHeaders;
          }
        ) s.redirectFrom
      ) cfg.sites
    )
  );
in
{
  imports = [
    (lib.mkRemovedOptionModule [
      "services"
      "muelle"
      "previewDomain"
    ] "next and preview environments are now served under `services.muelle.domain`.")
  ];

  options.services.muelle = {
    enable = mkEnableOption "muelle deploy receiver";

    domain = mkOption {
      type = types.str;
      example = "muelle.example.com";
      description = ''
        Host serving `/hooks/<site>`, `/status/<id>` and the listing of
        next/preview environments at `/`. Those environments are served at
        `next-<site>.<domain>` and `<hash>-<site>.<domain>`.
      '';
    };
    acmeHost = mkOption {
      type = types.str;
      description = "ACME cert (security.acme.certs name) covering `domain` and `*.<domain>`.";
    };
    baseDir = mkOption {
      type = types.str;
      default = "/var/www/muelle";
      description = "Root of `<site>/<slot>/{releases,current}`.";
    };
    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/muelle";
      description = "Status files, spool, logs and locks.";
    };
    port = mkOption {
      type = types.port;
      default = 9077;
      description = "Local port of the webhook daemon.";
    };
    maxSkew = mkOption {
      type = types.int;
      default = 300;
      description = "Seconds a request timestamp may differ from the server clock.";
    };
    keepReleases = mkOption {
      type = types.ints.positive;
      default = 3;
      description = "Releases kept per slot.";
    };
    keepPreviews = mkOption {
      type = types.ints.positive;
      default = 10;
      description = "Preview slots kept per site (oldest removed first).";
    };
    previewTtlDays = mkOption {
      type = types.nullOr types.ints.positive;
      default = 30;
      description = ''
        Days after its last deploy a preview is removed (daily, by `muelle-prune.timer`).
        Null disables expiry. Overridable per site.
      '';
    };
    maxArtifactBytes = mkOption {
      type = types.ints.positive;
      default = 200 * 1024 * 1024;
      description = "Maximum artifact download size.";
    };
    maxExtractedBytes = mkOption {
      type = types.ints.positive;
      default = 2 * 1024 * 1024 * 1024;
      description = "Maximum total uncompressed size of an artifact (guards against zip bombs).";
    };
    maxArtifactFiles = mkOption {
      type = types.ints.positive;
      default = 50000;
      description = "Maximum number of entries (files and directories) in an artifact.";
    };
    artifactUrlBase = mkOption {
      type = types.str;
      default = "https://api.github.com/repos";
      description = "Artifacts must be fetched from `<artifactUrlBase>/<repo>/`.";
    };
    hookRateLimit = mkOption {
      type = types.str;
      default = "20r/m";
      description = "nginx `limit_req` rate for `/hooks/`, per client IP (burst 5).";
    };
    accessLogFormat = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "withHost";
      description = "nginx `log_format` name for prod vhost access logs (must be defined elsewhere). Null uses nginx's default.";
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
    previewVhostDefaults = mkOption {
      type = types.attrs;
      default = { };
      example = {
        enableAuth = true;
      };
      description = "Default extra vhost attributes for next/preview vhosts.";
    };
    indexVhost = mkOption {
      type = types.attrs;
      default = cfg.previewVhostDefaults;
      defaultText = lib.literalExpression "config.services.muelle.previewVhostDefaults";
      example = {
        enableAuth = true;
      };
      description = ''
        Extra nginx vhost attributes for the `domain` vhost, whose `/` lists the
        next/preview deployments. Gate it: it reveals branch names and URLs.
        Gate `location /` only (as `enableAuth`-style modules do): `/hooks/` and
        `/status/` are separate locations and must stay reachable from CI.
      '';
    };
    sites = mkOption {
      type = types.attrsOf siteType;
      default = { };
      description = "Sites that can be deployed.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.sites != { };
        message = "services.muelle.sites must not be empty";
      }
    ]
    ++ lib.mapAttrsToList (name: _: {
      assertion =
        builtins.match "[a-z0-9][a-z0-9-]*" name != null && builtins.match "prod|next" name == null;
      message = "services.muelle.sites.${name}: name must match [a-z0-9][a-z0-9-]*";
    }) cfg.sites
    ++ [
      {
        assertion = lib.allUnique (lib.mapAttrsToList (_: s: s.domain) prodSites);
        message = "services.muelle.sites: two sites share the same domain";
      }
    ];

    users.users.muelle = {
      isSystemUser = true;
      group = "muelle";
      description = "muelle deploy receiver";
    };
    users.groups.muelle = { };

    systemd.tmpfiles.rules = [
      "d ${cfg.baseDir} 0755 muelle muelle -"
      "d ${cfg.stateDir} 0755 muelle muelle -"
      "d ${cfg.stateDir}/status 0755 muelle muelle 30d"
      "d ${cfg.stateDir}/spool 0700 muelle muelle -"
      "d ${cfg.stateDir}/log 0750 muelle muelle 30d"
      "d ${cfg.stateDir}/locks 0750 muelle muelle -"
      "d ${cfg.stateDir}/index 0755 muelle muelle -"
    ]
    ++ lib.mapAttrsToList (name: _: "d ${cfg.baseDir}/${name} 0755 muelle muelle -") cfg.sites;

    services.webhook = {
      enable = true;
      user = "muelle";
      group = "muelle";
      ip = "127.0.0.1";
      # verbose logs every request's environment, including the GitHub token
      verbose = false;
      inherit (cfg) port;
      environment.MUELLE_CONFIG = "${configFile}";
      hooksTemplated = lib.mapAttrs hook cfg.sites;
    };

    # refresh the listing on (re)start, e.g. after sites change
    systemd.services.webhook.preStart = "${muelle}/bin/muelle-index";

    systemd.services.webhook.serviceConfig = {
      LoadCredential = lib.mapAttrsToList (name: s: "muelle-${name}:${s.secretFile}") cfg.sites;
      UMask = "0022";
      NoNewPrivileges = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectControlGroups = true;
      RestrictSUIDSGID = true;
      ReadWritePaths = [
        cfg.baseDir
        cfg.stateDir
      ];
      # Default KillMode (control-group) on purpose: the unit's main process is
      # a wrapper script, so KillMode=process would leave the old daemon
      # holding the port. In-flight workers die with it and report failure.
    };

    systemd.services.muelle-prune = {
      description = "Remove expired muelle previews";
      environment.MUELLE_CONFIG = "${configFile}";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${muelle}/bin/muelle-prune";
        User = "muelle";
        Group = "muelle";
        UMask = "0022";
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        ReadWritePaths = [
          cfg.baseDir
          cfg.stateDir
        ];
      };
    };
    systemd.timers.muelle-prune = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        RandomizedDelaySec = "1h";
      };
    };

    services.nginx.appendHttpConfig = ''
      limit_req_zone $binary_remote_addr zone=muelle_hooks:1m rate=${cfg.hookRateLimit};
    '';

    services.nginx.virtualHosts = mkMerge [
      {
        ${cfg.domain} = mkMerge [
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
          cfg.indexVhost
        ];
      }
      prodVhosts
      previewVhosts
      redirectVhosts
    ];
  };
}

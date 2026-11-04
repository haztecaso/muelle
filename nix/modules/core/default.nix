# Site table, user and directories. `sites` is also declared by static/,
# which adds the options that only matter for serving.
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkOption mkIf types;
  cfg = config.services.muelle;

  siteModule = {
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
      prodUrl = mkOption {
        type = types.str;
        example = "https://example.com";
        defaultText = lib.literalExpression ''"https://''${domain}" if domain is set'';
        description = "Public URL of the prod environment, reported back to CI.";
      };
      previewTtlDays = mkOption {
        type = types.nullOr types.ints.positive;
        default = cfg.previewTtlDays;
        defaultText = lib.literalExpression "config.services.muelle.previewTtlDays";
        description = "Days after its last deploy a preview of this site is removed. Null keeps previews until pruned by count.";
      };
    };
  };
in
{
  imports = [
    ./settings.nix
    ./units.nix
  ];

  options.services.muelle = {
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
    sites = mkOption {
      type = types.attrsOf (types.submodule siteModule);
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
      # prod and next are slot names: `<slot>-<site>` would be ambiguous
      assertion =
        builtins.match "[a-z0-9][a-z0-9-]*" name != null && builtins.match "prod|next" name == null;
      message = "services.muelle.sites.${name}: name must match [a-z0-9][a-z0-9-]* and not be prod or next";
    }) cfg.sites;

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
      "d ${cfg.stateDir}/jobs 0750 muelle muelle 30d"
      "d ${cfg.stateDir}/locks 0750 muelle muelle -"
      "d ${cfg.stateDir}/index 0755 muelle muelle -"
    ]
    ++ lib.mapAttrsToList (name: _: "d ${cfg.baseDir}/${name} 0755 muelle muelle -") cfg.sites;
  };
}

# The config JSON read by the Python side (MUELLE_CONFIG) and the admin CLI
# wrapping it.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkOption mkIf types;
  cfg = config.services.muelle;

  # admin CLI: runs as muelle (like the worker) with the module's config
  cli = pkgs.writeShellScriptBin "muelle" ''
    export MUELLE_CONFIG=${cfg.configFile}
    umask 0022
    cmd=(${cfg.package}/bin/muelle "$@")
    case "$(id -un)" in
      muelle) exec "''${cmd[@]}" ;;
      root) exec ${pkgs.util-linux}/bin/runuser -u muelle -- "''${cmd[@]}" ;;
      # sudo resets the environment: run this wrapper again as muelle
      *) exec sudo -u muelle "$0" "$@" ;;
    esac
  '';
in
{
  options.services.muelle = {
    settings = mkOption {
      type = types.attrs;
      internal = true;
      readOnly = true;
      # keys are the fields of muelle.config.Config and Site (checked by checks.config-parity)
      default = {
        inherit (cfg) domain;
        base_dir = cfg.baseDir;
        state_dir = cfg.stateDir;
        max_skew = cfg.maxSkew;
        keep_releases = cfg.keepReleases;
        keep_previews = cfg.keepPreviews;
        max_artifact_bytes = cfg.maxArtifactBytes;
        max_extracted_bytes = cfg.maxExtractedBytes;
        max_artifact_files = cfg.maxArtifactFiles;
        sites = lib.mapAttrs (_: s: {
          inherit (s) envs repo;
          prod_url = s.prodUrl;
          preview_ttl_days = s.previewTtlDays;
          prod_branches = s.prodBranches;
          artifact_url_prefix = "${cfg.artifactUrlBase}/${s.repo}/";
        }) cfg.sites;
      };
      description = "Contents of the config JSON, generated from the options.";
    };
    configFile = mkOption {
      type = types.path;
      internal = true;
      readOnly = true;
      default = (pkgs.formats.json { }).generate "muelle-config.json" cfg.settings;
      defaultText = lib.literalExpression "JSON of config.services.muelle.settings";
      description = "The config JSON passed as `MUELLE_CONFIG`.";
    };
  };

  config = mkIf cfg.enable {
    # wins over a plain muelle package in systemPackages
    environment.systemPackages = [ (lib.hiPrio cli) ];
  };
}

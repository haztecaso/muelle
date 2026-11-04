# services.muelle: options shared by every part of the module.
#
#   core/    user, directories, config JSON, admin CLI, webhook and prune units
#   nginx/   the muelle domain vhost (hooks, status, listing), shared headers
#   static/  static sites: prod, next/preview and redirect vhosts
{
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkOption mkEnableOption types;
in
{
  imports = [
    ./core
    ./nginx
    ./static
  ];

  options.services.muelle = {
    enable = mkEnableOption "muelle deploy receiver";

    package = mkOption {
      type = types.package;
      internal = true;
      readOnly = true;
      default = pkgs.callPackage ../../package.nix { };
      description = "The muelle package run by every unit and the admin CLI.";
    };

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
      description = "Status and job files, spool, logs and locks.";
    };
  };
}

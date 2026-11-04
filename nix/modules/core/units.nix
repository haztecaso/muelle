# systemd units: the webhook daemon receiving `/hooks/<site>` and the daily
# prune. Both run the muelle package as the muelle user.
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkIf;
  cfg = config.services.muelle;
  muelle = cfg.package;

  # every muelle unit: write access to baseDir and stateDir only
  hardening = {
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
          inherit name;
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
in
{
  config = mkIf cfg.enable {
    services.webhook = {
      enable = true;
      user = "muelle";
      group = "muelle";
      ip = "127.0.0.1";
      # verbose logs every request's environment, including the GitHub token
      verbose = false;
      inherit (cfg) port;
      environment.MUELLE_CONFIG = "${cfg.configFile}";
      hooksTemplated = lib.mapAttrs hook cfg.sites;
    };

    # refresh the listing on (re)start, e.g. after sites change
    systemd.services.webhook.preStart = "${muelle}/bin/muelle index";

    systemd.services.webhook.serviceConfig = hardening // {
      LoadCredential = lib.mapAttrsToList (name: s: "muelle-${name}:${s.secretFile}") cfg.sites;
      # Default KillMode (control-group) on purpose: the unit's main process is
      # a wrapper script, so KillMode=process would leave the old daemon
      # holding the port. In-flight workers die with it and report failure.
    };

    systemd.services.muelle-prune = {
      description = "Prune muelle previews and releases";
      environment.MUELLE_CONFIG = "${cfg.configFile}";
      serviceConfig = hardening // {
        Type = "oneshot";
        ExecStart = "${muelle}/bin/muelle prune --no-record";
        User = "muelle";
        Group = "muelle";
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
  };
}

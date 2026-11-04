# Module evaluation tests: no VM, run by `nix flake check` (also --no-build).
{
  pkgs,
  nixosSystem,
  muelleModule,
}:
let
  inherit (pkgs) lib;

  base = {
    nixpkgs.hostPlatform = pkgs.stdenv.hostPlatform.system;
    system.stateVersion = "26.05";
    services.muelle = {
      enable = true;
      domain = "muelle.test";
      acmeHost = "muelle.test";
      sites = lib.mkDefault {
        demo = {
          repo = "owner/demo";
          secretFile = "/run/secrets/demo";
          domain = "demo.test";
          redirectFrom = [ "demo.test" ];
        };
        prodonly = {
          repo = "owner/prodonly";
          secretFile = "/run/secrets/prodonly";
          prodUrl = "https://prodonly.test";
          envs = [ "prod" ];
          prodBranches = null;
        };
      };
    };
  };

  eval =
    module:
    (nixosSystem {
      modules = [
        muelleModule
        base
        module
      ];
    }).config;

  default = eval { };
  vhosts = c: c.services.nginx.virtualHosts;

  failedAssertions =
    module:
    map (a: a.message) (
      lib.filter (a: !a.assertion && lib.hasPrefix "services.muelle" a.message) (eval module).assertions
    );

  # vhosts declared by muelle for the default config
  muelleVhosts =
    c:
    lib.attrVals [ "muelle.test" "demo.test" "muelle-preview-demo" "muelle-redirect-demo-demo.test" ] (
      vhosts c
    );

  tests = {
    testAssertionsPass = {
      expr = failedAssertions { };
      expected = [ ];
    };
    testEmptySites = {
      expr = failedAssertions { services.muelle.sites = lib.mkForce { }; };
      expected = [ "services.muelle.sites must not be empty" ];
    };
    testInvalidNames = {
      expr = failedAssertions {
        services.muelle.sites = lib.mkForce {
          Bad_name = {
            repo = "o/a";
            secretFile = "/s";
            prodUrl = "https://a";
          };
          next = {
            repo = "o/b";
            secretFile = "/s";
            prodUrl = "https://b";
          };
        };
      };
      expected = [
        "services.muelle.sites.Bad_name: name must match [a-z0-9][a-z0-9-]* and not be prod or next"
        "services.muelle.sites.next: name must match [a-z0-9][a-z0-9-]* and not be prod or next"
      ];
    };
    testDuplicateDomains = {
      expr = failedAssertions {
        services.muelle.sites = lib.mkForce {
          a = {
            repo = "o/a";
            secretFile = "/s";
            domain = "same.test";
          };
          b = {
            repo = "o/b";
            secretFile = "/s";
            domain = "same.test";
          };
          # sites without a domain don't clash
          c = {
            repo = "o/c";
            secretFile = "/s";
            prodUrl = "https://c";
          };
          d = {
            repo = "o/d";
            secretFile = "/s";
            prodUrl = "https://d";
          };
        };
      };
      expected = [ "services.muelle.sites: two sites share the same domain" ];
    };

    testSettings = {
      expr = default.services.muelle.settings.sites;
      expected = {
        demo = {
          repo = "owner/demo";
          envs = [
            "prod"
            "next"
            "preview"
          ];
          prod_url = "https://demo.test";
          preview_ttl_days = 30;
          prod_branches = [ "main" ];
          artifact_url_prefix = "https://api.github.com/repos/owner/demo/";
        };
        prodonly = {
          repo = "owner/prodonly";
          envs = [ "prod" ];
          prod_url = "https://prodonly.test";
          preview_ttl_days = 30;
          prod_branches = null;
          artifact_url_prefix = "https://api.github.com/repos/owner/prodonly/";
        };
      };
    };

    testCliInstalled = {
      expr = lib.any (
        p: p.name == "muelle" && p.meta.priority or 5 < 5
      ) default.environment.systemPackages;
      expected = true;
    };

    testVhostNames = {
      expr = lib.filter (n: n != "localhost") (lib.attrNames (vhosts default));
      expected = [
        "demo.test"
        "muelle-preview-demo"
        "muelle-redirect-demo-demo.test"
        "muelle.test"
      ];
    };
    testProdVhost = {
      expr = {
        inherit ((vhosts default)."demo.test") root useACMEHost forceSSL;
      };
      expected = {
        root = "/var/www/muelle/demo/prod/current";
        useACMEHost = "demo.test";
        forceSSL = true;
      };
    };
    testPreviewVhost = {
      expr = {
        inherit ((vhosts default)."muelle-preview-demo") serverName root useACMEHost;
      };
      expected = {
        serverName = ''"~^(?!prod-)(?<muelle_slot>[a-z0-9]+)-demo\.muelle\.test$"'';
        root = "/var/www/muelle/demo/$muelle_slot/current";
        useACMEHost = "muelle.test";
      };
    };
    testRedirectVhost = {
      expr = (vhosts default)."muelle-redirect-demo-demo.test".locations."/".return;
      expected = "302 https://$muelle_slot-demo.muelle.test$request_uri";
    };

    # previewVhost, sites.<name>.previewVhost and indexVhost deep-merge
    # with each other and with muelle's own definitions
    testVhostMerge =
      let
        v = vhosts (eval {
          services.muelle.previewVhost = {
            basicAuthFile = "/run/htpasswd";
            locations."/a".return = "200 a";
          };
          services.muelle.previewVhost.locations."/a".extraConfig = "# second definition";
          services.muelle.sites.demo.previewVhost.locations."/b".return = "200 b";
          services.muelle.indexVhost.locations."/".extraConfig = "# gate";
        });
      in
      {
        expr = {
          preview = {
            inherit (v."muelle-preview-demo") basicAuthFile forceSSL;
            locations = lib.mapAttrs (_: l: {
              inherit (l) return extraConfig;
            }) v."muelle-preview-demo".locations;
          };
          index = {
            inherit (v."muelle.test") basicAuthFile forceSSL;
            locations = lib.attrNames v."muelle.test".locations;
            gate = v."muelle.test".locations."/".extraConfig;
          };
        };
        expected = {
          preview = {
            basicAuthFile = "/run/htpasswd";
            forceSSL = true;
            locations = {
              "/a" = {
                return = "200 a";
                extraConfig = "# second definition";
              };
              "/b" = {
                return = "200 b";
                extraConfig = "";
              };
            };
          };
          index = {
            basicAuthFile = "/run/htpasswd";
            forceSSL = true;
            locations = [
              "/"
              "/a"
              "/hooks/"
              "~ ^/status/(?<muelle_id>[A-Za-z0-9-]+)$"
            ];
            gate = "# gate";
          };
        };
      };
    # vhost modules are type-checked against nginx's vhost options
    testVhostTypeChecked = {
      expr =
        (builtins.tryEval
          (vhosts (eval {
            services.muelle.previewVhost.notAnOption = true;
          }))."muelle-preview-demo".root
        ).success;
      expected = false;
    };

    testSecurityHeaders = {
      expr = map (v: lib.hasInfix "Strict-Transport-Security" v.extraConfig) (muelleVhosts default);
      expected = [
        true
        true
        true
        true
      ];
    };
    testSecurityHeadersOff = {
      expr = map (v: lib.hasInfix "Strict-Transport-Security" v.extraConfig) (
        muelleVhosts (eval {
          services.muelle.securityHeaders = "";
        })
      );
      expected = [
        false
        false
        false
        false
      ];
    };
  };
in
{
  failures = lib.runTests tests;
  # for checks.config-parity
  settings = (pkgs.formats.json { }).generate "muelle-config.json" default.services.muelle.settings;
}

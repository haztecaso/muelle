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
          previewApex = "demo.test";
        };
        pub = {
          repo = "owner/pub";
          secretFile = "/run/secrets/pub";
          domain = "pub.test";
          previewApex = "pub.test";
          publicPreviews = true;
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
    lib.attrVals [
      "muelle.test"
      "demo.test"
      "muelle-preview-demo"
      "muelle-apex-demo"
      "muelle-apex-pub"
    ] (vhosts c);

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
    testPreviewApexAssertions = {
      expr = failedAssertions {
        services.muelle.sites = lib.mkForce {
          a = {
            repo = "o/a";
            secretFile = "/s";
            prodUrl = "https://a";
            previewApex = "same.test";
          };
          b = {
            repo = "o/b";
            secretFile = "/s";
            prodUrl = "https://b";
            previewApex = "same.test";
          };
          c = {
            repo = "o/c";
            secretFile = "/s";
            prodUrl = "https://c";
            publicPreviews = true;
          };
        };
      };
      expected = [
        "services.muelle.sites: two sites share the same previewApex"
        "services.muelle.sites.c: publicPreviews needs previewApex"
      ];
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
          public_apex = null;
          preview_ttl_days = 30;
          prod_branches = [ "main" ];
          artifact_url_prefix = "https://api.github.com/repos/owner/demo/";
        };
        prodonly = {
          repo = "owner/prodonly";
          envs = [ "prod" ];
          prod_url = "https://prodonly.test";
          public_apex = null;
          preview_ttl_days = 30;
          prod_branches = null;
          artifact_url_prefix = "https://api.github.com/repos/owner/prodonly/";
        };
        pub = {
          repo = "owner/pub";
          envs = [
            "prod"
            "next"
            "preview"
          ];
          prod_url = "https://pub.test";
          public_apex = "pub.test";
          preview_ttl_days = 30;
          prod_branches = [ "main" ];
          artifact_url_prefix = "https://api.github.com/repos/owner/pub/";
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
        "muelle-apex-demo"
        "muelle-apex-pub"
        "muelle-preview-demo"
        "muelle-preview-pub"
        "muelle.test"
        "pub.test"
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
      expr = {
        inherit ((vhosts default)."muelle-apex-demo") serverName root useACMEHost;
        return = (vhosts default)."muelle-apex-demo".locations."/".return;
      };
      expected = {
        serverName = ''"~^(?<muelle_slot>next|[0-9a-f]{8})\.demo\.test$"'';
        root = null;
        useACMEHost = "demo.test";
        return = "302 https://$muelle_slot-demo.muelle.test$request_uri";
      };
    };
    # served on the apex, and the gate of previewVhost stays off it
    testPublicVhost =
      let
        v = vhosts (eval {
          services.muelle.previewVhost.basicAuthFile = "/run/htpasswd";
        });
      in
      {
        expr = {
          inherit (v."muelle-apex-pub")
            serverName
            root
            useACMEHost
            basicAuthFile
            ;
          locations = lib.attrNames v."muelle-apex-pub".locations;
          noindex = lib.hasInfix "X-Robots-Tag" v."muelle-apex-pub".extraConfig;
          gated = v."muelle-preview-pub".basicAuthFile;
        };
        expected = {
          serverName = ''"~^(?<muelle_slot>next|[0-9a-f]{8})\.pub\.test$"'';
          root = "/var/www/muelle/pub/$muelle_slot/current";
          useACMEHost = "pub.test";
          basicAuthFile = null;
          locations = [ ];
          noindex = true;
          gated = "/run/htpasswd";
        };
      };

    # publicPreviewVhost reaches the public apex vhost and nothing else
    testPublicPreviewVhost =
      let
        # replaces the default sites, so both are spelled out
        v = vhosts (eval {
          services.muelle.sites = {
            pub = {
              repo = "owner/pub";
              secretFile = "/run/secrets/pub";
              domain = "pub.test";
              previewApex = "pub.test";
              publicPreviews = true;
              publicPreviewVhost = {
                extraConfig = "error_page 404 /404.html;";
                locations."/x".return = "200 x";
              };
            };
            demo = {
              repo = "owner/demo";
              secretFile = "/run/secrets/demo";
              domain = "demo.test";
              previewApex = "demo.test";
              publicPreviewVhost.extraConfig = "error_page 404 /404.html;";
            };
          };
        });
        has404 = name: lib.hasInfix "error_page 404" v.${name}.extraConfig;
      in
      {
        expr = {
          pub = has404 "muelle-apex-pub";
          pubKeepsOwn = lib.hasInfix "X-Robots-Tag" v."muelle-apex-pub".extraConfig;
          pubLocations = lib.attrNames v."muelle-apex-pub".locations;
          pubPreview = has404 "muelle-preview-pub";
          # demo's apex only redirects: not public, so not merged
          demo = has404 "muelle-apex-demo";
          demoProd = has404 "demo.test";
        };
        expected = {
          pub = true;
          pubKeepsOwn = true;
          pubLocations = [ "/x" ];
          pubPreview = false;
          demo = false;
          demoProd = false;
        };
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
        false
      ];
    };
    # five headers by default on every vhost, the listing's CSP replaced
    testDefaultHeaders = {
      expr = map (
        v:
        map (h: lib.hasInfix "add_header ${h} " v.extraConfig) [
          "Strict-Transport-Security"
          "X-Content-Type-Options"
          "Referrer-Policy"
          "X-Frame-Options"
          "Content-Security-Policy"
        ]
      ) (muelleVhosts default);
      expected = lib.replicate 5 (lib.replicate 5 true);
    };
    testListingHeaders = {
      expr =
        let
          c = (vhosts default)."muelle.test".extraConfig;
        in
        {
          csp = lib.length (lib.splitString "Content-Security-Policy" c) - 1;
          strict = lib.hasInfix "default-src 'none'" c;
          referrer = lib.hasInfix ''Referrer-Policy "no-referrer"'' c;
        };
      expected = {
        csp = 1;
        strict = true;
        referrer = true;
      };
    };
    # global and per-site overrides, header by header
    testHeadersOverride =
      let
        v = vhosts (eval {
          services.muelle.headers.Referrer-Policy = "no-referrer";
          services.muelle.sites = {
            demo = {
              repo = "owner/demo";
              secretFile = "/run/secrets/demo";
              domain = "demo.test";
              headers = {
                X-Frame-Options = null;
                Content-Security-Policy = ''frame-ancestors "https://a.test"'';
                X-Extra = "1";
              };
            };
            other = {
              repo = "owner/other";
              secretFile = "/run/secrets/other";
              domain = "other.test";
            };
          };
        });
        has = name: h: lib.hasInfix "add_header ${h} " v.${name}.extraConfig;
      in
      {
        expr = {
          demo = map (has "demo.test") [
            "X-Frame-Options"
            "X-Extra"
            "Strict-Transport-Security"
          ];
          demoReferrer = lib.hasInfix ''Referrer-Policy "no-referrer"'' v."demo.test".extraConfig;
          escaped = lib.hasInfix ''"frame-ancestors \"https://a.test\""'' v."demo.test".extraConfig;
          other = map (has "other.test") [
            "X-Frame-Options"
            "X-Extra"
          ];
        };
        expected = {
          demo = [
            false
            true
            true
          ];
          demoReferrer = true;
          escaped = true;
          other = [
            true
            false
          ];
        };
      };
    testSecurityHeadersDeprecated = {
      expr =
        lib.any (lib.hasInfix "securityHeaders is deprecated")
          (eval {
            services.muelle.securityHeaders = "";
          }).warnings;
      expected = true;
    };

    testImmutablePaths =
      let
        c = eval {
          services.muelle.sites = {
            demo = {
              repo = "owner/demo";
              secretFile = "/run/secrets/demo";
              domain = "demo.test";
              previewApex = "demo.test";
              immutablePaths = [
                "/_astro/"
                "/a.b/"
              ];
            };
            pub = {
              repo = "owner/pub";
              secretFile = "/run/secrets/pub";
              domain = "pub.test";
              previewApex = "pub.test";
              publicPreviews = true;
              immutablePaths = [ "/assets/" ];
            };
            plain = {
              repo = "owner/plain";
              secretFile = "/run/secrets/plain";
              domain = "plain.test";
            };
          };
        };
        v = vhosts c;
        cached = name: lib.hasInfix "add_header Cache-Control $muelle_immutable_" v.${name}.extraConfig;
      in
      {
        expr = {
          vhosts = map cached [
            "demo.test"
            "muelle-preview-demo"
            "muelle-apex-demo"
            "pub.test"
            "muelle-apex-pub"
            "plain.test"
          ];
          map = lib.hasInfix ''"~^/a\.b/" "public, max-age=31536000, immutable";'' c.services.nginx.appendHttpConfig;
          maps =
            lib.length (lib.splitString "map $uri $muelle_immutable_" c.services.nginx.appendHttpConfig) - 1;
        };
        expected = {
          # demo's apex only redirects
          vhosts = [
            true
            true
            false
            true
            true
            false
          ];
          map = true;
          maps = 2;
        };
      };
  };
in
{
  failures = lib.runTests tests;
  # for checks.config-parity
  settings = (pkgs.formats.json { }).generate "muelle-config.json" default.services.muelle.settings;
}

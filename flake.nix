{
  description = "muelle: pull-based CI/CD for static sites on a NixOS host";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/release-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      nixosModules.default = import ./nix/modules;

      packages = forAll (pkgs: rec {
        muelle = pkgs.callPackage ./package.nix { };
        default = muelle;
        docs = import ./nix/docs.nix {
          inherit (pkgs) lib;
          inherit pkgs;
          inherit (nixpkgs.lib) nixosSystem;
        };
      });

      checks = forAll (
        pkgs:
        let
          inherit (self.packages.${pkgs.stdenv.hostPlatform.system}) muelle docs;
          eval = import ./tests/eval.nix {
            inherit pkgs;
            inherit (nixpkgs.lib) nixosSystem;
            muelleModule = self.nixosModules.default;
          };
        in
        {
          package = muelle;
          inherit docs;
          # internal links and anchors only: the sandbox has no network
          docs-links =
            pkgs.runCommand "muelle-docs-links"
              {
                nativeBuildInputs = [ pkgs.lychee ];
                SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
              }
              ''
                # 404.html resolves links against site-url (/muelle/), as served on Pages
                lychee --offline --include-fragments --no-progress --exclude-path 404.html --root-dir ${docs} ${docs}
                touch $out
              '';
          # fails at evaluation, so `nix flake check --no-build` runs it too
          eval =
            if eval.failures == [ ] then
              pkgs.runCommand "muelle-eval" { } "touch $out"
            else
              throw "module eval tests failed:\n${nixpkgs.lib.generators.toPretty { } eval.failures}";
          config-parity = import ./tests/config-parity.nix {
            inherit pkgs muelle;
            inherit (eval) settings;
          };
          vm = pkgs.testers.runNixOSTest (
            import ./tests/vm.nix { muelleModule = self.nixosModules.default; }
          );
        }
      );

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.uv
            pkgs.python314
          ];
          env.UV_PYTHON_DOWNLOADS = "never";
        };
      });

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}

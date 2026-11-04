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
      });

      checks = forAll (
        pkgs:
        let
          muelle = self.packages.${pkgs.stdenv.hostPlatform.system}.muelle;
          eval = import ./tests/eval.nix {
            inherit pkgs;
            inherit (nixpkgs.lib) nixosSystem;
            muelleModule = self.nixosModules.default;
          };
        in
        {
          package = muelle;
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

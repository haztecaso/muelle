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
      nixosModules.default = import ./module.nix;

      packages = forAll (pkgs: rec {
        muelle = pkgs.callPackage ./scripts { };
        default = muelle;
      });

      checks = forAll (pkgs: {
        scripts = self.packages.${pkgs.stdenv.hostPlatform.system}.muelle;
        vm = pkgs.testers.runNixOSTest (
          import ./tests/vm.nix { muelleModule = self.nixosModules.default; }
        );
      });

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}

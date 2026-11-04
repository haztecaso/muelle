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
        muelle = pkgs.callPackage ./package.nix { };
        default = muelle;
      });

      checks = forAll (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.muelle;
        vm = pkgs.testers.runNixOSTest (
          import ./tests/vm.nix { muelleModule = self.nixosModules.default; }
        );
      });

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

# The documentation site: docs/ built with mdBook, plus the reference pages
# generated from their sources (NixOS options, action.yml, CLI, docstrings).
{
  lib,
  pkgs,
  nixosSystem,
}:
let
  root = ../.;

  options =
    (nixosSystem {
      modules = [
        # by path, not the flake output, so declarations point at module files
        ./modules
        {
          nixpkgs.hostPlatform = pkgs.stdenv.hostPlatform.system;
          system.stateVersion = "26.05";
        }
      ];
    }).options.services.muelle;

  optionsDoc = pkgs.nixosOptionsDoc {
    inherit options;
    # link declarations to GitHub instead of the store
    transformOptions =
      opt:
      opt
      // {
        declarations = map (
          decl:
          let
            relative = lib.removePrefix "${toString root}/" (toString decl);
            # an imported directory stands for its default.nix
            path = if lib.hasSuffix ".nix" relative then relative else "${relative}/default.nix";
          in
          {
            name = path;
            url = "https://github.com/haztecaso/muelle/blob/main/${path}";
          }
        ) opt.declarations;
      };
  };

  python = pkgs.python314.withPackages (ps: [ ps.pyyaml ]);
in
pkgs.stdenvNoCC.mkDerivation {
  name = "muelle-docs";

  src = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      ../action.yml
      ../docs
      ../src
    ];
  };

  nativeBuildInputs = [
    pkgs.mdbook
    python
  ];

  buildPhase = ''
    runHook preBuild
    python -I docs/gen/reference.py . docs/src
    {
      echo "# NixOS options"
      echo
      echo "Generated from the module in [\`nix/modules\`](https://github.com/haztecaso/muelle/tree/main/nix/modules)."
      echo "Every option is under \`services.muelle\`; site options under \`services.muelle.sites.<name>\`."
      echo
      cat ${optionsDoc.optionsCommonMark}
    } > docs/src/reference/options.md
    mdbook build docs --dest-dir "$out"
    runHook postBuild
  '';

  dontInstall = true;

  passthru = { inherit optionsDoc; };
}

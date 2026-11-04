{
  lib,
  python314Packages,
}:
let
  pyproject = lib.importTOML ./pyproject.toml;
in
python314Packages.buildPythonApplication {
  pname = "muelle";
  inherit (pyproject.project) version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./README.md
      ./src
      ./tests/unit
    ];
  };

  build-system = [ python314Packages.uv-build ];

  nativeCheckInputs = [ python314Packages.pytestCheckHook ];

  pythonImportsCheck = [ "muelle" ];

  meta = {
    description = "muelle deploy client, receiver and worker";
    platforms = lib.platforms.linux;
    mainProgram = "muelle-client";
  };
}

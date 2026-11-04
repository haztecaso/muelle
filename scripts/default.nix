{
  lib,
  symlinkJoin,
  writeShellApplication,
  coreutils,
  curl,
  findutils,
  gnugrep,
  gnutar,
  gzip,
  jq,
  unzip,
  util-linux,
}:
let
  common = ./lib.sh;
  mk =
    name: file: inputs: preamble:
    writeShellApplication {
      inherit name;
      runtimeInputs = inputs;
      text = ''
        ${preamble}
        # shellcheck source=/dev/null
        source ${common}
        ${builtins.readFile file}
      '';
    };
  receive = mk "muelle-receive" ./receive.sh [
    coreutils
    jq
    util-linux
  ] "MUELLE_WORKER=${worker}/bin/muelle-worker";
  worker = mk "muelle-worker" ./worker.sh [
    coreutils
    curl
    findutils
    gnugrep
    gnutar
    gzip
    jq
    unzip
    util-linux
  ] "";
in
symlinkJoin {
  name = "muelle";
  paths = [
    receive
    worker
  ];
  meta = {
    description = "muelle deploy receiver and worker";
    platforms = lib.platforms.linux;
  };
}

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
  openssl,
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
  index = mk "muelle-index" ./index.sh [
    coreutils
    jq
    util-linux
  ] "";
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
  ] "MUELLE_INDEX=${index}/bin/muelle-index";
  # standalone (no lib.sh): runs on CI runners through action.yml
  client = writeShellApplication {
    name = "muelle-client";
    runtimeInputs = [
      coreutils
      curl
      jq
      openssl
    ];
    text = builtins.readFile ./client.sh;
  };
in
symlinkJoin {
  name = "muelle";
  paths = [
    client
    index
    receive
    worker
  ];
  meta = {
    description = "muelle deploy receiver and worker";
    platforms = lib.platforms.linux;
  };
}

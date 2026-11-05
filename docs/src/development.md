# Development

Client, receiver, worker, index and admin CLI are one Python 3.14 package
without runtime dependencies (`src/muelle`), managed with uv:

```sh
nix develop                 # uv + python 3.14 (or any uv install)
uv run pytest               # unit and property tests (tests/unit, tests/property)
uv run ruff check && uv run ruff format --check
uv run ty check
nix build .#muelle          # package, runs the unit tests with nixpkgs' python
nix build .#docs            # this site, into ./result
nix flake check             # also module eval tests, config parity, docs, links and the NixOS VM test
```

The NixOS module is in `nix/modules`: `core/` (site table, config JSON, admin
CLI, systemd units), `nginx/` (the `domain` vhost and what every vhost
shares) and `static/` (prod, preview and redirect vhosts of static sites).

## Documentation

This site is [mdBook](https://rust-lang.github.io/mdBook/), built by
`nix/docs.nix`. Hand-written pages are in `docs/src`; the reference is
generated at build time, so nothing in it is written twice:

- NixOS options: `pkgs.nixosOptionsDoc` over the module.
- Action inputs and outputs: `action.yml`.
- Admin CLI: the argparse parser of `muelle.cli`.
- Python modules: the docstrings and signatures in `src/muelle`, one page
  per module. A new module must be added to `docs/src/SUMMARY.md`, or the
  build fails.

The generators are in `docs/gen/reference.py`. `nix flake check` builds the
site and checks its internal links with lychee (offline: external links are
not checked). Pushes to `main` deploy it to GitHub Pages (`pages.yml`).

## CI and releases

`main` is protected: changes land through pull requests once the two `ci`
jobs pass: `lint` (actionlint, SHA pins, nixfmt, flake evaluation, ruff, ty)
and `check` (pytest, a smoke test of the composite action, and
`nix flake check` with the package, module eval tests, docs and the NixOS VM
test on a KVM-enabled runner).

Every third-party action, including `actions/setup-python` inside the
composite action and the Pages actions, is pinned by commit SHA (`lint`
rejects tag references); Dependabot bumps the SHAs and their version
comments, and `flake-update.yml` opens a weekly `flake.lock` PR that merges
itself when ci passes.

To release, tag a commit on `main` with `vX.Y.Z` and push the tag:
`release.yml` creates the GitHub release and moves the floating major tag;
see [Protocol versioning](explanation/protocol.md).

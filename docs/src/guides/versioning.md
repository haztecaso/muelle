# Versioning and pinning the action

The action is released with semver tags (`v1.2.3`), and a floating major tag
(`v2`) follows the latest release of each major. The major version changes
only on breaking changes to the action inputs or to the payload, see
[Protocol versioning](../explanation/protocol.md).

How to reference it, from most to least locked down:

- `@<commit sha> # v1.2.3`: immutable. Recommended, because the action is
  handed your `MUELLE_SECRET`. Let Dependabot bump the SHA and the comment
  together:

  ```yaml
  # .github/dependabot.yml
  version: 2
  updates:
    - package-ecosystem: github-actions
      directory: /
      schedule:
        interval: weekly
  ```

  Resolve a tag to its commit with
  `git ls-remote https://github.com/haztecaso/muelle 'refs/tags/v1.2.3^{}'`
  (the `^{}` matters: an annotated tag's own SHA does not work).
- `@v1.2.3`: readable, but tags can be moved.
- `@v1`: follows every compatible release automatically. Fine for repos you
  own when you trust the muelle repo as much as the host.

Never use `@main` outside of testing muelle itself.

Pin the flake input on the host the same way (`github:haztecaso/muelle/v1.2.3`
or a locked `flake.lock`), and update host and action together across
majors; see [Upgrade notes](upgrading.md).

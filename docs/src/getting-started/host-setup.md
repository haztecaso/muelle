# Host setup

Add the flake and its module to your NixOS configuration:

```nix
{
  inputs.muelle.url = "github:haztecaso/muelle";
  # in your nixosConfiguration modules: inputs.muelle.nixosModules.default
}
```

Then declare the muelle domain and your sites:

```nix
{ config, ... }:
{
  services.muelle = {
    enable = true;
    domain = "muelle.example.com";     # hooks, status, listing; previews at *.muelle.example.com
    acmeHost = "example.com";          # cert covering muelle.example.com and *.muelle.example.com
    previewVhost = { };                # e.g. { enableAuth = true; } for your own auth gate
    sites.mysite = {
      repo = "octocat/mysite";
      secretFile = config.age.secrets."muelle-mysite".path;
      # muelle declares the prod vhost: HTTPS, root, try_files, per-site logs
      domain = "mysite.example.org";   # prodUrl defaults to https://<domain>
      acmeHost = "mysite.example.org"; # default: domain
      extraConfig = ''
        error_page 404 /404.html;
      '';
      redirectFrom = [ "mysite.example.org" ];
    };
  };

  # extend the prod vhost by name when extraConfig is not enough
  services.nginx.virtualHosts."mysite.example.org".locations."/feed".proxyPass = "http://127.0.0.1:8000";
}
```

Without `domain`, muelle declares no prod vhost: set `prodUrl` and point
your own vhost at `services.muelle.sites.<name>.root`.
`accessLogFormat` picks a `log_format` (defined elsewhere) for the prod
access logs. Every option is listed in the [NixOS options](../reference/options.md)
reference.

`domain` also serves a listing of every next and preview environment, which
reveals branch names: gate it before going further, see
[Gating previews](../guides/previews.md).

## The secret

Generate one secret per site, as hex (it is embedded in the webhook config
template):

```sh
openssl rand -hex 32
```

Store it on the host (e.g. with agenix, as `secretFile`) and as a repository
secret (`MUELLE_SECRET`) on GitHub. A secret only works on its own site's hook.

Next: [Your first workflow](first-workflow.md).

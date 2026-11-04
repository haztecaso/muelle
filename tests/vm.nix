{ muelleModule }:
{
  name = "muelle";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [ muelleModule ];

      # ACME never succeeds in the VM; nginx runs on the self-signed fallback certs.
      security.acme = {
        acceptTerms = true;
        defaults.email = "test@example.test";
        defaults.server = "https://127.0.0.1/unreachable";
        defaults.dnsProvider = "cloudflare";
        certs."muelle.test".extraDomainNames = [
          "*.muelle.test"
        ];
        certs."demo.test".extraDomainNames = [ "*.demo.test" ];
      };
      users.users.nginx.extraGroups = [ "acme" ];
      # muelle-client (python) resolves and verifies TLS like on a runner
      networking.hosts."127.0.0.1" = [ "muelle.test" ];

      environment.etc."muelle-demo".text = "deadbeef";
      environment.etc."muelle-prodonly".text = "cafebabe";
      environment.systemPackages = with pkgs; [
        (callPackage ../package.nix { })
        jq
        openssl
        zip
        curl
      ];

      services.nginx = {
        enable = true;
        # like a real host: unknown names must not fall through to the first vhost
        virtualHosts."_" = {
          default = true;
          rejectSSL = true;
          locations."/".return = "444";
        };
        # fake GitHub artifact API
        virtualHosts."artifacts" = {
          listen = [
            {
              addr = "127.0.0.1";
              port = 8080;
            }
          ];
          root = "/srv/artifacts";
        };
        commonHttpConfig = "log_format withHost '$host $request';";
        # extends the prod vhost declared by muelle
        virtualHosts."demo.test".locations."/extra".return = "200 extra";
      };

      services.muelle = {
        enable = true;
        domain = "muelle.test";
        acmeHost = "muelle.test";
        # stand-in for an auth gate on the listing: /hooks/ and /status/ must stay open
        indexVhost.locations."/".extraConfig = ''
          if ($http_x_gate != "open") {
            return 401;
          }
        '';
        artifactUrlBase = "http://127.0.0.1:8080/repos";
        hookRateLimit = "600r/m";
        accessLogFormat = "withHost";
        sites.demo = {
          repo = "owner/demo";
          secretFile = "/etc/muelle-demo";
          domain = "demo.test";
          extraConfig = "add_header X-Demo yes always;";
          redirectFrom = [ "demo.test" ];
        };
        sites.prodonly = {
          repo = "owner/prodonly";
          secretFile = "/etc/muelle-prodonly";
          prodUrl = "https://prodonly.test";
          envs = [ "prod" ];
        };
      };
    };

  testScript = ''
    import json, time

    machine.wait_for_unit("nginx.service")
    machine.wait_for_unit("webhook.service")
    machine.wait_for_open_port(443)
    machine.wait_for_open_port(9077)

    RESOLVE = " ".join(f"--resolve {h}:443:127.0.0.1" for h in
      ["muelle.test", "demo.test", "next.demo.test", "next-demo.muelle.test", "prod-demo.muelle.test"])
    counter = [0]

    def make_artifact(name, content, kind="zip"):
        machine.succeed(f"rm -rf /tmp/build && mkdir -p /tmp/build/dist && echo '{content}' > /tmp/build/dist/index.html")
        machine.succeed("mkdir -p /srv/artifacts/repos/owner/demo /srv/artifacts/repos/owner/prodonly")
        path = f"/srv/artifacts/repos/owner/demo/{name}"
        if kind == "zip":
            machine.succeed(f"cd /tmp/build && zip -qr {path} dist")
        else:
            machine.succeed(f"tar -czf {path} -C /tmp/build dist")
        return path, machine.succeed(f"sha256sum {path} | cut -d' ' -f1").strip()

    def post(site, payload, secret, expect_code=200):
        counter[0] += 1
        payload.setdefault("id", f"test-{counter[0]:04d}-{int(time.time())}")
        payload.setdefault("ts", int(machine.succeed("date +%s").strip()))
        payload.setdefault("site", site)
        payload.setdefault("branch", "main")
        body = json.dumps(payload)
        machine.succeed(f"cat > /tmp/body.json <<'EOF'\n{body}\nEOF")
        machine.succeed("truncate -s -1 /tmp/body.json")
        sig = machine.succeed(f"openssl dgst -sha256 -hmac {secret} -r /tmp/body.json | cut -d' ' -f1").strip()
        out = machine.succeed(
          f"curl -sk {RESOLVE} -o /tmp/resp -w '%{{http_code}}' "
          f"-H 'Content-Type: application/json' -H 'X-Muelle-Signature: sha256={sig}' "
          f"--data-binary @/tmp/body.json https://muelle.test/hooks/{site}"
        )
        resp = machine.succeed("cat /tmp/resp")
        assert out == str(expect_code), f"expected {expect_code}, got {out}: {resp}"
        return payload["id"], resp

    def wait_status(id, want="success"):
        for _ in range(60):
            s = json.loads(machine.succeed(f"curl -sk {RESOLVE} https://muelle.test/status/{id}"))
            if s["state"] in ("success", "failure"):
                assert s["state"] == want, f"status: {s}"
                return s
            time.sleep(1)
        raise Exception(f"timeout waiting for {id}")

    def deploy(site, env, sha, name, branch="main", secret="deadbeef", **kw):
        path, digest = make_artifact(name, f"{env}-{sha[:7]}", **kw)
        return post(site, {
            "action": "deploy", "env": env, "sha": sha, "branch": branch,
            "artifact_url": f"http://127.0.0.1:8080/repos/owner/{site}/{name}",
            "sha256": digest, "token": "ghs_dummy",
        }, secret)

    SHA1 = "1" * 40
    SHA2 = "2" * 40

    with subtest("prod deploy"):
        id, _ = deploy("demo", "prod", SHA1, "a.zip")
        s = wait_status(id)
        assert s["url"] == "https://demo.test", s
        machine.succeed(f"curl -sk {RESOLVE} https://demo.test/ | grep prod-1111111")

    with subtest("prod vhost declared by muelle"):
        machine.succeed(f"curl -skI {RESOLVE} https://demo.test/ | grep -i '^x-demo: yes'")
        machine.succeed(f"curl -skf {RESOLVE} https://demo.test/index | grep prod-1111111")
        machine.succeed(f"curl -skf {RESOLVE} https://demo.test/extra | grep extra")
        machine.succeed("grep -q '^demo.test GET / ' /var/log/nginx/demo-access.log")
        loc = machine.succeed(f"curl -s {RESOLVE} --resolve demo.test:80:127.0.0.1 -o /dev/null -w '%{{redirect_url}}' http://demo.test/")
        assert loc == "https://demo.test/", loc

    with subtest("prod redeploy with tarball flips current"):
        id, _ = deploy("demo", "prod", SHA2, "b.tar.gz", kind="tar")
        wait_status(id)
        machine.succeed(f"curl -sk {RESOLVE} https://demo.test/ | grep prod-2222222")
        machine.succeed(f"test -d /var/www/muelle/demo/prod/releases/{SHA1}")

    with subtest("next deploy and apex redirect"):
        id, _ = deploy("demo", "next", SHA1, "c.zip")
        wait_status(id)
        machine.succeed(f"curl -sk {RESOLVE} https://next-demo.muelle.test/ | grep next-1111111")
        loc = machine.succeed(f"curl -sk {RESOLVE} -o /dev/null -w '%{{redirect_url}}' https://next.demo.test/x")
        assert loc == "https://next-demo.muelle.test/x", loc
        # prod is never served under the muelle domain
        machine.fail(f"curl -skf {RESOLVE} https://prod-demo.muelle.test/")
        idx = json.loads(machine.succeed(f"curl -skf {RESOLVE} -H 'X-Gate: open' https://muelle.test/index.json"))
        assert [(e["site"], e["env"], e["url"]) for e in idx["entries"]] == [("demo", "next", "https://next-demo.muelle.test")], idx
        machine.succeed(f"curl -skf {RESOLVE} -H 'X-Gate: open' https://muelle.test/ | grep -F 'href=\"https://next-demo.muelle.test\"'")
        # the gate on the listing leaves /hooks/ and /status/ (used above) alone
        code = machine.succeed(f"curl -sk {RESOLVE} -o /dev/null -w '%{{http_code}}' https://muelle.test/index.json")
        assert code == "401", code

    with subtest("security headers"):
        def headers(url, extra=""):
            return machine.succeed(f"curl -skI {RESOLVE} {extra} {url}").lower()
        for url in ["https://demo.test/", "https://next-demo.muelle.test/", "https://next.demo.test/",
                    "https://muelle.test/", f"https://muelle.test/status/{id}"]:
            h = headers(url, "-H 'X-Gate: open'")
            assert "strict-transport-security: max-age=31536000\r\n" in h, (url, h)
            assert "x-content-type-options: nosniff" in h, (url, h)
        for url in ["https://muelle.test/", f"https://muelle.test/status/{id}"]:
            h = headers(url, "-H 'X-Gate: open'")
            assert "content-security-policy: default-src 'none'" in h, (url, h)
            assert "cache-control: no-store" in h, (url, h)
        assert "content-security-policy" not in headers("https://demo.test/")

    with subtest("preview deploy and teardown"):
        id, resp = deploy("demo", "preview", SHA2, "d.zip", branch="feat/x")
        url = json.loads(resp)["url"]
        host = url.removeprefix("https://")
        wait_status(id)
        machine.succeed(f"curl -sk --resolve {host}:443:127.0.0.1 {url}/ | grep preview-2222222")
        page = machine.succeed(f"curl -skf {RESOLVE} -H 'X-Gate: open' https://muelle.test/")
        assert url in page and "feat/x" in page, page
        id, _ = post("demo", {"action": "teardown", "env": "preview", "branch": "feat/x"}, "deadbeef")
        wait_status(id)
        machine.succeed(f"curl -sk --resolve {host}:443:127.0.0.1 -o /dev/null -w '%{{http_code}}' {url}/ | grep 404")
        page = machine.succeed(f"curl -skf {RESOLVE} -H 'X-Gate: open' https://muelle.test/")
        assert url not in page and "next-demo.muelle.test" in page, page

    # webhook answers an HMAC mismatch with 500 "Error occurred while evaluating hook rules."
    with subtest("bad signature is rejected"):
        _, r = post("demo", {"action": "deploy", "env": "prod"}, "wrong", expect_code=500)
        assert "hook rules" in r, r

    with subtest("secret of another site is rejected"):
        _, r = post("demo", {"action": "deploy", "env": "prod"}, "cafebabe", expect_code=500)
        assert "hook rules" in r, r

    with subtest("validation errors"):
        _, r = post("prodonly", {"action": "deploy", "env": "preview", "sha": SHA1, "sha256": "0" * 64,
                                  "artifact_url": "http://127.0.0.1:8080/repos/owner/prodonly/x"}, "cafebabe", expect_code=500)
        assert "not allowed" in r, r
        _, r = post("demo", {"action": "deploy", "env": "prod", "sha": SHA1, "sha256": "0" * 64,
                              "artifact_url": "http://127.0.0.1:8080/repos/owner/other/x"}, "deadbeef", expect_code=500)
        assert "artifact_url not allowed" in r, r
        _, r = post("demo", {"action": "deploy", "env": "prod", "ts": 1000000000}, "deadbeef", expect_code=500)
        assert "stale" in r, r
        _, r = post("demo", {"site": "prodonly", "action": "deploy", "env": "prod"}, "deadbeef", expect_code=500)
        assert "site does not match" in r, r
        _, r = post("demo", {"action": "deploy", "env": "prod", "branch": "feat/x", "sha": SHA1, "sha256": "0" * 64,
                              "artifact_url": "http://127.0.0.1:8080/repos/owner/demo/x"}, "deadbeef", expect_code=500)
        assert "may not deploy to prod" in r, r

    with subtest("unknown protocol is rejected"):
        _, r = post("demo", {"protocol": 2, "action": "deploy", "env": "prod"}, "deadbeef", expect_code=500)
        assert "unsupported protocol" in r, r

    with subtest("replayed id is rejected"):
        id, _ = deploy("demo", "next", SHA2, "e.zip")
        wait_status(id)
        _, r = post("demo", {"id": id, "action": "deploy", "env": "next", "sha": SHA2, "sha256": "0" * 64,
                              "artifact_url": "http://127.0.0.1:8080/repos/owner/demo/e.zip"}, "deadbeef", expect_code=500)
        assert "already used" in r, r

    with subtest("sha256 mismatch fails the deploy"):
        machine.succeed("echo junk > /srv/artifacts/repos/owner/demo/junk.zip")
        id, _ = post("demo", {"action": "deploy", "env": "next", "sha": SHA1, "sha256": "0" * 64,
                               "artifact_url": "http://127.0.0.1:8080/repos/owner/demo/junk.zip"}, "deadbeef")
        s = wait_status(id, "failure")
        assert "sha256 mismatch" in s["message"], s

    with subtest("symlinks in artifact are rejected"):
        machine.succeed("rm -rf /tmp/evil && mkdir /tmp/evil && ln -s /etc/passwd /tmp/evil/index.html")
        machine.succeed("tar -czf /srv/artifacts/repos/owner/demo/evil.tgz -C /tmp/evil .")
        digest = machine.succeed("sha256sum /srv/artifacts/repos/owner/demo/evil.tgz | cut -d' ' -f1").strip()
        id, _ = post("demo", {"action": "deploy", "env": "next", "sha": SHA1, "sha256": digest,
                               "artifact_url": "http://127.0.0.1:8080/repos/owner/demo/evil.tgz"}, "deadbeef")
        s = wait_status(id, "failure")
        assert "non-regular" in s["message"], s
        machine.succeed(f"curl -sk {RESOLVE} https://next-demo.muelle.test/ | grep next-2222222")

    def client(extra, check=True):
        env = {
            "MUELLE_ENDPOINT": "https://muelle.test", "MUELLE_SITE": "demo",
            "MUELLE_SECRET": "deadbeef", "MUELLE_DEPLOYMENTS": "false",
            "SSL_CERT_FILE": "/var/lib/acme/.minica/cert.pem",
            "MUELLE_POLL_INTERVAL": "1", "MUELLE_TIMEOUT": "60",
            "GITHUB_OUTPUT": "/tmp/gh-output", "GITHUB_RUN_ID": "99",
        } | extra
        cmd = "rm -f /tmp/gh-output; " + " ".join(f"{k}='{v}'" for k, v in env.items()) + " muelle-client"
        return machine.succeed(cmd) if check else machine.fail(cmd)

    with subtest("client deploys a preview and tears it down"):
        path, digest = make_artifact("client.zip", "client-preview")
        out = client({"MUELLE_ENV": "preview", "MUELLE_BRANCH": "feat/client", "MUELLE_SHA": "3" * 40,
                      "MUELLE_ARTIFACT_URL": "http://127.0.0.1:8080/repos/owner/demo/client.zip",
                      "MUELLE_SHA256": f"sha256:{digest}", "MUELLE_TOKEN": "ghs_dummy"})
        assert "muelle: success" in out, out
        url = machine.succeed("sed -n 's/^url=//p' /tmp/gh-output").strip()
        assert url.endswith("-demo.muelle.test"), url
        host = url.removeprefix("https://")
        machine.succeed(f"curl -sk --resolve {host}:443:127.0.0.1 {url}/ | grep client-preview")
        client({"MUELLE_ACTION": "teardown", "MUELLE_BRANCH": "feat/client"})
        machine.succeed(f"curl -sk --resolve {host}:443:127.0.0.1 -o /dev/null -w '%{{http_code}}' {url}/ | grep 404")

    with subtest("client fails on rejection and on failed deploys"):
        client({"MUELLE_SECRET": "wrong", "MUELLE_ACTION": "teardown", "MUELLE_BRANCH": "x"}, check=False)
        client({"MUELLE_ENV": "next", "MUELLE_BRANCH": "main", "MUELLE_SHA": "4" * 40,
                "MUELLE_ARTIFACT_URL": "http://127.0.0.1:8080/repos/owner/demo/junk.zip",
                "MUELLE_SHA256": "0" * 64}, check=False)

    with subtest("service restarts cleanly and keeps serving"):
        machine.succeed("systemctl restart webhook.service")
        machine.wait_for_unit("webhook.service")
        machine.wait_for_open_port(9077)
        assert machine.succeed("pgrep -c -x webhook").strip() == "1"
        machine.succeed("! journalctl -u webhook | grep -q ghs_dummy")
        id, _ = deploy("demo", "next", SHA1, "after-restart.zip")
        wait_status(id)
        machine.succeed(f"curl -sk {RESOLVE} https://next-demo.muelle.test/ | grep next-1111111")

    with subtest("expired previews are pruned"):
        id, _ = deploy("demo", "preview", "3" * 40, "expiring.zip", branch="stale")
        slot = wait_status(id)["url"].removeprefix("https://").split("-")[0]
        meta = f"/var/www/muelle/demo/{slot}/meta.json"
        machine.succeed("systemctl list-timers | grep muelle-prune")
        machine.succeed("systemctl start muelle-prune.service")
        machine.succeed(f"test -f {meta}")
        machine.succeed(f"sed -i 's/\"deployed_at\": \"[^\"]*\"/\"deployed_at\": \"2000-01-01T00:00:00Z\"/' {meta}")
        machine.succeed("systemctl start muelle-prune.service")
        machine.fail(f"test -e /var/www/muelle/demo/{slot}")
        page = machine.succeed(f"curl -skf {RESOLVE} -H 'X-Gate: open' https://muelle.test/index.json")
        assert "stale" not in page, page

    with subtest("dotdot paths are rejected"):
        machine.succeed("cd /tmp && rm -rf dd && mkdir -p dd/a && echo x > dd/x && cd dd/a && tar -czPf /srv/artifacts/repos/owner/demo/dd.tgz ../x")
        digest = machine.succeed("sha256sum /srv/artifacts/repos/owner/demo/dd.tgz | cut -d' ' -f1").strip()
        id, _ = post("demo", {"action": "deploy", "env": "next", "sha": SHA1, "sha256": digest,
                               "artifact_url": "http://127.0.0.1:8080/repos/owner/demo/dd.tgz"}, "deadbeef")
        s = wait_status(id, "failure")
        assert "'..'" in s["message"], s

    with subtest("admin CLI"):
        out = machine.succeed("muelle status demo")
        assert "next" in out and "https://next-demo.muelle.test" in out, out
        machine.succeed("muelle releases demo prod | grep -E '^[*][[:space:]]+2{40}[[:space:]]'")
        id, resp = deploy("demo", "preview", SHA1, "cli.zip", branch="feat/cli")
        wait_status(id)
        url = json.loads(resp)["url"]
        host = url.removeprefix("https://")
        machine.succeed("muelle teardown demo feat/cli | grep 'removed 1 preview'")
        machine.succeed(f"curl -sk --resolve {host}:443:127.0.0.1 -o /dev/null -w '%{{http_code}}' {url}/ | grep 404")
        job = machine.succeed("muelle jobs -n 1").split()
        assert job[1] == "success" and job[2].startswith("host-"), job
        assert machine.succeed(f"stat -c %U /var/lib/muelle/status/{job[2]}.json").strip() == "muelle"
        machine.succeed(f"muelle log {job[2]} | grep 'teardown of feat/cli (host)'")
        machine.fail("muelle rm demo prod")
        machine.succeed("muelle prune && muelle index")
        machine.succeed("test -d /var/www/muelle/demo/prod/releases/" + SHA2)
  '';
}

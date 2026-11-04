import json
from typing import Any

import pytest

from muelle import protocol, receive
from muelle.config import Config
from muelle.receive import Rejected, field, validate

NOW = 1_800_000_000
SHA = "1" * 40


def deploy(**overrides: Any) -> dict[str, Any]:
    payload = protocol.deploy_payload(
        id="test-0001",
        site="demo",
        env="next",
        branch="main",
        sha=SHA,
        sha256="0" * 64,
        artifact_url="https://api.github.com/repos/owner/demo/actions/artifacts/1/zip",
        token="ghs_dummy",
        ts=NOW,
    )
    payload.update(overrides)
    return payload


def rejected(payload: object, config: Config, site: str = "demo") -> str:
    with pytest.raises(Rejected) as e:
        validate(payload, site, config, NOW)
    return str(e.value)


def test_field_reads_like_jq_tostring() -> None:
    assert field({"a": "x"}, "a") == "x"
    assert field({"a": 12}, "a") == "12"
    assert field({"a": None}, "a") == ""
    assert field({"a": False}, "a") == ""
    assert field({}, "a") == ""


def test_valid_deploy(config: Config) -> None:
    request = validate(deploy(), "demo", config, NOW)
    assert request.action == "deploy"
    assert request.url == "https://next-demo.muelle.test"


def test_prod_deploy_reports_prod_url(config: Config) -> None:
    assert validate(deploy(env="prod"), "demo", config, NOW).url == "https://demo.test"


def test_preview_url_uses_slot(config: Config) -> None:
    request = validate(deploy(env="preview", branch="feat/x"), "demo", config, NOW)
    slot = protocol.slot_for("demo", "preview", "feat/x", SHA)
    assert request.url == f"https://{slot}-demo.muelle.test"


def test_missing_protocol_means_1(config: Config) -> None:
    payload = deploy()
    del payload["protocol"]
    validate(payload, "demo", config, NOW)


def test_valid_teardown(config: Config) -> None:
    payload = protocol.teardown_payload(id="test-0002", site="demo", branch="feat/x", ts=NOW)
    request = validate(payload, "demo", config, NOW)
    assert request.action == "teardown"
    assert request.url == ""


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"protocol": 2}, "unsupported protocol '2'"),
        ({"id": "short"}, "invalid id"),
        ({"id": "has space ok"}, "invalid id"),
        ({"site": "prodonly"}, "site does not match hook"),
        ({"ts": "soon"}, "invalid ts"),
        ({"ts": NOW - 301}, "stale or future request"),
        ({"ts": NOW + 301}, "stale or future request"),
        ({"branch": ""}, "invalid branch"),
        ({"branch": "a..b"}, "invalid branch"),
        ({"branch": "x y"}, "invalid branch"),
        ({"branch": "main\n"}, "invalid branch"),
        ({"env": "staging"}, "invalid env"),
        ({"sha": "abc"}, "invalid sha"),
        ({"sha256": "0" * 63}, "invalid sha256"),
        ({"artifact_url": "https://api.github.com/repos/owner/other/x"}, "artifact_url not allowed"),
        ({"artifact_url": "https://api.github.com/repos/owner/demo/a b"}, "invalid artifact_url"),
        ({"artifact_url": "https://api.github.com/repos/owner/demo/../other/x"}, "invalid artifact_url"),
        ({"action": "explode"}, "invalid action"),
        ({"action": "teardown", "env": "next"}, "teardown only applies to previews"),
    ],
)
def test_rejections(config: Config, overrides: dict[str, Any], message: str) -> None:
    assert message in rejected(deploy(**overrides), config)


def test_env_not_allowed_for_site(config: Config) -> None:
    payload = deploy(
        site="prodonly",
        env="preview",
        artifact_url="https://api.github.com/repos/owner/prodonly/x",
    )
    assert rejected(payload, config, site="prodonly") == "env 'preview' not allowed for site 'prodonly'"


def test_unknown_site(config: Config) -> None:
    assert rejected(deploy(site="ghost"), config, site="ghost") == "unknown site"


def test_not_an_object(config: Config) -> None:
    assert rejected([1, 2], config) == "payload is not a JSON object"


def test_handle_spools_and_queues(config: Config) -> None:
    spawned: list[str] = []
    raw = json.dumps(deploy())
    response = receive.handle(raw, "demo", config, NOW, spawn=lambda _c, id: spawned.append(id))
    assert response == {
        "id": "test-0001",
        "state": "queued",
        "url": "https://next-demo.muelle.test",
        "status_url": "https://muelle.test/status/test-0001",
    }
    assert spawned == ["test-0001"]
    spool = config.state_dir / "spool" / "test-0001.json"
    assert spool.read_text() == raw
    assert spool.stat().st_mode & 0o777 == 0o600
    status = json.loads((config.state_dir / "status" / "test-0001.json").read_text())
    assert status["state"] == "queued"


def test_handle_rejects_replayed_id(config: Config) -> None:
    raw = json.dumps(deploy())
    receive.handle(raw, "demo", config, NOW, spawn=lambda _c, _id: None)
    with pytest.raises(Rejected, match="id already used"):
        receive.handle(raw, "demo", config, NOW, spawn=lambda _c, _id: None)
    # also after the worker consumed the spool file
    (config.state_dir / "spool" / "test-0001.json").unlink()
    with pytest.raises(Rejected, match="id already used"):
        receive.handle(raw, "demo", config, NOW, spawn=lambda _c, _id: None)


def test_handle_rejects_invalid_json(config: Config) -> None:
    with pytest.raises(Rejected, match="not a JSON object"):
        receive.handle("{", "demo", config, NOW)


def test_main_reports_errors_on_stderr(
    config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "domain": config.domain,
                "baseDir": str(config.base_dir),
                "stateDir": str(config.state_dir),
                "maxSkew": 300,
                "keepReleases": 2,
                "keepPreviews": 2,
                "maxArtifactBytes": 1,
                "sites": {},
            }
        )
    )
    monkeypatch.setenv("MUELLE_CONFIG", str(config_file))
    monkeypatch.setenv("MUELLE_PAYLOAD", json.dumps(deploy()))
    assert receive.main(["demo"]) == 1
    assert "muelle: unknown site" in capsys.readouterr().err

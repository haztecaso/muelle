import hashlib
import hmac

from muelle import protocol


def test_slot_for_prod_and_next_are_fixed() -> None:
    assert protocol.slot_for("demo", "prod", "main", "1" * 40) == "prod"
    assert protocol.slot_for("demo", "next", "main", "1" * 40) == "next"


def test_slot_for_preview_matches_former_shell_hash() -> None:
    # printf '%s\n%s\n%s' demo feat/x 2222...2 | sha256sum | cut -c1-8
    expected = hashlib.sha256(b"demo\nfeat/x\n" + b"2" * 40).hexdigest()[:8]
    assert protocol.slot_for("demo", "preview", "feat/x", "2" * 40) == expected
    assert len(expected) == 8


def test_slot_for_preview_changes_per_commit() -> None:
    a = protocol.slot_for("demo", "preview", "feat/x", "1" * 40)
    b = protocol.slot_for("demo", "preview", "feat/x", "2" * 40)
    assert a != b


def test_sign_is_hmac_sha256_of_body() -> None:
    body = protocol.encode({"a": 1})
    assert body == b'{"a":1}'
    expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert protocol.sign(body, "secret") == f"sha256={expected}"


def test_payloads_carry_protocol_version() -> None:
    teardown = protocol.teardown_payload(id="x" * 8, site="demo", branch="b", ts=1)
    assert teardown["protocol"] == protocol.PROTOCOL
    assert teardown["env"] == "preview"
    assert teardown["action"] == "teardown"

"""Properties of the wire format: encoding, signatures and slot names."""

import hashlib
import hmac
import json
from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st

from muelle import protocol

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=20,
)


@given(payload=st.dictionaries(st.text(), json_values))
def test_encode_round_trips(payload: dict[str, Any]) -> None:
    assert json.loads(protocol.encode(payload)) == payload


@given(body=st.binary(), secret=st.text(min_size=1), other=st.text(min_size=1))
def test_sign_verifies_only_with_its_secret(body: bytes, secret: str, other: str) -> None:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(protocol.sign(body, secret), expected)
    assume(other != secret)
    assert protocol.sign(body, other) != expected


@given(site=st.text(), env=st.sampled_from(protocol.ENVS), branch=st.text(), sha=st.text())
def test_slot_for_is_a_fixed_name_or_a_preview_slot(site: str, env: str, branch: str, sha: str) -> None:
    slot = protocol.slot_for(site, env, branch, sha)
    if env == "preview":
        assert protocol.is_preview_slot(slot)
    else:
        assert slot == env
        assert not protocol.is_preview_slot(slot)

"""Properties of payload validation: whatever arrives, only Rejected escapes, and
what is accepted is safe to use."""

import contextlib
from typing import Any

import pytest
from helpers import NOW, SHA, deploy
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from muelle import protocol, receive
from muelle.config import Config
from muelle.receive import Rejected, validate

# validate() never writes: sharing the config fixture across examples is safe
uses_config = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(), children, max_size=3),
    max_leaves=10,
)
# values that get past the earlier checks, so later ones are reached too
near_valid = (
    st.sampled_from(["deploy", "teardown", "announce", "abort", "prod", "next", "preview", "demo", SHA, "0" * 64])
    | st.integers(NOW - 1000, NOW + 1000)
    | st.from_regex(r"[A-Za-z0-9._/-]{1,20}", fullmatch=True)
)
fields = st.sampled_from(
    ["protocol", "id", "site", "action", "env", "branch", "sha", "sha256", "artifact_url", "run_url", "ts", "token"]
)
# half the time only branch and URL characters, so ".." mixed with them is frequent
url_text = st.text(st.sampled_from("./a1%?=&:-_"), max_size=40) | st.text(max_size=250)


@uses_config
@given(payload=json_values)
def test_validate_only_rejects_any_json(config: Config, payload: object) -> None:
    with contextlib.suppress(Rejected):
        validate(payload, "demo", config, NOW)


@uses_config
@given(changes=st.dictionaries(fields, json_values | near_valid, min_size=1, max_size=3))
def test_validate_only_rejects_mutated_deploys(config: Config, changes: dict[str, Any]) -> None:
    with contextlib.suppress(Rejected):
        validate(deploy(**changes), "demo", config, NOW)


@uses_config
@given(branch=url_text)
def test_accepted_branches_are_safe(config: Config, branch: str) -> None:
    try:
        validate(deploy(env="preview", branch=branch), "demo", config, NOW)
    except Rejected:
        return
    assert receive._BRANCH.fullmatch(branch)
    assert ".." not in branch


@uses_config
@given(branch=st.from_regex(r"[A-Za-z0-9._/-]{1,200}", fullmatch=True).filter(lambda b: ".." not in b))
def test_any_valid_branch_deploys_to_its_preview(config: Config, branch: str) -> None:
    slot = protocol.slot_for("demo", "preview", branch, SHA)
    assert validate(deploy(env="preview", branch=branch), "demo", config, NOW) == f"https://{slot}-demo.muelle.test"


@uses_config
@given(skew=st.integers(-1000, 1000))
def test_ts_is_accepted_within_max_skew(config: Config, skew: int) -> None:
    payload = deploy(ts=NOW + skew)
    if abs(skew) <= config.max_skew:
        validate(payload, "demo", config, NOW)
    else:
        with pytest.raises(Rejected, match="stale or future request"):
            validate(payload, "demo", config, NOW)


@uses_config
@given(suffix=url_text)
def test_accepted_artifact_urls_stay_under_the_prefix(config: Config, suffix: str) -> None:
    prefix = config.sites["demo"].artifact_url_prefix
    try:
        validate(deploy(artifact_url=prefix + suffix), "demo", config, NOW)
    except Rejected:
        return
    assert ".." not in suffix
    assert receive._ARTIFACT_URL.fullmatch(prefix + suffix)

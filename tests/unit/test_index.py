import json
from pathlib import Path

from muelle import index
from muelle.config import Config


def deployed(config: Config, site: str, slot: str, env: str, branch: str, at: str, current: bool = True) -> None:
    slot_dir = config.base_dir / site / slot
    (slot_dir / "releases" / "x").mkdir(parents=True)
    if current:
        (slot_dir / "current").symlink_to("releases/x")
    meta = {"site": site, "env": env, "branch": branch, "sha": "a" * 40, "deployed_at": at}
    (slot_dir / "meta.json").write_text(json.dumps(meta))


def test_collect_orders_next_first_then_newest(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "old", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "bbbbbbbb", "preview", "new", "2026-02-01T00:00:00Z")
    deployed(config, "demo", "next", "next", "main", "2025-01-01T00:00:00Z")
    entries = index.collect(config)
    assert [e["branch"] for e in entries] == ["main", "new", "old"]
    assert entries[0]["url"] == "https://next-demo.muelle.test"
    assert entries[1]["url"] == "https://bbbbbbbb-demo.muelle.test"
    assert entries[0]["repo"] == "owner/demo"
    assert entries[0]["prod"] == "https://demo.test"


def test_collect_skips_prod_and_incomplete_slots(config: Config) -> None:
    deployed(config, "demo", "prod", "prod", "main", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "cccccccc", "preview", "nocurrent", "2026-01-01T00:00:00Z", current=False)
    deployed(config, "demo", "notaslot", "preview", "weird", "2026-01-01T00:00:00Z")
    (config.base_dir / "demo" / ".tmp.abc").mkdir()
    assert index.collect(config) == []


def test_render_escapes_and_links(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "feat/<x>", "2026-01-01T00:00:00Z")
    html = index.render(index.collect(config), "2026-01-02T00:00:00Z")
    assert "feat/&lt;x&gt;" in html
    assert "<x>" not in html
    assert 'href="https://aaaaaaaa-demo.muelle.test"' in html
    assert 'href="https://github.com/owner/demo/commit/' + "a" * 40 + '"' in html
    assert "<code>aaaaaaa</code>" in html
    assert "1 deployment(s)" in html


def test_render_empty() -> None:
    assert "Nothing deployed to next or preview." in index.render([], "now")


def test_write_creates_both_files(config: Config) -> None:
    deployed(config, "demo", "next", "next", "main", "2026-01-01T00:00:00Z")
    index.write(config)
    out: Path = config.state_dir / "index"
    data = json.loads((out / "index.json").read_text())
    assert [e["env"] for e in data["entries"]] == ["next"]
    assert list(data["entries"][0]) == ["site", "env", "branch", "sha", "deployed_at", "slot", "url", "repo", "prod"]
    assert (out / "index.html").stat().st_mode & 0o777 == 0o644


def test_html_escapes_everything_but_markup() -> None:
    raw = "<b>"
    trusted = index.Markup("<i>")
    rows = [index.Markup("<tr>"), index.Markup("</tr>")]
    assert index.html(t"{raw}{trusted}{rows}") == "&lt;b&gt;<i><tr></tr>"
    assert index.html(t"{['<s>']}") == "[&#x27;&lt;s&gt;&#x27;]"

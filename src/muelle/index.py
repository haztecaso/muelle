"""muelle-index

Regenerates the listing of deployed next/preview environments served at the
muelle domain: <stateDir>/index/{index.json,index.html}.
"""

import json
from html import escape
from itertools import groupby
from pathlib import Path
from string.templatelib import Interpolation, Template
from typing import Any, TypedDict

from . import protocol
from .config import Config
from .state import utc_now, write_atomic, write_json_atomic


class Entry(TypedDict):
    site: str
    env: str
    branch: str
    sha: str
    deployed_at: str
    slot: str
    url: str
    repo: str
    prod: str


class Workflow(TypedDict):
    id: str
    site: str
    env: str
    branch: str
    sha: str
    repo: str
    url: str
    workflow_url: str
    state: str
    message: str
    updated: str


def read_meta(slot_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((slot_dir / "meta.json").read_text())
    except OSError, ValueError:
        return {}


def collect(config: Config) -> list[Entry]:
    """Deployed next/preview slots: grouped by site, next first, then newest first."""
    entries: list[Entry] = []
    for site_name, site in config.sites.items():
        site_dir = config.base_dir / site_name
        if not site_dir.is_dir():
            continue
        for slot_dir in site_dir.iterdir():
            slot = slot_dir.name
            if not (slot == "next" or protocol.is_preview_slot(slot)) or not (slot_dir / "current").is_symlink():
                continue
            if not (meta := read_meta(slot_dir)):
                continue
            entries.append(
                Entry(
                    site=meta["site"],
                    env=meta["env"],
                    branch=meta["branch"],
                    sha=meta["sha"],
                    deployed_at=meta["deployed_at"],
                    slot=slot,
                    url=config.url_for(site_name, slot),
                    repo=site.repo,
                    prod=site.prod_url,
                )
            )
    entries.sort(key=lambda e: e["deployed_at"], reverse=True)
    entries.sort(key=lambda e: (e["site"], 0 if e["env"] == "next" else 1))
    return entries


def collect_workflows(config: Config) -> list[Workflow]:
    """Queued/running/failed deploy workflows, newest first."""
    workflows: list[Workflow] = []
    for status in (config.state_dir / "status").glob("*.json"):
        meta = read_meta(status)
        if (
            meta.get("action") != "deploy"
            or meta.get("state") not in ("queued", "in_progress", "failure")
            or not all(meta.get(k) for k in ("id", "site", "env", "branch", "sha", "repo", "updated"))
        ):
            continue
        workflows.append(
            Workflow(
                id=str(meta["id"]),
                site=str(meta["site"]),
                env=str(meta["env"]),
                branch=str(meta["branch"]),
                sha=str(meta["sha"]),
                repo=str(meta["repo"]),
                url=str(meta.get("url", "")),
                workflow_url=str(meta.get("workflow_url", "")),
                state=str(meta["state"]),
                message=str(meta.get("message", "")),
                updated=str(meta["updated"]),
            )
        )
    workflows.sort(key=lambda e: e["updated"], reverse=True)
    return workflows


STYLE = """\
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --accent: #0a66c2; --tag: #f2f2f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111113; --fg: #f2f2f7; --muted: #98989f; --line: #2c2c2e; --accent: #64a8ff; --tag: #1c1c1e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; }
h2 a { font-weight: normal; font-size: 14px; margin-left: 8px; }
p.meta { color: var(--muted); margin: 0; font-size: 13px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
table { width: 100%; border-collapse: collapse; }
td, th { text-align: left; padding: 8px 8px 8px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: normal; font-size: 13px; }
code { font-size: 13px; }
.env { display: inline-block; padding: 0 8px; border-radius: 4px; background: var(--tag); font-size: 13px; }
.empty { color: var(--muted); }
@media (max-width: 600px) { .when { display: none; } }
"""


class Markup(str):
    """HTML that html() inserts verbatim."""


def html(template: Template) -> Markup:
    """Renders a t-string, escaping every interpolated value that is not Markup."""
    parts: list[str] = []
    for item in template:
        match item:
            case str():
                parts.append(item)
            case Interpolation(value=Markup() as markup):
                parts.append(markup)
            case Interpolation(value=list() as items) if all(isinstance(i, Markup) for i in items):
                parts.append("".join(items))
            case Interpolation(value=value):
                parts.append(escape(str(value)))
    return Markup("".join(parts))


def _link(href: str, inner: str) -> Markup:
    return html(t'<a href="{href}" target="_blank" rel="noopener noreferrer">{inner}</a>')


def _row(e: Entry) -> Markup:
    label = "next" if e["env"] == "next" else e["slot"]
    github = f"https://github.com/{e['repo']}"
    env = _link(e["url"], html(t'<span class="env">{label}</span>'))
    branch = _link(f"{github}/tree/{e['branch']}", e["branch"])
    commit = _link(f"{github}/commit/{e['sha']}", html(t"<code>{e['sha'][:7]}</code>"))
    return html(t'<tr><td>{env}</td><td>{branch}</td><td>{commit}</td><td class="when">{e["deployed_at"]}</td></tr>')


def _site(site: str, rows: list[Entry]) -> Markup:
    header = '<tr><th>env</th><th>branch</th><th>commit</th><th class="when">deployed</th></tr>'
    body = [_row(e) for e in rows]
    return html(t"<h2>{site}{_link(rows[0]['prod'], 'prod')}</h2>\n<table>{Markup(header)}\n{body}\n</table>\n")


def _workflow_row(e: Workflow) -> Markup:
    github = f"https://github.com/{e['repo']}"
    env = _link(e["url"], html(t'<span class="env">{e["env"]}</span>')) if e["url"] else html(t'<span class="env">{e["env"]}</span>')
    branch = _link(f"{github}/tree/{e['branch']}", e["branch"])
    commit = _link(f"{github}/commit/{e['sha']}", html(t"<code>{e['sha'][:7]}</code>"))
    state = _link(e["workflow_url"], e["state"]) if e["workflow_url"] else e["state"]
    return html(t'<tr><td>{e["site"]}</td><td>{env}</td><td>{branch}</td><td>{commit}</td><td>{state}</td><td class="when">{e["updated"]}</td></tr>')


def _workflow_table(workflows: list[Workflow]) -> Markup:
    if not workflows:
        return Markup()
    header = '<h2>deploy workflows</h2>\n<table><tr><th>site</th><th>env</th><th>branch</th><th>commit</th><th>state</th><th class="when">updated</th></tr>\n'
    rows = [_workflow_row(e) for e in workflows]
    return html(t"{Markup(header)}{rows}\n</table>\n")


def render(entries: list[Entry], workflows: list[Workflow], generated: str) -> str:
    sites = [_site(site, list(rows)) for site, rows in groupby(entries, key=lambda e: e["site"])]
    empty = Markup('<p class="empty">Nothing deployed to next or preview.</p>\n') if not entries else Markup()
    workflow_table = _workflow_table(workflows)
    return html(t"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>muelle previews</title>
<style>
{Markup(STYLE)}</style>
</head>
<body>
<main>
<h1>muelle previews</h1>
<p class="meta">{len(entries)} deployment(s) &middot; {len(workflows)} in-progress/failed workflow(s) &middot; generated {generated}</p>
{workflow_table}{empty}{sites}</main>
</body>
</html>
""")


def write(config: Config) -> None:
    out: Path = config.state_dir / "index"
    out.mkdir(parents=True, exist_ok=True)
    with config.state.lock("index"):
        entries = collect(config)
        workflows = collect_workflows(config)
        generated = utc_now()
        write_json_atomic(out / "index.json", {"generated": generated, "entries": entries, "workflows": workflows})
        write_atomic(out / "index.html", render(entries, workflows, generated))


def main() -> int:
    write(Config.load())
    return 0

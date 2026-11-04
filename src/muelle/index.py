"""Listing (`muelle index`)

Regenerates the listing of deployed next/preview environments served at the
muelle domain: <stateDir>/index/{index.json,index.html}. Above them, per site,
the jobs of every env still building or deploying, and recent failures.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from string.templatelib import Interpolation, Template
from typing import Any, TypedDict

from . import protocol
from .config import Config, Site
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
    run_url: str


class Job(TypedDict):
    id: str
    site: str
    action: str
    env: str
    branch: str
    sha: str
    url: str
    run_url: str
    state: str
    message: str
    updated: str
    repo: str
    prod: str


# How long a job stays listed by state. Older queued/in_progress jobs are lost
# (worker killed, CI runner gone without an abort).
MAX_AGE = {"queued": timedelta(hours=1), "in_progress": timedelta(hours=1), "failure": timedelta(days=7)}
# jobs about a build or deploy, shown while pending or failed
SHOWN_ACTIONS = ("announce", "abort", "deploy")


def job_key(job: dict[str, Any]) -> tuple[str, str, str]:
    """What a job is about: a newer job with the same key supersedes it. Previews are
    per branch (a new commit supersedes the last); prod and next are one slot each."""
    env = str(job.get("env", ""))
    return str(job.get("site", "")), env, str(job.get("branch", "")) if env == "preview" else ""


def latest_jobs(jobs: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The newest job of each key. Times have one-second resolution: on a tie, an
    announce is older than the deploy or abort that follows it."""

    def age(job: dict[str, Any]) -> tuple[str, bool, str]:
        return str(job.get("created", "")), job.get("action") != "announce", str(job.get("updated", ""))

    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for job in sorted(jobs, key=age):
        latest[job_key(job)] = job
    return latest


def _parse(when: str) -> datetime:
    try:
        return datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def is_pending(job: Job) -> bool:
    return job["state"] in ("queued", "in_progress")


def _job(job: dict[str, Any], site: Site) -> Job:
    def get(key: str) -> str:
        return str(job.get(key) or "")

    return Job(
        id=get("id"),
        site=get("site"),
        action=get("action"),
        env=get("env"),
        branch=get("branch"),
        sha=get("sha"),
        url=get("url"),
        run_url=get("run_url"),
        state=get("state"),
        message=get("message"),
        updated=get("updated"),
        repo=site.repo,
        prod=site.prod_url,
    )


def collect_jobs(config: Config, now: datetime) -> list[Job]:
    """Per key, the newest job if it is still pending or failed recently: grouped by site, newest first."""
    jobs: list[Job] = []
    for job in latest_jobs(config.state.jobs()).values():
        site = config.sites.get(str(job.get("site", "")))
        if site is None or job.get("action") not in SHOWN_ACTIONS:
            continue
        age = MAX_AGE.get(str(job.get("state", "")))
        if age is None or now - _parse(str(job.get("updated", ""))) > age:
            continue
        jobs.append(_job(job, site))
    jobs.sort(key=lambda j: j["updated"], reverse=True)
    jobs.sort(key=lambda j: j["site"])
    return jobs


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
                    run_url=meta.get("run_url", ""),
                )
            )
    entries.sort(key=lambda e: e["deployed_at"], reverse=True)
    entries.sort(key=lambda e: (e["site"], 0 if e["env"] == "next" else 1))
    return entries


STYLE = """\
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --accent: #0a66c2; --tag: #f2f2f7;
  --fail: #d70015; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111113; --fg: #f2f2f7; --muted: #98989f; --line: #2c2c2e; --accent: #64a8ff; --tag: #1c1c1e;
    --fail: #ff6961; }
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
table.jobs { margin-bottom: 16px; }
.failed { color: var(--fail); }
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


def _github(repo: str, branch: str, sha: str) -> tuple[Markup, Markup]:
    github = f"https://github.com/{repo}"
    branch_link = _link(f"{github}/tree/{branch}", branch)
    commit = _link(f"{github}/commit/{sha}", html(t"<code>{sha[:7]}</code>")) if sha else Markup()
    return branch_link, commit


def _run(run_url: str) -> Markup:
    return _link(run_url, "run") if run_url else Markup()


def _row(e: Entry) -> Markup:
    label = "next" if e["env"] == "next" else e["slot"]
    env = _link(e["url"], html(t'<span class="env">{label}</span>'))
    branch, commit = _github(e["repo"], e["branch"], e["sha"])
    return html(
        t"<tr><td>{env}</td><td>{branch}</td><td>{commit}</td><td>{_run(e['run_url'])}</td>"
        t'<td class="when">{e["deployed_at"]}</td></tr>'
    )


def _job_state(j: Job) -> Markup:
    if j["state"] == "failure":
        return html(t'<span class="failed">{j["message"] or "failed"}</span>')
    return Markup("building" if j["action"] == "announce" else "deploying")


def _job_row(j: Job) -> Markup:
    branch, commit = _github(j["repo"], j["branch"], j["sha"])
    env = html(t'<span class="env">{j["env"]}</span>')
    return html(
        t"<tr><td>{env}</td><td>{branch}</td><td>{commit}</td><td>{_run(j['run_url'])}</td>"
        t'<td>{_job_state(j)}</td><td class="when">{j["updated"]}</td></tr>'
    )


def _site(site: str, prod: str, rows: list[Entry], jobs: list[Job]) -> Markup:
    parts = [html(t"<h2>{site}{_link(prod, 'prod')}</h2>\n")]
    if jobs:
        header = Markup(
            '<tr><th>env</th><th>branch</th><th>commit</th><th>run</th><th>state</th><th class="when">updated</th></tr>'
        )
        parts.append(html(t'<table class="jobs">{header}\n{[_job_row(j) for j in jobs]}\n</table>\n'))
    if rows:
        header = Markup('<tr><th>env</th><th>branch</th><th>commit</th><th>run</th><th class="when">deployed</th></tr>')
        parts.append(html(t"<table>{header}\n{[_row(e) for e in rows]}\n</table>\n"))
    return Markup("".join(parts))


# while something is pending, browsers reload the listing to follow it
REFRESH_SECONDS = 15


def render(entries: list[Entry], generated: str, jobs: list[Job] | None = None) -> str:
    jobs = jobs or []
    prods = {e["site"]: e["prod"] for e in [*entries, *jobs]}
    sites = [
        _site(site, prod, [e for e in entries if e["site"] == site], [j for j in jobs if j["site"] == site])
        for site, prod in sorted(prods.items())
    ]
    empty = Markup('<p class="empty">Nothing deployed to next or preview.</p>\n') if not sites else Markup()
    pending = sum(is_pending(j) for j in jobs)
    failed = len(jobs) - pending
    refresh = Markup(f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">\n') if pending else Markup()
    return html(t"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{refresh}<title>muelle previews</title>
<style>
{Markup(STYLE)}</style>
</head>
<body>
<main>
<h1>muelle previews</h1>
<p class="meta">{len(entries)} deployment(s) &middot; {pending} in progress &middot; {failed} failed
&middot; generated {generated}</p>
{empty}{sites}</main>
</body>
</html>
""")


def write(config: Config) -> None:
    out: Path = config.state_dir / "index"
    out.mkdir(parents=True, exist_ok=True)
    with config.state.lock("index"):
        entries = collect(config)
        jobs = collect_jobs(config, datetime.now(UTC))
        generated = utc_now()
        write_json_atomic(out / "index.json", {"generated": generated, "entries": entries, "jobs": jobs})
        write_atomic(out / "index.html", render(entries, generated, jobs))

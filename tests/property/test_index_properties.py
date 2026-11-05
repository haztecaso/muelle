"""Properties of the listing: escaping of any text, and which job wins per key."""

from html.parser import HTMLParser
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from muelle import index


class _Parsed(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


TAGS = {"html", "head", "meta", "title", "style", "body", "main", "h1", "h2", "p", "a", "table", "tr", "th", "td"}
TAGS |= {"span", "code"}

# text that often looks like markup
markupish = st.text(st.sampled_from("<>&\"'=/ ab") | st.characters(), min_size=1)


@given(branch=markupish, message=markupish, when=markupish)
def test_render_never_lets_text_become_markup(branch: str, message: str, when: str) -> None:
    entry = index.Entry(
        site="demo",
        env="preview",
        branch=branch,
        sha="a" * 40,
        deployed_at=when,
        slot="aaaaaaaa",
        url="https://aaaaaaaa-demo.muelle.test",
        repo="owner/demo",
        prod="https://demo.test",
        run_url="",
    )
    failed = index.Job(
        id="j",
        site="demo",
        action="deploy",
        env="next",
        branch=branch,
        sha="b" * 40,
        url="",
        run_url="",
        state="failure",
        message=message,
        updated=when,
        repo="owner/demo",
        prod="https://demo.test",
    )
    parsed = _Parsed()
    parsed.feed(index.render([entry], when, [failed]))
    parsed.close()
    assert parsed.tags <= TAGS
    text = "".join(parsed.text)
    assert branch in text
    assert message in text


jobs = st.lists(
    st.fixed_dictionaries(
        {
            "site": st.sampled_from(["demo", "other"]),
            "env": st.sampled_from(["prod", "next", "preview"]),
            "branch": st.sampled_from(["main", "feat"]),
            "action": st.sampled_from(["announce", "abort", "deploy", "teardown"]),
            "created": st.sampled_from(["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"]),
            "updated": st.sampled_from(["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"]),
        }
    ),
    max_size=12,
)


def _age(job: dict[str, Any]) -> tuple[str, int, str]:
    """Within a second, a run goes announce, abort, then deploy or teardown."""
    rank = {"announce": 0, "abort": 1}.get(job["action"], 2)
    return job["created"], rank, job["updated"]


@given(jobs=jobs, data=st.data())
def test_latest_jobs_keeps_the_newest_of_each_key(jobs: list[dict[str, Any]], data: st.DataObject) -> None:
    latest = index.latest_jobs(jobs)
    assert set(latest) == {index.job_key(j) for j in jobs}
    for key, job in latest.items():
        assert _age(job) == max(_age(j) for j in jobs if index.job_key(j) == key)
    shuffled = index.latest_jobs(data.draw(st.permutations(jobs)))
    assert {k: _age(j) for k, j in shuffled.items()} == {k: _age(j) for k, j in latest.items()}

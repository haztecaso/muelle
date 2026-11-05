"""Properties of safe extraction, over generated trees, names, sizes and modes.

Hypothesis runs each test many times, so they extract into their own
temporary directory instead of the function-scoped tmp_path.
"""

import io
import posixpath
import tarfile
import tempfile
from pathlib import Path

import pytest
from helpers import make_tar, make_zip
from hypothesis import assume, given
from hypothesis import strategies as st

from muelle.archive import ArchiveError, check_name, extract

# a path segment that is never "." or ".."
segments = st.from_regex(r"[a-z0-9_-][a-z0-9._-]{0,7}", fullmatch=True)


def write(tmp: Path, data: bytes) -> Path:
    path = tmp / "artifact"
    path.write_bytes(data)
    return path


def _is_prefix(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return a != b and b[: len(a)] == a


@st.composite
def trees(draw: st.DrawFn) -> dict[str, str]:
    """Files by relative path, where no file is also a directory of another."""
    paths = draw(st.lists(st.lists(segments, min_size=1, max_size=3).map(tuple), min_size=1, max_size=8, unique=True))
    assume(not any(_is_prefix(a, b) for a in paths for b in paths))
    return {"/".join(path): draw(st.text(max_size=20)) for path in paths}


def _files(root: Path) -> dict[str, str]:
    return {
        (dirpath / name).relative_to(root).as_posix(): (dirpath / name).read_text(newline="")
        for dirpath, _, names in root.walk()
        for name in names
    }


@given(files=trees())
def test_zip_round_trips_any_tree(files: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        extract(write(Path(tmp), make_zip(files)), Path(tmp) / "root")
        assert _files(Path(tmp) / "root") == files


@given(files=trees())
def test_tar_round_trips_any_tree(files: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = write(Path(tmp), make_tar([(f"dist/{name}", content) for name, content in files.items()]))
        extract(archive, Path(tmp) / "root")
        assert _files(Path(tmp) / "root") == files


@given(name=st.text(alphabet="ab./", max_size=12))
def test_check_name_accepts_only_names_inside_dest(name: str) -> None:
    try:
        check_name(name)
    except ArchiveError:
        return
    assert posixpath.normpath(posixpath.join("/dest", name)).startswith("/dest"), name


evil_names = st.builds(
    lambda head, tail, absolute: ("/" if absolute else "") + "/".join([*head, *tail]),
    st.lists(st.sampled_from(["..", "a", "b", "."]), max_size=3),
    st.lists(st.sampled_from(["..", "a", "b"]), min_size=1, max_size=3),
    st.booleans(),
).filter(lambda name: name.startswith("/") or ".." in name.split("/"))


@given(names=st.lists(evil_names, min_size=1, max_size=3), fmt=st.sampled_from(["zip", "tar"]))
def test_escaping_names_are_rejected_before_writing(names: list[str], fmt: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        entries = [("dist/index.html", "ok"), *((name, "evil") for name in names)]
        data = make_zip(dict(entries), top="") if fmt == "zip" else make_tar(list(entries))
        with pytest.raises(ArchiveError):
            extract(write(Path(tmp), data), Path(tmp) / "root")
        assert sorted(p.name for p in Path(tmp).iterdir()) == ["artifact", "root"]
        assert not any((Path(tmp) / "root").iterdir())


@given(
    sizes=st.lists(st.integers(0, 50), min_size=1, max_size=10),
    max_bytes=st.integers(0, 300),
    max_files=st.integers(1, 10),
)
def test_limits_reject_exactly_what_exceeds_them(sizes: list[int], max_bytes: int, max_files: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = write(Path(tmp), make_zip({f"f{i}": "x" * size for i, size in enumerate(sizes)}, top=""))
        if len(sizes) > max_files or sum(sizes) > max_bytes:
            with pytest.raises(ArchiveError):
                extract(archive, Path(tmp) / "root", max_bytes=max_bytes, max_files=max_files)
        else:
            extract(archive, Path(tmp) / "root", max_bytes=max_bytes, max_files=max_files)


@given(modes=st.lists(st.integers(0, 0o7777), min_size=1, max_size=5), dir_mode=st.integers(0, 0o7777))
def test_any_mode_is_normalized(modes: list[int], dir_mode: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            sub = tarfile.TarInfo("sub")
            sub.type = tarfile.DIRTYPE
            sub.mode = dir_mode
            t.addfile(sub)
            for i, mode in enumerate(modes):
                info = tarfile.TarInfo(f"sub/f{i}")
                info.mode = mode
                t.addfile(info, io.BytesIO())
            # a second top-level entry: no unwrapping
            t.addfile(tarfile.TarInfo("top"), io.BytesIO())
        root = Path(tmp) / "root"
        extract(write(Path(tmp), buf.getvalue()), root)
        assert root.stat().st_mode & 0o7777 == 0o755
        assert (root / "sub").stat().st_mode & 0o7777 == 0o755
        for i, mode in enumerate(modes):
            assert (root / "sub" / f"f{i}").stat().st_mode & 0o7777 == (0o755 if mode & 0o100 else 0o644)

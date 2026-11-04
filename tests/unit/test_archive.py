import re
import tarfile
import zipfile
from pathlib import Path

import pytest
from helpers import make_tar, make_zip

from muelle.archive import ArchiveError, check_name, extract


def write(tmp_path: Path, data: bytes, name: str = "artifact") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_zip_unwraps_single_top_level_dir(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "hi", "css/a.css": "x"}))
    extract(archive, tmp_path / "root")
    assert (tmp_path / "root" / "index.html").read_text() == "hi"
    assert (tmp_path / "root" / "css" / "a.css").exists()


def test_zip_without_top_level_dir_is_kept(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "hi", "b.html": "b"}, top=""))
    extract(archive, tmp_path / "root")
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["b.html", "index.html"]


def test_single_file_is_not_unwrapped(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"index.html": "hi"}, top=""))
    extract(archive, tmp_path / "root")
    assert (tmp_path / "root" / "index.html").is_file()


def test_tar_gz(tmp_path: Path) -> None:
    archive = write(tmp_path, make_tar([("dist/index.html", "tar")]))
    extract(archive, tmp_path / "root")
    assert (tmp_path / "root" / "index.html").read_text() == "tar"


def test_modes_are_normalized(tmp_path: Path) -> None:
    info = tarfile.TarInfo("secret.html")
    info.mode = 0o600
    archive = write(tmp_path, make_tar([info]))
    extract(archive, tmp_path / "root")
    assert (tmp_path / "root" / "secret.html").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "root").stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("name", ["/etc/passwd", "../x", "a/../../x", "a/.."])
def test_check_name_rejects_escapes(name: str) -> None:
    with pytest.raises(ArchiveError):
        check_name(name)


@pytest.mark.parametrize("name", ["index.html", "./a", "a..b/c", "dist/"])
def test_check_name_accepts(name: str) -> None:
    check_name(name)


def test_tar_dotdot_is_rejected(tmp_path: Path) -> None:
    archive = write(tmp_path, make_tar([("../x", "evil")]))
    with pytest.raises(ArchiveError, match=re.escape("'..' in archive: ../x")):
        extract(archive, tmp_path / "root")
    assert not (tmp_path / "x").exists()


def test_tar_absolute_is_rejected(tmp_path: Path) -> None:
    archive = write(tmp_path, make_tar([("/srv/x", "evil")]))
    with pytest.raises(ArchiveError, match="absolute path"):
        extract(archive, tmp_path / "root")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_tar_non_regular_is_rejected(tmp_path: Path, kind: bytes) -> None:
    info = tarfile.TarInfo("index.html")
    info.type = kind
    info.linkname = "/etc/passwd"
    archive = write(tmp_path, make_tar([info]))
    with pytest.raises(ArchiveError, match="non-regular"):
        extract(archive, tmp_path / "root")


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("index.html")
        info.external_attr = (0o120777 << 16) | 0x20
        z.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveError, match="non-regular"):
        extract(path, tmp_path / "root")


def test_zip_dotdot_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("../x", "evil")
    with pytest.raises(ArchiveError, match=re.escape("'..'")):
        extract(path, tmp_path / "root")


def test_unknown_format(tmp_path: Path) -> None:
    archive = write(tmp_path, b"junk\n")
    with pytest.raises(ArchiveError, match="unknown artifact format"):
        extract(archive, tmp_path / "root")


def test_zip_bomb_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", b"\0" * (2 << 20))
    assert path.stat().st_size < 64 << 10
    with pytest.raises(ArchiveError, match="expands to"):
        extract(path, tmp_path / "root", max_bytes=1 << 20)
    assert not any((tmp_path / "root").iterdir())


def test_tar_gz_bomb_is_rejected(tmp_path: Path) -> None:
    archive = write(tmp_path, make_tar([("index.html", "\0" * (2 << 20))]))
    assert archive.stat().st_size < 64 << 10
    with pytest.raises(ArchiveError, match="expands to"):
        extract(archive, tmp_path / "root", max_bytes=1 << 20)
    assert not any((tmp_path / "root").iterdir())


def test_size_at_limit_is_accepted(tmp_path: Path) -> None:
    archive = write(tmp_path, make_zip({"a": "x" * 10, "b": "y" * 10}, top=""))
    extract(archive, tmp_path / "root", max_bytes=20)
    assert (tmp_path / "root" / "b").read_text() == "y" * 10


@pytest.mark.parametrize("make", [make_zip, lambda files: make_tar(list(files.items()))])
def test_too_many_files_is_rejected(tmp_path: Path, make) -> None:
    archive = write(tmp_path, make({f"{i}.html": "" for i in range(11)}))
    with pytest.raises(ArchiveError, match="entries"):
        extract(archive, tmp_path / "root", max_files=10)


def test_lying_zip_size_is_bounded(tmp_path: Path) -> None:
    """A zip member that decompresses past its declared size fails instead of filling the disk."""
    path = tmp_path / "artifact"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", b"\0" * (2 << 20))
    data = bytearray(path.read_bytes())
    real = (2 << 20).to_bytes(4, "little")
    fake = (10).to_bytes(4, "little")
    assert data.count(real) == 2  # local header and central directory
    path.write_bytes(bytes(data).replace(real, fake))
    with pytest.raises(zipfile.BadZipFile):
        extract(path, tmp_path / "root", max_bytes=1 << 20)
    assert (tmp_path / "root" / "index.html").stat().st_size <= 10

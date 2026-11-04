"""Safe extraction of site artifacts (zip or tar, optionally compressed)."""

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


class ArchiveError(Exception):
    pass


def check_name(name: str) -> None:
    """Rejects member names that could escape the destination."""
    if PurePosixPath(name).is_absolute():
        raise ArchiveError(f"absolute path in archive: {name}")
    if ".." in PurePosixPath(name).parts:
        raise ArchiveError(f"'..' in archive: {name}")


def _non_regular() -> ArchiveError:
    # Static content must be plain files and dirs: symlinks could expose
    # arbitrary host files through nginx.
    return ArchiveError("archive contains non-regular files (symlinks, devices, ...)")


def _extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            check_name(info.filename)
            # no file type bits: archivers that store no unix type (regular file or dir by name)
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise _non_regular()
        z.extractall(dest)  # noqa: S202 members checked above


def _extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:*") as t:
        members = t.getmembers()
        for m in members:
            check_name(m.name)
            if not (m.isfile() or m.isdir()):
                raise _non_regular()
        # "data" is the 3.14 default; explicit as a second line of defense
        t.extractall(dest, members=members, filter="data")


def _check_tree(root: Path) -> None:
    for dirpath, dirnames, filenames in root.walk():
        for name in dirnames + filenames:
            st = (dirpath / name).lstat()
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise _non_regular()


def _normalize_modes(root: Path) -> None:
    """chmod -R u=rwX,go=rX"""
    root.chmod(0o755)
    for dirpath, dirnames, filenames in root.walk():
        for name in dirnames:
            (dirpath / name).chmod(0o755)
        for name in filenames:
            path = dirpath / name
            path.chmod(0o755 if path.stat().st_mode & 0o100 else 0o644)


def _unwrap_single_dir(dest: Path) -> None:
    """Hoists the content of a lone top-level directory (e.g. 'dist/')."""
    entries = list(dest.iterdir())
    if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
        return
    unwrapped = dest.with_name(dest.name + ".unwrap")
    entries[0].rename(unwrapped)
    dest.rmdir()
    unwrapped.rename(dest)


def extract(archive: Path, dest: Path) -> None:
    """Extracts ARCHIVE into DEST (created), rejecting anything but plain files and dirs."""
    dest.mkdir(parents=True)
    if zipfile.is_zipfile(archive):
        _extract_zip(archive, dest)
    elif tarfile.is_tarfile(archive):
        _extract_tar(archive, dest)
    else:
        raise ArchiveError("unknown artifact format (expected zip or tar)")
    _check_tree(dest)
    _normalize_modes(dest)
    _unwrap_single_dir(dest)

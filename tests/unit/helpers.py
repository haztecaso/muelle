import hashlib
import io
import tarfile
import zipfile


def make_zip(files: dict[str, str], top: str = "dist") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(f"{top}/{name}" if top else name, content)
    return buf.getvalue()


def make_tar(entries: list[tarfile.TarInfo | tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                t.addfile(entry)
            else:
                name, content = entry
                data = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

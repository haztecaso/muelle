"""Artifact download: bounded size, retried, no downgrade from https."""

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO


class DownloadError(Exception):
    pass


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if req.full_url.startswith("https://") and not newurl.startswith("https://"):
            raise DownloadError(f"refusing redirect from https to {newurl.split(':', 1)[0]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoDowngrade)

CHUNK = 1 << 16


def _copy_bounded(src: IO[bytes], dest: IO[bytes], max_bytes: int) -> None:
    total = 0
    while chunk := src.read(CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise DownloadError(f"artifact larger than {max_bytes} bytes")
        dest.write(chunk)


def download(url: str, dest: Path, *, token: str, max_bytes: int, retries: int = 2, timeout: float = 60) -> None:
    request = urllib.request.Request(url)
    if token:
        # unredirected: GitHub redirects artifact downloads to blob storage,
        # which must not receive (and rejects) the GitHub token
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    for attempt in range(retries + 1):
        try:
            with _opener.open(request, timeout=timeout) as response, dest.open("wb") as f:
                _copy_bounded(response, f, max_bytes)
            return
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                raise DownloadError(f"download failed: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise DownloadError(f"download failed: {e}") from None
        time.sleep(1 + attempt)


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

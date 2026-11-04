from pathlib import Path

import pytest

from muelle.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    state = tmp_path / "state"
    for sub in ("status", "spool", "log", "locks", "index"):
        (state / sub).mkdir(parents=True)
    return Config.from_json(
        {
            "domain": "muelle.test",
            "base_dir": str(tmp_path / "www"),
            "state_dir": str(state),
            "max_skew": 300,
            "keep_releases": 2,
            "keep_previews": 2,
            "max_artifact_bytes": 1 << 20,
            "max_extracted_bytes": 1 << 20,
            "max_artifact_files": 100,
            "sites": {
                "demo": {
                    "repo": "owner/demo",
                    "prod_url": "https://demo.test",
                    "envs": ["prod", "next", "preview"],
                    "artifact_url_prefix": "https://api.github.com/repos/owner/demo/",
                },
                "prodonly": {
                    "repo": "owner/prodonly",
                    "prod_url": "https://prodonly.test",
                    "envs": ["prod"],
                    "artifact_url_prefix": "https://api.github.com/repos/owner/prodonly/",
                },
            },
        }
    )

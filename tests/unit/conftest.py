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
            "baseDir": str(tmp_path / "www"),
            "stateDir": str(state),
            "maxSkew": 300,
            "keepReleases": 2,
            "keepPreviews": 2,
            "maxArtifactBytes": 1 << 20,
            "sites": {
                "demo": {
                    "repo": "owner/demo",
                    "prodUrl": "https://demo.test",
                    "envs": ["prod", "next", "preview"],
                    "artifactUrlPrefix": "https://api.github.com/repos/owner/demo/",
                },
                "prodonly": {
                    "repo": "owner/prodonly",
                    "prodUrl": "https://prodonly.test",
                    "envs": ["prod"],
                    "artifactUrlPrefix": "https://api.github.com/repos/owner/prodonly/",
                },
            },
        }
    )

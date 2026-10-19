import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def photo():
    from skimage import data
    return data.astronaut().astype(np.float32) / 255.0


@pytest.fixture(scope="session")
def chart():
    """Grey ramp on top, colour patches below."""
    h, w = 240, 480
    img = np.zeros((h, w, 3), np.float32)
    img[:120] = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    cols = [(0.8, 0.2, 0.2), (0.85, 0.5, 0.2), (0.8, 0.8, 0.2), (0.2, 0.7, 0.2),
            (0.2, 0.7, 0.7), (0.2, 0.3, 0.8), (0.5, 0.25, 0.8), (0.8, 0.2, 0.7)]
    for i, c in enumerate(cols):
        img[120:, i * 60:(i + 1) * 60] = c
    return img


@pytest.fixture(scope="session")
def library():
    from engine.presets import load_folder
    return load_folder(ROOT / "presets" / "library")


@pytest.fixture(autouse=True)
def like_the_app(monkeypatch):
    """Test clients send X-Requested-With, as the app's own pages do (server/webguard.py).
    Tests of the cross-site check pass headers={"X-Requested-With": ""} to leave it out."""
    from starlette.testclient import TestClient
    original = TestClient.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        self.headers.setdefault("X-Requested-With", "AutoEdits")
    monkeypatch.setattr(TestClient, "__init__", init)

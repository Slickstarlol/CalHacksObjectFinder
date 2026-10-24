import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_object_image(color, size=(120, 160), seed=0, bg=(128, 128, 128)):
    """A coloured, textured 'object' on a grey background (BGR)."""
    rng = np.random.default_rng(seed)
    h, w = size
    img = np.full((h, w, 3), bg, np.uint8)
    obj = np.full((h - 20, w - 20, 3), color, np.int16)
    obj += rng.integers(-18, 18, obj.shape, dtype=np.int16)  # texture/noise
    img[10:-10, 10:-10] = np.clip(obj, 0, 255).astype(np.uint8)
    return img


def register(base, name, category, images, item_id=None):
    item_id = item_id or name.lower().replace(" ", "_")
    folder = Path(base) / item_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.json").write_text(
        json.dumps({"id": item_id, "name": name, "category": category}))
    for i, img in enumerate(images, 1):
        cv2.imwrite(str(folder / f"reference_{i}.jpg"), img)
    return item_id


@pytest.fixture
def belongings_dir(tmp_path):
    d = tmp_path / "belongings"
    d.mkdir()
    return d


RED = (40, 40, 200)
BLUE = (200, 60, 30)
GREEN = (50, 170, 60)

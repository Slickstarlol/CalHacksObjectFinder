"""Recognize *your* belongings, not just object categories.

YOLO says "that's a backpack". This module answers "is it MY backpack?"
by comparing a crop of the detection with the reference photos saved by
register.py, using image embeddings (DINOv2 by default).

    matcher = BelongingMatcher()
    match = matcher.match(crop_bgr, "backpack")
    if match:
        print(match.name, match.score)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
BELONGINGS_DIR = BASE_DIR / "belongings"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ---------------------------------------------------------------------------
# Embedders: turn an image into a unit-length vector.
# Similar-looking items -> vectors with high cosine similarity.
# ---------------------------------------------------------------------------
class DinoEmbedder:
    """DINOv2 image embeddings (default).

    Tested on real snapshots from this project:
      same item in two different frames ........ 0.77 - 0.95
      other chairs in the same room ............ 0.50 - 0.64
      100 strangers' objects (COCO) ............ best 0.25 - 0.50
    That gap is ~3-4x wider than CLIP's, i.e. much better at "is this MY
    bottle or just A bottle". First run downloads ~90 MB from Hugging Face.
    """

    name = "dinov2"
    default_threshold = 0.70

    def __init__(self, model_id="facebook/dinov2-small", device=None):
        import torch
        from transformers import AutoImageProcessor, AutoModel

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = AutoModel.from_pretrained(model_id).to(self.device).eval()
        self.processor = AutoImageProcessor.from_pretrained(model_id)

    def embed(self, images_bgr):
        if not images_bgr:
            return np.zeros((0, 2 * self.model.config.hidden_size), np.float32)
        rgb = [cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in images_bgr]
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            tokens = self.model(**inputs).last_hidden_state
        # CLS token (global look) + mean of patch tokens (texture/detail)
        feats = self.torch.cat([tokens[:, 0], tokens[:, 1:].mean(1)], dim=1)
        return _normalize(feats.cpu().numpy().astype(np.float32))


class ClipEmbedder:
    """CLIP image embeddings. Alternative to DINOv2; in our tests it
    rated strangers' bottles/laptops too similar to yours (0.82 vs 0.94),
    so it needs a high threshold. ~600 MB download.
    """

    name = "clip"
    default_threshold = 0.90

    def __init__(self, model_id="openai/clip-vit-base-patch32", device=None):
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CLIPModel.from_pretrained(model_id).to(self.device).eval()
        self.processor = CLIPProcessor.from_pretrained(model_id)

    def embed(self, images_bgr):
        if not images_bgr:
            return np.zeros((0, 512), dtype=np.float32)
        rgb = [cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in images_bgr]
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            feats = self.model.get_image_features(**inputs)
            # Newer transformers versions may return a model-output object.
            if not isinstance(feats, self.torch.Tensor):
                feats = getattr(feats, "image_embeds", None) or feats.pooler_output
        feats = feats.cpu().numpy().astype(np.float32)
        return _normalize(feats)


class HistogramEmbedder:
    """Colour-histogram fallback. No downloads, very fast, but only tells
    items apart by colour. Used when torch/transformers aren't installed.
    """

    name = "histogram"
    default_threshold = 0.80

    def embed(self, images_bgr):
        out = []
        for img in images_bgr:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            # Only count the central ellipse: crops have background at the
            # edges (table, wall) that shouldn't decide the match.
            h, w = img.shape[:2]
            mask = np.zeros((h, w), np.uint8)
            cv2.ellipse(mask, (w // 2, h // 2), (max(1, int(w * 0.38)),
                        max(1, int(h * 0.38))), 0, 0, 360, 255, -1)
            hist = cv2.calcHist(
                [hsv], [0, 1, 2], mask, [12, 6, 4], [0, 180, 0, 256, 0, 256]
            ).flatten()
            hist = np.sqrt(hist / max(hist.sum(), 1e-9))  # Hellinger
            out.append(hist.astype(np.float32))
        if not out:
            return np.zeros((0, 12 * 6 * 4), dtype=np.float32)
        return _normalize(np.stack(out))


def _normalize(x):
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.maximum(norms, 1e-9)


EMBEDDERS = {"dinov2": DinoEmbedder, "clip": ClipEmbedder,
             "histogram": HistogramEmbedder}


def load_embedder(prefer="dinov2"):
    try:
        return EMBEDDERS[prefer]()
    except ImportError:
        print(
            "[recognition] torch/transformers not installed - "
            "falling back to colour histograms. "
            "Run: pip install torch transformers"
        )
    except OSError as e:  # model download failed (offline, firewall)
        print(f"[recognition] could not load {prefer} ({e}); "
              "falling back to colour histograms.")
    return HistogramEmbedder()


# ---------------------------------------------------------------------------
# YOLO often mixes these up (e.g. a gaming mouse seen up close was labelled
# "cell phone" and "remote" in this project's own snapshots). A belonging
# registered as one of them is checked against detections of all of them;
# the embedding similarity decides whether it's really yours.
# ---------------------------------------------------------------------------
CONFUSABLE_GROUPS = [
    {"mouse", "cell phone", "remote"},
    {"backpack", "handbag", "suitcase"},
    {"laptop", "tv", "keyboard"},
    {"bottle", "cup", "vase"},
    {"book", "laptop"},
]


def related_categories(category):
    related = {category}
    for group in CONFUSABLE_GROUPS:
        if category in group:
            related |= group
    return related


# ---------------------------------------------------------------------------
# Belongings on disk
# ---------------------------------------------------------------------------
@dataclass
class Belonging:
    id: str
    name: str
    category: str
    folder: Path
    reference_paths: list = field(default_factory=list)


def load_belongings(base_dir=BELONGINGS_DIR):
    base_dir = Path(base_dir)
    items = []
    if not base_dir.exists():
        return items
    for folder in sorted(base_dir.iterdir()):
        info_path = folder / "info.json"
        if not info_path.is_file():
            continue
        info = json.loads(info_path.read_text(encoding="utf-8"))
        refs = sorted(
            p for p in folder.iterdir()
            if p.suffix.lower() in IMAGE_SUFFIXES and p.stem.startswith("reference")
        )
        items.append(
            Belonging(
                id=info.get("id", folder.name),
                name=info["name"],
                category=info["category"].strip().lower(),
                folder=folder,
                reference_paths=refs,
            )
        )
    return items


def crop_box(frame, xyxy, pad=0.08):
    """Crop a detection box with a little padding, clamped to the frame."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in xyxy]
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    x1, y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
    x2, y2 = int(min(w, x2 + px)), int(min(h, y2 + py))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------
@dataclass
class Match:
    belonging_id: str
    name: str
    category: str
    score: float


class BelongingMatcher:
    """Holds reference embeddings for every registered belonging.

    match() compares a crop only against belongings of the same YOLO
    category, and returns the best one if its similarity clears the
    threshold. Below the threshold it returns None: "a backpack, but not
    yours".
    """

    def __init__(self, embedder=None, base_dir=BELONGINGS_DIR,
                 threshold=None, top_k=2):
        self.embedder = embedder or load_embedder()
        self.base_dir = Path(base_dir)
        self.threshold = (
            threshold if threshold is not None
            else self.embedder.default_threshold
        )
        self.top_k = top_k
        self.reload()

    # -- loading -----------------------------------------------------------
    def reload(self):
        self.belongings = []
        self.skipped = []          # registered but no reference photos
        self._refs = {}            # belonging_id -> (N, D) embeddings
        for item in load_belongings(self.base_dir):
            if not item.reference_paths:
                self.skipped.append(item)
                continue
            emb = self._reference_embeddings(item)
            if len(emb):
                self.belongings.append(item)
                self._refs[item.id] = emb
            else:
                self.skipped.append(item)
        for item in self.skipped:
            print(
                f"[recognition] '{item.name}' has no reference photos - "
                f"run: python register.py --id {item.id}"
            )

    def _reference_embeddings(self, item):
        """Embed reference photos, cached in the item's folder."""
        cache = item.folder / f"embeddings_{self.embedder.name}.npz"
        key = np.array(
            [f"{p.name}:{p.stat().st_mtime_ns}" for p in item.reference_paths]
        )
        if cache.exists():
            try:
                data = np.load(cache)
                if np.array_equal(data["key"], key):
                    return data["emb"]
            except Exception:
                pass
        images = [cv2.imread(str(p)) for p in item.reference_paths]
        images = [im for im in images if im is not None and im.size]
        emb = self.embedder.embed(images)
        try:
            np.savez(cache, key=key, emb=emb)
        except OSError:
            pass
        return emb

    @property
    def categories(self):
        """Every YOLO category worth checking (incl. commonly confused ones)."""
        cats = set()
        for b in self.belongings:
            cats |= related_categories(b.category)
        return cats

    # -- matching ----------------------------------------------------------
    def scores(self, crop_bgr, category):
        """Similarity of a crop to every belonging in that category."""
        return self.scores_many([crop_bgr], [category])[0]

    def scores_many(self, crops, categories):
        """Batch version: one embedding pass for all crops in a frame."""
        results = [dict() for _ in crops]
        idx = [i for i, (c, cat) in enumerate(zip(crops, categories))
               if c is not None and c.size and cat in self.categories]
        if not idx:
            return results
        emb = self.embedder.embed([crops[i] for i in idx])
        for row, i in enumerate(idx):
            cat = categories[i]
            for b in self.belongings:
                if cat not in related_categories(b.category):
                    continue
                sims = self._refs[b.id] @ emb[row]
                k = min(self.top_k, len(sims))
                # Mean of the best k references: one odd reference photo
                # can't cause a false match on its own.
                results[i][b.id] = float(np.sort(sims)[-k:].mean())
        return results

    def _best(self, score_map):
        if not score_map:
            return None
        bid, score = max(score_map.items(), key=lambda kv: kv[1])
        if score < self.threshold:
            return None
        b = next(b for b in self.belongings if b.id == bid)
        return Match(b.id, b.name, b.category, score)

    def match(self, crop_bgr, category):
        return self._best(self.scores(crop_bgr, category))

    def match_many(self, crops, categories):
        return [self._best(s) for s in self.scores_many(crops, categories)]

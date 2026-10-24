"""Fast recognition tests using the colour-histogram embedder (no downloads)."""

import numpy as np

from conftest import BLUE, GREEN, RED, make_object_image, register
from recognition import (BelongingMatcher, HistogramEmbedder, crop_box,
                         load_belongings)


class CountingEmbedder(HistogramEmbedder):
    calls = 0

    def embed(self, images):
        CountingEmbedder.calls += len(images)
        return super().embed(images)


def refs(color, n=5):
    return [make_object_image(color, seed=i) for i in range(n)]


def test_load_belongings_ignores_junk(belongings_dir):
    register(belongings_dir, "My Bag", "backpack", refs(RED))
    (belongings_dir / "not_an_item").mkdir()
    items = load_belongings(belongings_dir)
    assert [b.name for b in items] == ["My Bag"]
    assert len(items[0].reference_paths) == 5


def test_item_without_photos_is_skipped_not_crashing(belongings_dir):
    # This is the current state of the real project: info.json only.
    register(belongings_dir, "My Mouse", "mouse", [])
    m = BelongingMatcher(HistogramEmbedder(), belongings_dir)
    assert m.belongings == []
    assert [b.name for b in m.skipped] == ["My Mouse"]
    assert m.match(make_object_image(RED), "mouse") is None


def test_own_item_matches_other_item_does_not(belongings_dir):
    register(belongings_dir, "My Bag", "backpack", refs(RED))
    m = BelongingMatcher(HistogramEmbedder(), belongings_dir)

    same = m.match(make_object_image(RED, seed=99), "backpack")
    assert same is not None and same.name == "My Bag"

    stranger = m.match(make_object_image(BLUE, seed=99), "backpack")
    assert stranger is None  # a backpack, but not yours


def test_category_must_match(belongings_dir):
    register(belongings_dir, "My Bag", "backpack", refs(RED))
    m = BelongingMatcher(HistogramEmbedder(), belongings_dir)
    assert m.match(make_object_image(RED, seed=5), "laptop") is None


def test_picks_the_right_one_of_two_same_category(belongings_dir):
    register(belongings_dir, "Red Bag", "backpack", refs(RED))
    register(belongings_dir, "Green Bag", "backpack", refs(GREEN))
    m = BelongingMatcher(HistogramEmbedder(), belongings_dir)
    assert m.match(make_object_image(GREEN, seed=42), "backpack").name == "Green Bag"
    assert m.match(make_object_image(RED, seed=42), "backpack").name == "Red Bag"


def test_match_many_batches_and_handles_none(belongings_dir):
    register(belongings_dir, "My Bag", "backpack", refs(RED))
    m = BelongingMatcher(HistogramEmbedder(), belongings_dir)
    out = m.match_many(
        [make_object_image(RED, seed=1), None, make_object_image(BLUE, seed=1)],
        ["backpack", "backpack", "backpack"])
    assert out[0].name == "My Bag" and out[1] is None and out[2] is None


def test_embeddings_are_cached(belongings_dir):
    register(belongings_dir, "My Bag", "backpack", refs(RED))
    CountingEmbedder.calls = 0
    BelongingMatcher(CountingEmbedder(), belongings_dir)
    first = CountingEmbedder.calls
    BelongingMatcher(CountingEmbedder(), belongings_dir)
    assert first == 5 and CountingEmbedder.calls == 5  # second load used cache


def test_crop_box_clamps_and_rejects_empty():
    frame = np.zeros((100, 100, 3), np.uint8)
    assert crop_box(frame, (-20, -20, 50, 50)).shape[:2] == (55, 55)
    assert crop_box(frame, (90, 90, 300, 300)).shape[:2] == (27, 27)
    assert crop_box(frame, (0, 0, 10, 10), pad=0).shape[:2] == (10, 10)
    assert crop_box(frame, (50, 50, 50, 80)) is None

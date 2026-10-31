import pandas as pd

from barrierfrei import store
from barrierfrei.evaluate import score
from barrierfrei.judge import Judgement
from barrierfrei.mapillary import Photo
from barrierfrei.osm import ACCESS_TAGS


def _verdict(wheelchair: bool) -> Judgement:
    return Judgement.model_validate(
        {
            "barriers": [] if wheelchair else [{"type": "stairs", "severity": "high", "reason": "steps"}],
            "passable_for": {"wheelchair": wheelchair, "stroller": wheelchair, "suitcase": True},
            "confidence": 0.8,
        }
    )


def test_score_counts_hits_and_misses():
    s = score(
        [
            ("steps", _verdict(False)),  # caught
            ("steps", _verdict(True)),  # missed
            ("footway", _verdict(True)),  # correct pass
            ("footway", _verdict(False)),  # false alarm
        ]
    )
    assert (s.tp, s.fn, s.fp, s.tn) == (1, 1, 1, 1)
    assert s.recall == 0.5 and s.precision == 0.5


def test_eval_sample_takes_closest_photo_per_labelled_segment(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)  # 1-2-0 footway, 3-4-0 steps
    store.save_photos(
        conn,
        [
            Photo("steps_near", 8.6405, 49.871, None, None, False, "u"),
            Photo("steps_far", 8.6405, 49.871, None, None, False, "u"),
            Photo("steps_toofar", 8.6405, 49.871, None, None, False, "u"),
            Photo("foot", 8.6405, 49.870, None, None, False, "u"),
            Photo("pano", 8.6405, 49.870, None, None, True, "u"),
        ],
    )
    store.save_matches(
        conn,
        ["steps_near", "steps_far", "steps_toofar", "foot", "pano"],
        pd.DataFrame(
            {
                "photo_id": ["steps_near", "steps_far", "steps_toofar", "foot", "pano"],
                "segment_id": ["3-4-0", "3-4-0", "3-4-0", "1-2-0", "1-2-0"],
                "distance_m": [2.0, 5.0, 12.0, 3.0, 1.0],
            }
        ),
    )
    assert [r[0] for r in store.eval_sample(conn, "steps", 10)] == ["steps_near"]
    assert [r[0] for r in store.eval_sample(conn, "footway", 10)] == ["foot"]  # panoramas skipped


def test_load_judgement_roundtrip(tmp_path):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_photos(conn, [Photo("p", 8.64, 49.87, None, None, False, "u")])
    assert store.load_judgement(conn, "p") is None
    store.save_perception(conn, "p", [], _verdict(False), "m")
    assert store.load_judgement(conn, "p").passable_for.wheelchair is False

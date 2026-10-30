import json

import pandas as pd

from barrierfrei import store
from barrierfrei.mapillary import Photo
from barrierfrei.osm import ACCESS_TAGS


def test_roundtrip(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)
    store.save_photos(conn, [Photo("p1", 8.6405, 49.87, None, None, False, None)])
    store.save_matches(conn, pd.DataFrame({"photo_id": ["p1"], "segment_id": ["1-2-0"], "distance_m": [1.2]}))

    tags = json.loads(conn.execute("SELECT tags FROM segments WHERE segment_id = '3-4-0'").fetchone()[0])
    assert tags == {"wheelchair": "no"}  # missing values are dropped
    assert conn.execute("SELECT COUNT(*) FROM segment_photos").fetchone()[0] == 1


def test_saving_twice_does_not_duplicate(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)
    store.save_segments(conn, segments, ACCESS_TAGS)
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2

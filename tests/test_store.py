import json

import pandas as pd

from barrierfrei import store
from barrierfrei.mapillary import Photo
from barrierfrei.osm import ACCESS_TAGS


def test_roundtrip(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)
    store.save_photos(conn, [Photo("p1", 8.6405, 49.87, None, None, False, None)])
    store.save_matches(
        conn, ["p1"], pd.DataFrame({"photo_id": ["p1"], "segment_id": ["1-2-0"], "distance_m": [1.2]})
    )

    tags = json.loads(conn.execute("SELECT tags FROM segments WHERE segment_id = '3-4-0'").fetchone()[0])
    assert tags == {"wheelchair": "no"}  # missing values are dropped
    assert conn.execute("SELECT COUNT(*) FROM segment_photos").fetchone()[0] == 1
    first = json.loads(conn.execute("SELECT tags FROM segments WHERE segment_id = '1-2-0'").fetchone()[0])
    assert first["ramp:wheelchair"] == "yes"  # tag names with ':' survive


def test_rematching_replaces_stale_links(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)
    store.save_photos(conn, [Photo("p1", 8.6405, 49.87, None, None, False, None)])
    links = pd.DataFrame({"photo_id": ["p1"], "segment_id": ["1-2-0"], "distance_m": [1.0]})
    store.save_matches(conn, ["p1"], links)
    store.save_matches(conn, ["p1"], links.assign(segment_id="3-4-0"))
    assert conn.execute("SELECT segment_id FROM segment_photos").fetchall() == [("3-4-0",)]
    store.save_matches(conn, ["p1"], links.iloc[0:0])  # now unmatched
    assert conn.execute("SELECT COUNT(*) FROM segment_photos").fetchone()[0] == 0


def test_barrier_nodes_are_saved(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point

    nodes = gpd.GeoDataFrame(
        {"osmid": [42], "barrier": ["kerb"], "kerb": ["raised"], "highway": [None]},
        geometry=[Point(8.64, 49.87)],
        crs="EPSG:4326",
    )
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_barrier_nodes(conn, nodes, ["barrier", "kerb", "highway"])
    tags = json.loads(conn.execute("SELECT tags FROM barrier_nodes WHERE node_id = 42").fetchone()[0])
    assert tags == {"barrier": "kerb", "kerb": "raised"}


def test_saving_twice_does_not_duplicate(tmp_path, segments):
    conn = store.connect(tmp_path / "test.sqlite")
    store.save_segments(conn, segments, ACCESS_TAGS)
    store.save_segments(conn, segments, ACCESS_TAGS)
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2

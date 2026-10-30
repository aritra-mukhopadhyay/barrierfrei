from barrierfrei import mapillary

ITEM = {
    "id": "1",
    "captured_at": 1700000000000,
    "compass_angle": 90.0,
    "is_pano": False,
    "computed_geometry": {"type": "Point", "coordinates": [8.64, 49.87]},
    "thumb_1024_url": "https://x/1.jpg",
}


def test_parse_photo_reads_position():
    photo = mapillary.parse_photo(ITEM)
    assert photo is not None
    assert (photo.id, photo.lon, photo.lat) == ("1", 8.64, 49.87)


def test_parse_photo_skips_unpositioned():
    assert mapillary.parse_photo({"id": "2"}) is None


def test_tiles_cover_bbox_without_overflow():
    tiles = list(mapillary.tiles((8.628, 49.869, 8.653, 49.876)))
    assert tiles[0][:2] == (8.628, 49.869)
    assert max(t[2] for t in tiles) == 8.653
    assert max(t[3] for t in tiles) == 49.876


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self._data}


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, params, timeout):
        self.calls += 1
        return FakeResponse([ITEM])  # every tile returns the same photo


def test_fetch_photos_deduplicates_across_tiles():
    session = FakeSession()
    photos = mapillary.fetch_photos(session, "MLY|test", (8.628, 49.869, 8.653, 49.876))
    assert session.calls > 1
    assert [p.id for p in photos] == ["1"]

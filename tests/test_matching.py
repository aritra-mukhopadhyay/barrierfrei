from barrierfrei.mapillary import Photo
from barrierfrei.matching import match_photos, photos_to_gdf


def photo(pid: str, lon: float, lat: float) -> Photo:
    return Photo(
        id=pid, lon=lon, lat=lat, captured_at=None, compass_angle=None, is_pano=False, thumb_url=None
    )


def test_photo_matches_nearest_segment(segments):
    photos = photos_to_gdf([photo("near-a", 8.6405, 49.87005), photo("near-b", 8.6405, 49.87095)])
    matches = match_photos(segments, photos, max_distance_m=15).set_index("photo_id")
    assert matches.loc["near-a", "segment_id"] == "1-2-0"
    assert matches.loc["near-b", "segment_id"] == "3-4-0"
    assert matches["distance_m"].max() < 15


def test_far_photo_is_dropped(segments):
    photos = photos_to_gdf([photo("far", 8.6405, 49.8705)])  # ~55 m from both paths
    assert match_photos(segments, photos, max_distance_m=15).empty


def test_no_photos_gives_empty_frame(segments):
    photos = photos_to_gdf([])
    assert photos.empty
    assert match_photos(segments, photos, max_distance_m=15).empty

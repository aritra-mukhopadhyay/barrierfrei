"""Attach each photo to the nearest walkable segment."""

from dataclasses import asdict, fields

import geopandas as gpd
import pandas as pd

from barrierfrei.mapillary import Photo

# ETRS89 / UTM 32N: metre units for Germany, so distances are real metres.
METRIC_CRS = "EPSG:25832"


def photos_to_gdf(photos: list[Photo]) -> gpd.GeoDataFrame:
    columns = [f.name for f in fields(Photo)]
    frame = pd.DataFrame([asdict(p) for p in photos], columns=columns)
    return gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(frame.lon, frame.lat), crs="EPSG:4326")


def match_photos(segments: gpd.GeoDataFrame, photos: gpd.GeoDataFrame, max_distance_m: float) -> pd.DataFrame:
    """Return (photo_id, segment_id, distance_m) for photos within max_distance_m of a segment."""
    if photos.empty or segments.empty:
        return pd.DataFrame(columns=["photo_id", "segment_id", "distance_m"])
    joined = gpd.sjoin_nearest(
        photos[["id", "geometry"]].to_crs(METRIC_CRS),
        segments[["segment_id", "geometry"]].to_crs(METRIC_CRS),
        how="inner",
        max_distance=max_distance_m,
        distance_col="distance_m",
    )
    # A photo equidistant to two segments appears twice; keep one.
    joined = joined.drop_duplicates(subset="id")
    return joined.rename(columns={"id": "photo_id"})[["photo_id", "segment_id", "distance_m"]].reset_index(
        drop=True
    )

import geopandas as gpd
import pytest
from shapely.geometry import LineString


@pytest.fixture
def segments() -> gpd.GeoDataFrame:
    # Two short east-west footpaths about 110 m apart in Darmstadt.
    return gpd.GeoDataFrame(
        {
            "segment_id": ["1-2-0", "3-4-0"],
            "u": [1, 3],
            "v": [2, 4],
            "key": [0, 0],
            "length": [70.0, 70.0],
            "highway": ["footway", "steps"],
            "wheelchair": [None, "no"],
            "kerb": ["lowered", None],
            "ramp:wheelchair": ["yes", None],
        },
        geometry=[
            LineString([(8.640, 49.870), (8.641, 49.870)]),
            LineString([(8.640, 49.871), (8.641, 49.871)]),
        ],
        crs="EPSG:4326",
    )

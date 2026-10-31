from barrierfrei import osm

# Two footways and a motorway, with a raised kerb on the shared node; one footway lies outside the box.
OSM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
  <node id="1" version="1" lat="50.1100" lon="8.6700"/>
  <node id="2" version="1" lat="50.1100" lon="8.6710"><tag k="kerb" v="raised"/></node>
  <node id="3" version="1" lat="50.1100" lon="8.6720"/>
  <node id="4" version="1" lat="50.1100" lon="8.6730"/>
  <node id="5" version="1" lat="50.2000" lon="8.9000"/>
  <node id="6" version="1" lat="50.2000" lon="8.9010"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/><tag k="surface" v="sett"/></way>
  <way id="11" version="1"><nd ref="3"/><nd ref="4"/><tag k="highway" v="motorway"/></way>
  <way id="12" version="1"><nd ref="5"/><nd ref="6"/><tag k="highway" v="footway"/></way>
</osm>
"""
BBOX = (8.665, 50.105, 8.680, 50.115)


def test_walkable_matches_osmnx_walk_filter():
    assert osm.walkable({"highway": "footway"})
    assert osm.walkable({"highway": "residential", "sidewalk": "both"})
    assert not osm.walkable({"highway": "motorway_link"})
    assert not osm.walkable({"highway": "cycleway"})
    assert not osm.walkable({"highway": "footway", "access": "private"})
    assert not osm.walkable({"highway": "primary", "sidewalk": "separate"})  # walk the sidewalk way instead
    assert not osm.walkable({"building": "yes"})


def test_offline_extract_feeds_the_same_network(tmp_path):
    import pytest

    pytest.importorskip("osmium")
    from barrierfrei import osm_extract

    source = tmp_path / "region.osm"
    source.write_text(OSM_XML)
    target = tmp_path / "area.osm"
    assert osm_extract.extract(source, BBOX, target) == 1  # motorway and far footway dropped

    segments, barrier_nodes = osm.walk_network(BBOX, tmp_path / "cache", target)
    assert set(segments["osmid"]) == {10}
    assert (segments["surface"] == "sett").all()
    assert list(barrier_nodes["kerb"]) == ["raised"]

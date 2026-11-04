import json

from metadata_convert.ckan import CkanBuilder, map_license, tag_name, validate_package
from metadata_convert.util import clean_text, extract_doi, normalize_datetime


def build(**kw):
    b = CkanBuilder("test")
    b.set_title(kw.get("title", "A title"))
    b.external_id = kw.get("external_id", "id-1")
    return b


def extras(pkg):
    return {e["key"]: e["value"] for e in pkg["extras"]}


def test_license_mapping():
    assert map_license("https://creativecommons.org/licenses/by/4.0/legalcode") == "cc-by"
    assert map_license("CC-BY-4.0") == "cc-by"
    assert map_license("cc0-1.0") == "cc-zero"
    assert map_license("http://creativecommons.org/publicdomain/zero/1.0") == "cc-zero"
    assert map_license("https://creativecommons.org/licenses/by-nc-nd/4.0/") == "cc-nc"
    assert map_license("CC-BY-SA-4.0") == "cc-by-sa"
    assert map_license("ODbL-1.0") == "odc-odbl"
    assert map_license("MIT") == "other-open"
    assert map_license("http://reference.data.gov.uk/id/open-government-licence") == "uk-ogl"
    assert map_license("Baseline Surface Radiation Network License 1.0") is None


def test_tags():
    assert tag_name("Sea Surface Temperature") == "sea-surface-temperature"
    assert tag_name("a") is None
    b = build()
    b.add_tags(["EARTH SCIENCE > OCEANS > OCEAN TEMPERATURE", "foo; bar", "0000698", "Foo"])
    pkg = b.build()
    assert [t["name"] for t in pkg["tags"]] == ["ocean-temperature", "foo", "bar"]


def test_reserved_extra_keys_renamed():
    b = build()
    b.add_extra("name", "x")
    b.add_extra("license", "y")
    b.add_extra("url", "http://z")
    ex = extras(b.build())
    assert ex["source_name"] == "x" and "name" not in ex
    assert ex["rights_statement"] == "y"
    assert "url" not in ex


def test_extras_values_are_strings_and_repeats_join():
    b = build()
    b.add_extra("k", {"a": 1})
    b.add_extra("list", ["a", "b"])
    b.add_extra("rep", "one")
    b.add_extra("rep", "two")
    b.add_extra("rep", "one")
    ex = extras(b.build())
    assert ex["k"] == '{"a": 1}'
    assert ex["list"] == "a; b"
    assert ex["rep"] == "one; two"


def test_spatial_antimeridian_and_point():
    b = build()
    b.add_bbox(170, -10, -170, 10)
    geom = json.loads(extras(b.build())["spatial"])
    assert geom["type"] == "MultiPolygon" and len(geom["coordinates"]) == 2
    b = build()
    b.add_bbox(5, 5, 5, 5)
    assert json.loads(extras(b.build())["spatial"]) == {"type": "Point", "coordinates": [5.0, 5.0]}


def test_spatial_mixed_becomes_envelope():
    b = build()
    b.add_bbox(0, 0, 1, 1)
    b.add_point(5, 5)
    geom = json.loads(extras(b.build())["spatial"])
    assert geom["coordinates"][0][2] == [5.0, 5.0]
    assert any("mixed geometry" in w for w in b.warnings)


def test_temporal():
    b = build()
    b.set_temporal("2001", "2005-06")
    assert json.loads(extras(b.build())["temporal"]) == {
        "startTime": "2001-01-01T00:00:00Z", "endTime": "2005-06-30T23:59:59Z"}
    b = build()
    b.set_temporal("sometime", None)
    ex = extras(b.build())
    assert "temporal" not in ex and ex["temporal_text"] == "sometime"


def test_normalize_datetime():
    assert normalize_datetime("2023-02-14T21:36:55.855361+00:00") == "2023-02-14T21:36:55Z"
    assert normalize_datetime("2020-01-01T05:00:00+05:00") == "2020-01-01T00:00:00Z"
    assert normalize_datetime("2024-02", end=True) == "2024-02-29T23:59:59Z"
    assert normalize_datetime("..") is None
    assert normalize_datetime("garbage") is None


def test_name_is_stable_and_valid():
    a = build(title="Ünïcode / Title!", external_id="doi:10.1/x").build()
    b = build(title="Ünïcode / Title!", external_id="doi:10.1/x").build()
    c = build(title="Ünïcode / Title!", external_id="doi:10.1/y").build()
    assert a["name"] == b["name"] != c["name"]
    assert a["name"].startswith("unicode-title-")
    assert validate_package(a) == []


def test_placeholder_resource_and_warnings():
    b = CkanBuilder("test")
    pkg = b.build()
    assert pkg["resources"] == [{"url": "", "name": "Landing page", "description": "Dataset landing page",
                                 "status": "active"}]
    assert any("no title" in w for w in b.warnings)
    assert validate_package(pkg) == []


def test_validate_catches_problems():
    errs = validate_package({"name": "Bad Name", "title": "", "extras": [{"key": "name", "value": 1}],
                             "tags": [{"name": "a"}], "resources": [], "license_id": "nope"})
    assert len(errs) >= 6


def test_clean_text_and_doi():
    assert clean_text("&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;") == "Hello & bye"
    assert clean_text('<p>See <a href="http://x">here</a></p>') == "See [here](http://x)"
    assert extract_doi("https://doi.org/10.5281/ZENODO.1") == "10.5281/zenodo.1"
    assert extract_doi("doi:10.1594/PANGAEA.932375") == "10.1594/pangaea.932375"
    assert extract_doi("https://example.org/10.1/x") is None

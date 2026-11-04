"""Small inline records for encodings the real samples don't cover."""

import json

import pytest

from metadata_convert import FormatError, metadata_convert


def extras(r):
    return {e["key"]: e["value"] for e in r.package["extras"]}


def test_datacite_kernel3_box_and_point_text():
    xml = """<resource xmlns="http://datacite.org/schema/kernel-3">
      <identifier identifierType="DOI">10.1234/ABC</identifier>
      <creators><creator><creatorName>Doe, Jane</creatorName></creator></creators>
      <titles><title>Kernel 3</title></titles>
      <geoLocations><geoLocation><geoLocationBox>10 20 30 40</geoLocationBox></geoLocation></geoLocations>
    </resource>"""
    r = metadata_convert(xml)
    assert r.source_format == "datacite" and r.external_id == "doi:10.1234/abc"
    geom = json.loads(extras(r)["spatial"])
    assert geom["coordinates"][0][0] == [20.0, 10.0]  # "S W N E" -> west=20, south=10


def test_datacite_json_polygon():
    rec = {"data": {"attributes": {
        "doi": "10.1/p", "titles": [{"title": "Poly"}], "creators": [],
        "geoLocations": [{"geoLocationPolygon": [
            {"polygonPoint": {"pointLongitude": 0, "pointLatitude": 0}},
            {"polygonPoint": {"pointLongitude": 1, "pointLatitude": 0}},
            {"polygonPoint": {"pointLongitude": 1, "pointLatitude": 1}},
            {"inPolygonPoint": {"pointLongitude": 0.5, "pointLatitude": 0.5}}]}],
        "dates": [{"date": "2001-01-01/2002-12-31", "dateType": "Collected"}],
    }}}
    ex = extras(metadata_convert(rec))
    assert json.loads(ex["spatial"])["coordinates"][0][-1] == [0.0, 0.0]
    assert json.loads(ex["temporal"])["endTime"] == "2002-12-31T23:59:59Z"


def test_dublincore_json_and_dcmi_box_period():
    rec = {"dc:title": "DC JSON", "dc:creator": ["A", "B"], "dc:subject": "ocean; ice",
           "dc:identifier": "https://example.org/ds/1",
           "dcterms:spatial": "name=Somewhere; northlimit=10; southlimit=0; eastlimit=5; westlimit=-5",
           "dcterms:temporal": "start=2000-01-01; end=2000-12-31", "dc:rights": "CC-BY-4.0"}
    r = metadata_convert(rec)
    ex = extras(r)
    assert r.source_format == "dublincore"
    assert r.package["license_id"] == "cc-by"
    assert {t["name"] for t in r.package["tags"]} == {"ocean", "ice"}
    assert ex["spatial_place"] == "Somewhere"
    assert json.loads(ex["temporal"])["startTime"] == "2000-01-01T00:00:00Z"
    assert r.package["resources"][0]["url"] == "https://example.org/ds/1"


def test_schemaorg_graph_geoshape_temporal():
    rec = {"@context": "https://schema.org/", "@graph": [
        {"@type": "WebPage", "name": "page"},
        {"@type": "schema:Dataset", "schema:name": "Geo", "description": "d",
         "identifier": {"@type": "PropertyValue", "propertyID": "doi", "value": "doi:10.9/geo"},
         "keywords": "alpha, beta, z",
         "spatialCoverage": {"@type": "Place", "geo": {"@type": "GeoShape", "box": "-10 -20 10 20"}},
         "temporalCoverage": "2010-01-01/..",
         "distribution": [{"@type": "DataDownload", "contentUrl": "https://x.org/f.csv", "encodingFormat": "text/csv"}]}]}
    r = metadata_convert(rec)
    ex = extras(r)
    assert r.source_format == "schemaorg" and r.external_id == "doi:10.9/geo"
    assert {t["name"] for t in r.package["tags"]} == {"alpha", "beta"}  # "z": below CKAN min tag length
    assert json.loads(ex["spatial"])["coordinates"][0][0] == [-20.0, -10.0]
    assert "temporal" not in ex and ex["startDateTime"] == "2010-01-01T00:00:00Z"  # open end
    assert r.package["resources"][0]["format"] == "CSV"


def test_dcat_turtle_wkt_and_period():
    ttl = """@prefix dcat: <http://www.w3.org/ns/dcat#> . @prefix dct: <http://purl.org/dc/terms/> .
    @prefix locn: <http://www.w3.org/ns/locn#> . @prefix vcard: <http://www.w3.org/2006/vcard/ns#> .
    <http://ex.org/ds> a dcat:Dataset ; dct:title "WKT"@en ; dct:identifier "ds-1" ;
      dcat:keyword "k1"@en, "k1-de"@de ;
      dcat:contactPoint [ vcard:fn "Ops" ; vcard:hasEmail <mailto:ops@ex.org> ] ;
      dct:spatial [ locn:geometry "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))" ] ;
      dct:temporal [ dcat:startDate "2001-01-01" ; dcat:endDate "2001-12-31" ] ;
      dcat:distribution [ dcat:downloadURL <http://ex.org/f.json> ; dcat:mediaType <https://www.iana.org/assignments/media-types/application/json> ] ."""
    r = metadata_convert(ttl)
    ex = extras(r)
    assert r.source_format == "dcat" and r.external_id == "ds-1"
    assert [t["name"] for t in r.package["tags"]] == ["k1"]
    assert r.package["maintainer_email"] == "ops@ex.org"
    assert json.loads(ex["spatial"])["type"] == "Polygon"
    assert r.package["resources"][0]["mimetype"] == "application/json"


def test_stac_3d_bbox_and_catalog():
    rec = {"type": "Collection", "stac_version": "1.0.0", "id": "c", "description": "d", "license": "CC-BY-4.0",
           "extent": {"spatial": {"bbox": [[-1, -2, 0, 1, 2, 100]]}, "temporal": {"interval": [["2000-01-01T00:00:00Z", "2001-01-01T00:00:00Z"]]}},
           "links": []}
    r = metadata_convert(rec)
    assert json.loads(extras(r)["spatial"])["coordinates"][0][2] == [1.0, 2.0]
    assert r.package["license_id"] == "cc-by"


def test_forced_format_mismatch_warns():
    rec = {"type": "Collection", "stac_version": "1.0.0", "id": "c", "description": "d", "extent": {}, "links": []}
    assert not any("forced" in w for w in metadata_convert(rec, fmt="stac").warnings)
    r = metadata_convert(rec, fmt="dublincore")
    assert r.source_format == "dublincore"
    assert any("format forced to 'dublincore' but input looks like 'stac'" in w for w in r.warnings)


def test_forced_format_incompatible_with_input():
    with pytest.raises(FormatError, match="iso19115 input must be XML"):
        metadata_convert({"type": "Collection", "stac_version": "1.0.0", "id": "c", "links": []}, fmt="iso19115")


def test_bad_inputs():
    with pytest.raises(FormatError):
        metadata_convert("")
    with pytest.raises(FormatError):
        metadata_convert("{not json")
    with pytest.raises(FormatError):
        metadata_convert({"foo": "bar"})
    with pytest.raises(FormatError):
        metadata_convert({"type": "Collection"}, fmt="nope")


def test_unknown_datacite_xml_element_is_retained():
    xml = """<resource xmlns="http://datacite.org/schema/kernel-4">
      <identifier identifierType="DOI">10.1234/x</identifier><titles><title>T</title></titles>
      <futureElement>kept verbatim</futureElement></resource>"""
    r = metadata_convert(xml)
    assert extras(r)["futureElement"] == "kept verbatim" and r.unmapped == ["futureElement"]

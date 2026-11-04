"""Field-level checks on real sample records, one or two per schema."""

import json
from pathlib import Path

from metadata_convert import metadata_convert

S = Path(__file__).parent / "samples"


def convert(rel):
    return metadata_convert((S / rel).read_bytes())


def extras(pkg) -> dict:
    return {e["key"]: e["value"] for e in pkg["extras"]}


def tags(pkg) -> set:
    return {t["name"] for t in pkg["tags"]}


# --- DataCite --------------------------------------------------------------

def test_datacite_json_zenodo():
    r = convert("datacite/zenodo_7641588.json")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "doi:10.5281/zenodo.7641588"
    assert p["title"].startswith("Newick Trees for EMERGE")
    assert p["license_id"] == "cc-by"
    assert "<em>" not in p["notes"] and "*et al.*" in p["notes"].replace(" *", "*")
    assert ex["doi"] == "10.5281/zenodo.7641588"
    assert ex["source_url"] == "https://doi.org/10.5281/zenodo.7641588"
    assert p["resources"][0]["url"] == "https://zenodo.org/record/7641588"


def test_datacite_json_and_xml_agree():
    for name in ("pangaea_932375", "zenodo_7641588"):
        j = convert(f"datacite/{name}.json").package
        x = convert(f"datacite/{name}.xml").package
        for key in ("name", "title", "author", "license_id", "tags"):
            assert j.get(key) == x.get(key), (name, key)
        assert extras(j).get("spatial") == extras(x).get("spatial")


def test_datacite_pangaea_geo_and_subjects():
    r = convert("datacite/pangaea_932375.json")
    ex = extras(r.package)
    assert json.loads(ex["spatial"]) == {"type": "Point", "coordinates": [6.9422, 46.8123]}
    assert ex["spatial_place"] == "Switzerland"
    assert "temperature-air" in tags(r.package)
    assert "license_id" not in r.package  # custom BSRN license
    assert "Baseline Surface Radiation Network License" in ex["rights_statement"]


# --- Dublin Core ------------------------------------------------------------

def test_dublincore_oai_zenodo():
    r = convert("dublincore/zenodo_7641588.xml")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "doi:10.5281/zenodo.7641588"
    assert p["license_id"] == "cc-by"
    assert ex["access_rights"] == "openAccess"
    assert ex["oai_identifier"] == "oai:zenodo.org:7641588"
    assert "&lt;" not in p["notes"] and "<p>" not in p["notes"]


def test_dublincore_matches_datacite_name():
    # Same record harvested via two schemas -> same CKAN name (stable re-ingest key).
    assert convert("dublincore/zenodo_7641588.xml").package["name"] == convert("datacite/zenodo_7641588.json").package["name"]


# --- schema.org -------------------------------------------------------------

def test_schemaorg_dataverse():
    r = convert("schemaorg/dataverse_TJCLKP.jsonld")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "doi:10.7910/dvn/tjclkp"
    assert p["license_id"] == "cc-zero"
    assert p["version"] == "5"
    assert p["author"] == "Durbin, Philip"
    assert ex["data_catalog"] == "Harvard Dataverse"
    urls = [res["url"] for res in p["resources"]]
    assert any("api/access/datafile" in u for u in urls)
    assert r.unmapped == []


def test_schemaorg_zenodo_distribution():
    p = convert("schemaorg/zenodo_7641588.jsonld").package
    names = [res["name"] for res in p["resources"]]
    assert "Newick_tree_for_methanogen_genome_tree.suptree" in names
    assert names[-1] == "Landing page"


# --- DCAT -------------------------------------------------------------------

def test_dcat_ap_turtle_multilingual():
    r = convert("dcat/europa_bristol-air-quality.ttl")
    p = r.package
    assert p["title"] == "Bristol Air quality"  # English chosen over 24 machine translations
    assert p["notes"].startswith("Air quality is monitored continuously")
    assert p["license_id"] == "uk-ogl"
    assert {res["format"] for res in p["resources"]} == {"CSV"}


def test_dcat_ap_spatial_temporal_contact():
    r = convert("dcat/europa_8a6905b1.ttl")
    p, ex = r.package, extras(r.package)
    assert p["maintainer_email"] == "opendata@marine.ie"
    assert json.loads(ex["spatial"])["type"] == "Polygon"
    assert ex["startDateTime"] == "2003-04-01T00:00:00Z" and "temporal" not in ex  # open-ended
    assert "aquaculture" in tags(p)
    assert "SHP" in {res.get("format") for res in p["resources"]}


def test_dcat_rdfxml_dataset_series():
    r = convert("dcat/europa_936b319d.rdf")
    assert r.package["title"].startswith("AVHRR")
    assert "noaa-14" in tags(r.package)


def test_dcat_us_json():
    r = convert("dcat/dcatus_data_cdc_gov.json")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "https://data.cdc.gov/api/views/2den-c3u2"
    assert p["license_id"] == "other-pd"
    assert p["maintainer_email"] == "VaxView@cdc.gov"
    assert ex["access_level"] == "public"
    assert "temporal_text" in ex
    assert {"CSV", "JSON", "XML"} <= {res.get("format") for res in p["resources"]}


# --- ISO 19115 --------------------------------------------------------------

def test_iso_ncei_oisst():
    r = convert("iso19115/ncei_C00844.xml")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "gov.noaa.ncdc:C00844"
    assert p["title"].startswith("NOAA Optimum Interpolation 1/4 Degree")
    assert p["author"] == "Reynolds, Richard W."
    assert ex["doi"] == "10.7289/v5sq8xb5"
    assert ex["startDateTime"] == "1981-09-01T00:00:00Z" and "endDateTime" not in ex
    assert json.loads(ex["spatial"])["coordinates"][0][0] == [-180.0, -90.0]
    assert "sea-surface-temperature" in tags(p)
    assert not any("ncei.noaa.gov/contact" in res["url"] for res in p["resources"])
    assert "THREDDS" in {res.get("format") for res in p["resources"]}


def test_iso_antimeridian_bbox_split():
    ex = extras(convert("iso19115/ncei_0000698.xml").package)
    geom = json.loads(ex["spatial"])
    assert geom["type"] == "MultiPolygon" and len(geom["coordinates"]) == 2


# --- STAC -------------------------------------------------------------------

def test_stac_collection():
    r = convert("stac/pc_sentinel-2-l2a_collection.json")
    p, ex = r.package, extras(r.package)
    assert r.external_id == "sentinel-2-l2a"
    assert p["title"] == "Sentinel-2 Level-2A"
    assert "license_id" not in p  # "proprietary" = non-SPDX; not mapped to a closed license
    assert ex["license_type"] == "proprietary"
    assert "sentinel" in tags(p)
    assert ex["startDateTime"] == "2015-06-27T10:25:31Z" and "temporal" not in ex
    assert "stac_summaries" in ex


def test_stac_item_assets_become_resources():
    r = convert("stac/pc_sentinel-2-l2a_item.json")
    p, ex = r.package, extras(r.package)
    assert r.external_id.startswith("sentinel-2-l2a/")
    assert json.loads(ex["spatial"])["type"] == "Polygon"
    b02 = next(res for res in p["resources"] if res.get("stac_asset_key") == "B02")
    assert b02["format"] in ("TIFF", "COG") and "eo:bands" in b02
    assert ex["stac:platform"] == "Sentinel-2C"


# --- NDP extras (package_validation.py) --------------------------------------

def test_ndp_fields_dublincore_zenodo():
    ex = extras(convert("dublincore/zenodo_7641588.xml").package)
    assert ex["uploadType"] == "dataset"
    assert ex["issueDate"] == "2023-02-14"
    assert ex["lastUpdateDate"] == "2023-04-26T15:21:36Z"  # OAI datestamp
    assert ex["creatorName"] == "Jared B. Ellenbogen, Mikayla A. Borton, Kelly C. Wrighton"
    assert ex["pocName"] == ex["creatorName"]  # no contact point: falls back to first creator
    assert ex["publisherName"] == "Zenodo"
    assert ex["datasetPageUrl"] == "https://doi.org/10.5281/zenodo.7641588"


def test_ndp_fields_iso_contacts_dates_spatial():
    r = convert("iso19115/ncei_0000698.xml")
    ex = extras(r.package)
    assert ex["issueDate"] == "2002-04-04" and ex["lastUpdateDate"] == "2013-03-18"
    assert ex["pocEmail"] == "ncei.info@noaa.gov"
    assert ex["creatorEmail"] == "menard@meds-sdmm.dfo-mpo.gc.ca"
    assert ex["spatialCovFormat"] == "geojson" and json.loads(ex["spatialCov"])["type"] == "MultiPolygon"
    assert ex["dataBboxFormat"] == "geojson" and json.loads(ex["dataBbox"])["type"] == "Polygon"
    assert ex["temporal"] == '{"startTime": "2002-02-27T00:00:00Z", "endTime": "2002-04-29T23:59:59Z"}'
    assert ex["startDateTime"] == "2002-02-27T00:00:00Z" and ex["endDateTime"] == "2002-04-29T23:59:59Z"


def test_ndp_fields_dcat_publisher_and_docs():
    ex = extras(convert("dcat/europa_8a6905b1.ttl").package)
    assert ex["publisherName"] == "Marine Institute"
    assert ex["publisherWebsite"] == "https://data.gov.ie/organization/marine-institute"
    assert ex["pocEmail"] == "opendata@marine.ie"


def test_ndp_fields_stac_resolution_and_mimetype():
    r = convert("stac/pc_sentinel-2-l2a_collection.json")
    ex = extras(r.package)
    assert ex["spatialRes"] == "10 meter"
    assert ex["publisherName"] == "Microsoft"
    assert ex["datasetPageUrl"] == "https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a"
    item = convert("stac/pc_sentinel-2-l2a_item.json").package
    b02 = next(res for res in item["resources"] if res.get("stac_asset_key") == "B02")
    assert b02["mimetype"] == "image/tiff" and "profile=cloud-optimized" in b02["media_type"]
    assert all(res["status"] == "active" and res["description"] for res in item["resources"])


def test_ndp_point_has_no_bbox():
    ex = extras(convert("datacite/pangaea_932375.json").package)
    assert ex["spatialCovFormat"] == "geojson" and "dataBbox" not in ex  # a point is not a bounding box


# --- source fields without a mapping are kept under their dotted source path -------

def test_retained_iso_fields():
    r = convert("iso19115/ncei_C00844.xml")
    ex = extras(r.package)
    assert ex["contentInfo.dimension.sequenceIdentifier.aName"].startswith("time; zlev; lat; lon; sst")
    assert "dataQualityInfo.lineage.source.sourceCitation.title" in r.unmapped
    assert not any(k.endswith((".facsimile", ".dateType")) or "thesaurusName" in k for k in ex)
    assert set(r.unmapped) <= set(ex)


def test_retained_stac_and_rdf_fields():
    ex = extras(convert("stac/pc_sentinel-2-l2a_collection.json").package)
    assert ex["msft:region"] == "westeurope"
    assert ex["storage:schemes.azure.type"] == "ms-azure"
    ex = extras(convert("dcat/europa_936b319d.rdf").package)
    assert ex["geodcatap:topicCategory"].endswith("/environment")
    assert json.loads(ex["prov:wasUsedBy"])["prov:generated"]["dct:description"] == "See the referenced specification"

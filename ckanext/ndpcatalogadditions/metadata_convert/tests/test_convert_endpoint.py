"""Tests for convert.py's convert_metadata() - the Flask-independent logic
behind /ndp/metadata_convert. Flask glue (auth, request parsing) is exercised
separately via live_smoke_test.py against a real deployment, since CKAN/Flask
aren't installed in this standalone dev venv.
"""

import json
from pathlib import Path

import pytest

from metadata_convert.convert import convert_metadata
from metadata_convert.detect import FormatError

SAMPLES_DIR = Path(__file__).parent / "samples"


def _read(fmt_dir, name):
    return (SAMPLES_DIR / fmt_dir / name).read_text()


def test_auto_detect_and_external_id():
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    out = convert_metadata(content)
    assert out["source_format"] == "stac"
    assert out["external_id"]
    assert out["ckan_json"]["title"]


def test_forced_format():
    content = _read("datacite", "zenodo_7641588.json")
    out = convert_metadata(content, source_format="datacite")
    assert out["source_format"] == "datacite"


def test_bad_forced_format_raises_formaterror():
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    with pytest.raises(FormatError):
        convert_metadata(content, source_format="iso19115")


def test_owner_org_and_private_passthrough():
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    out = convert_metadata(content, owner_org="my-org", private=False)
    assert out["ckan_json"]["owner_org"] == "my-org"
    assert out["ckan_json"]["private"] is False


def test_private_defaults_true():
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    out = convert_metadata(content)
    assert out["ckan_json"]["private"] is True


def test_provenance_with_metadata_url():
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    url = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/x/items/y"
    out = convert_metadata(content, metadata_url=url)
    prov = [r for r in out["ckan_json"]["resources"] if r["url"] == url]
    assert len(prov) == 1
    assert prov[0]["name"].startswith("Original stac source record")
    extras = {e["key"]: e["value"] for e in out["ckan_json"]["extras"]}
    assert extras["sourceMetadataUrl"] == url
    assert extras["sourceFormat"] == "stac"


def test_provenance_inline_only_uses_extras_not_resource_url():
    # Real constraint found via a live test: resource.url has its own btree
    # index capped at ~2.7KB/row, so a data: URI embedding a whole record
    # there broke package_create on anything beyond a trivial record. The
    # original must live in extras (plain, unbounded TEXT, no such index).
    content = _read("stac", "pc_sentinel-2-l2a_item.json")
    out = convert_metadata(content)  # no metadata_url
    prov = [r for r in out["ckan_json"]["resources"] if r["name"].startswith("Original stac")]
    assert len(prov) == 1
    assert prov[0]["url"] == ""  # CKAN's own "no URL available" convention, never a data: URI
    extras = {e["key"]: e["value"] for e in out["ckan_json"]["extras"]}
    assert extras["sourceMetadataContent"] == content
    assert "sourceMetadataUrl" not in extras
    # an empty resource url is package_validation.py's own established
    # "no url to check" convention - must never be flagged as invalid
    assert not any("url" in w.lower() for w in out["warnings"] if w.startswith("ndp_validation:"))


def test_large_inline_content_fits_in_extras():
    # A STAC item padded with a large unmapped property, still valid JSON -
    # this used to trigger a size warning when the fallback was a data: URI;
    # now it's just an ordinary extras value, nothing special to flag.
    base = json.loads(_read("stac", "pc_sentinel-2-l2a_item.json"))
    base.setdefault("properties", {})["padding"] = "x" * 150_000
    content = json.dumps(base)
    out = convert_metadata(content)
    extras = {e["key"]: e["value"] for e in out["ckan_json"]["extras"]}
    assert extras["sourceMetadataContent"] == content


def test_ndp_validation_warnings_are_tagged():
    # Dublin Core has no contact-person concept, so pocEmail is always missing
    # for a public (non-private) dataset - this should show up as a tagged
    # ndp_validation warning, not a hard error, and never block the response.
    xml = """<oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                       xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>T</dc:title><dc:description>d</dc:description>
      <dc:identifier>https://example.org/x</dc:identifier></oai_dc:dc>"""
    out = convert_metadata(xml, source_format="dublincore", private=False)
    assert any(w.startswith("ndp_validation:") for w in out["warnings"])
    assert out["ckan_json"]["title"] == "T"

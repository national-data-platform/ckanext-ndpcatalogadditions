"""Every sample passes NDP's package_validation.py except for data the source genuinely lacks."""

import json
import re
from pathlib import Path

import pytest

from metadata_convert import metadata_convert
from package_validation import ndp_package_validate

SAMPLES = sorted(p for p in (Path(__file__).parent / "samples").glob("*/*") if p.is_file())

# Errors that only mean "the source record has no such information" (or, for publisherEmail,
# that no owner_org has been resolved yet). Everything else is a converter bug.
ALLOWED = re.compile(
    r"Missing required field for public dataset: (extras:(pocEmail|pocName|publisherEmail|lastUpdateDate)|tags"
    r"|resource\[\d+\]:mimetype)"
    r"|Missing required field: notes"
)


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_ndp_validation(path):
    r = metadata_convert(path.read_bytes())
    errors = ndp_package_validate(json.dumps(r.package), check_urls=False)["errors"]
    assert [e for e in errors if not ALLOWED.search(e)] == []


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_ndp_required_fields_present(path):
    ex = {e["key"]: e["value"] for e in metadata_convert(path.read_bytes()).package["extras"]}
    assert ex["uploadType"] in ("dataset", "model", "service", "docker-image", "application", "code")
    assert ex.get("datasetPageUrl")

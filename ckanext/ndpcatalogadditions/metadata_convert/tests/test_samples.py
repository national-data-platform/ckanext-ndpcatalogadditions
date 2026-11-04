"""Every real sample record converts cleanly and matches its reviewed golden output.

Regenerate golden files after an intended mapping change with:
    UPDATE_GOLDEN=1 python -m pytest tests/test_samples.py
then review the diff (git diff tests/expected) before committing.
"""

import json
import os
from pathlib import Path

import pytest

from metadata_convert import metadata_convert
from metadata_convert.detect import detect, load

HERE = Path(__file__).parent
SAMPLES = sorted(p for p in (HERE / "samples").glob("*/*") if p.is_file())
EXPECTED = HERE / "expected"


def _id(p: Path) -> str:
    return f"{p.parent.name}/{p.name}"


@pytest.mark.parametrize("path", SAMPLES, ids=_id)
def test_detects_format(path):
    assert detect(load(path.read_bytes())) == path.parent.name


@pytest.mark.parametrize("path", SAMPLES, ids=_id)
def test_converts_without_errors(path):
    r = metadata_convert(path.read_bytes())
    assert r.errors == []
    assert r.external_id, "every sample has a stable external id"
    assert r.package["title"] and not r.package["title"].startswith("Untitled")
    assert r.package["resources"]
    assert r.original == path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("path", SAMPLES, ids=_id)
def test_idempotent(path):
    a = metadata_convert(path.read_bytes())
    b = metadata_convert(path.read_bytes())
    assert a.package == b.package
    assert a.package["name"] == b.package["name"]


@pytest.mark.parametrize("path", SAMPLES, ids=_id)
def test_golden(path):
    r = metadata_convert(path.read_bytes())
    got = r.to_dict()
    golden = EXPECTED / path.parent.name / (path.name + ".ckan.json")
    if os.environ.get("UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert golden.exists(), f"missing golden file {golden}; run with UPDATE_GOLDEN=1"
    assert got == json.loads(golden.read_text(encoding="utf-8"))

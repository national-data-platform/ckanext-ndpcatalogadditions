"""
Manual end-to-end check for prestac_migration.py.

Creates a dummy collection + item directly in preSTAC, runs
move_prestac_collection_to_stac() against it, verifies the copy landed in
production STAC (with rewritten links/hrefs) and that the preSTAC originals
were deleted, then cleans up the copy from production STAC.

Usage:
    python test_prestac_migration.py
"""

import sys
import time

import requests

from prestac_migration import (
    PRESTAC_URL,
    STAC_URL,
    STAC_API_KEY,
    move_prestac_collection_to_stac,
)

WRITE_HEADERS = {'Content-Type': 'application/json', 'X-API-Key': STAC_API_KEY}

COLLECTION_ID = "test-prestac-migration-collection"
ITEM_ID = "test-prestac-migration-item"


def make_dummy_collection():
    return {
        "stac_version": "1.0.0",
        "type": "Collection",
        "id": COLLECTION_ID,
        "title": "Test preSTAC migration collection",
        "description": "Dummy collection used to validate prestac_migration.py",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [
            {"rel": "root", "href": f"{PRESTAC_URL}/", "type": "application/json"},
            {"rel": "self", "href": f"{PRESTAC_URL}/collections/{COLLECTION_ID}", "type": "application/json"},
        ],
    }


def make_dummy_item():
    return {
        "stac_version": "1.0.0",
        "type": "Feature",
        "id": ITEM_ID,
        "collection": COLLECTION_ID,
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "bbox": [0, 0, 0, 0],
        "properties": {"datetime": "2026-01-01T00:00:00Z"},
        "assets": {
            "data": {
                "href": f"{PRESTAC_URL}/collections/{COLLECTION_ID}/items/{ITEM_ID}/dummy.tif",
                "type": "image/tiff",
            }
        },
        "links": [
            {"rel": "root", "href": f"{PRESTAC_URL}/", "type": "application/json"},
            {
                "rel": "self",
                "href": f"{PRESTAC_URL}/collections/{COLLECTION_ID}/items/{ITEM_ID}",
                "type": "application/json",
            },
            {
                "rel": "collection",
                "href": f"{PRESTAC_URL}/collections/{COLLECTION_ID}",
                "type": "application/json",
            },
        ],
    }


def setup_prestac_fixture():
    print(f"Creating dummy collection '{COLLECTION_ID}' in preSTAC...")
    resp = requests.post(f"{PRESTAC_URL}/collections", json=make_dummy_collection(), headers=WRITE_HEADERS)
    assert resp.status_code in (200, 201, 409), f"collection create failed: {resp.status_code} {resp.text}"

    print(f"Creating dummy item '{ITEM_ID}' in preSTAC...")
    resp = requests.post(
        f"{PRESTAC_URL}/collections/{COLLECTION_ID}/items", json=make_dummy_item(), headers=WRITE_HEADERS
    )
    assert resp.status_code in (200, 201, 409), f"item create failed: {resp.status_code} {resp.text}"


def _get_with_retry(url, attempts=6, delay=3):
    """This deployment shows brief read-after-write lag - a GET right after a
    POST can 404 for a few seconds. Retry before treating it as a real failure."""
    resp = None
    for attempt in range(attempts):
        resp = requests.get(url)
        if resp.status_code == 200:
            return resp
        if attempt < attempts - 1:
            time.sleep(delay)
    return resp


def verify_copied_to_stac():
    resp = _get_with_retry(f"{STAC_URL}/collections/{COLLECTION_ID}")
    assert resp.status_code == 200, f"collection missing in production STAC: {resp.status_code} {resp.text}"
    collection = resp.json()
    for link in collection.get('links', []):
        assert not link['href'].startswith(PRESTAC_URL), f"link still points at preSTAC: {link}"

    resp = _get_with_retry(f"{STAC_URL}/collections/{COLLECTION_ID}/items/{ITEM_ID}")
    assert resp.status_code == 200, f"item missing in production STAC: {resp.status_code} {resp.text}"
    item = resp.json()
    for link in item.get('links', []):
        assert not link['href'].startswith(PRESTAC_URL), f"link still points at preSTAC: {link}"
    for asset in item.get('assets', {}).values():
        assert not asset['href'].startswith(PRESTAC_URL), f"asset href still points at preSTAC: {asset}"

    print("Verified: collection + item present in production STAC with rewritten links.")


def _get_until_404(url, attempts=6, delay=2):
    resp = None
    for attempt in range(attempts):
        resp = requests.get(url)
        if resp.status_code == 404:
            return resp
        if attempt < attempts - 1:
            time.sleep(delay)
    return resp


def verify_prestac_deleted():
    resp = _get_until_404(f"{PRESTAC_URL}/collections/{COLLECTION_ID}/items/{ITEM_ID}")
    assert resp.status_code == 404, f"preSTAC item should have been deleted: {resp.status_code}"
    resp = _get_until_404(f"{PRESTAC_URL}/collections/{COLLECTION_ID}")
    assert resp.status_code == 404, f"preSTAC collection should have been deleted: {resp.status_code}"
    print("Verified: preSTAC originals deleted after the move.")


def cleanup_production_stac_copy():
    print("Cleaning up copied item/collection from production STAC...")
    requests.delete(f"{STAC_URL}/collections/{COLLECTION_ID}/items/{ITEM_ID}", headers=WRITE_HEADERS)
    requests.delete(f"{STAC_URL}/collections/{COLLECTION_ID}", headers=WRITE_HEADERS)


def main():
    setup_prestac_fixture()

    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("test_prestac_migration")

    print(f"Moving collection '{COLLECTION_ID}' from preSTAC to production STAC...")
    success = move_prestac_collection_to_stac(COLLECTION_ID, logger)
    assert success, "move_prestac_collection_to_stac() reported failure"

    verify_copied_to_stac()
    verify_prestac_deleted()
    cleanup_production_stac_copy()

    print("\nAll checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)

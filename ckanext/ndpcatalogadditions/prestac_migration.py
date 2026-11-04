"""
Copy STAC collections referenced by Prekan dataset resources from preSTAC
(staging) into production STAC when the dataset is approved.

preSTAC and production STAC are separate stac-fastapi-pgstac instances
fronted by the same nginx host:
    preSTAC:     https://ndp-test.sdsc.edu/prestac
    production:  https://ndp-test.sdsc.edu/stac

A dataset resource may point at a collection or a specific item in preSTAC,
e.g.:
    https://ndp-test.sdsc.edu/prestac/collections/{collection_id}
    https://ndp-test.sdsc.edu/prestac/collections/{collection_id}/items/{item_id}

On approval we copy the whole referenced collection (and all its items) into
production STAC, rewrite the resource URL to point at production STAC, and then
delete the collection (and its items) from preSTAC - a true move, per manager
decision 2026-07-07. The preSTAC entry is only deleted after the copy to
production STAC has succeeded, so a failed copy never loses data.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

STAC_URL = "https://ndp-test.sdsc.edu/stac"
PRESTAC_URL = "https://ndp-test.sdsc.edu/prestac"
STAC_API_KEY = "replaceDIMMsoon"  # TODO: move to environment variable

_WRITE_HEADERS = {'Content-Type': 'application/json', 'X-API-Key': STAC_API_KEY}

# Matches a resource URL pointing into preSTAC, at either collection level
# (.../collections/{id}) or item level (.../collections/{id}/items/{id})
_PRESTAC_RESOURCE_RE = re.compile(
    re.escape(PRESTAC_URL.rstrip('/')) + r"/collections/([^/?]+)(?:/items/([^/?]+))?/?$"
)


def _rewrite_stac_refs(obj: Dict[str, Any]) -> None:
    """Rewrite any links/asset hrefs that point at PRESTAC_URL to point at STAC_URL."""
    for link in obj.get('links', []) or []:
        href = link.get('href')
        if href and href.startswith(PRESTAC_URL):
            link['href'] = STAC_URL + href[len(PRESTAC_URL):]

    for asset in (obj.get('assets') or {}).values():
        href = asset.get('href')
        if href and href.startswith(PRESTAC_URL):
            asset['href'] = STAC_URL + href[len(PRESTAC_URL):]


def _fetch_prestac_collection_and_items(
    collection_id: str,
    logger: Any = None
) -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """
    Fetch a collection and all of its items from preSTAC.

    Returns (collection, items) or None if the collection doesn't exist in preSTAC.
    """
    response = requests.get(f"{PRESTAC_URL}/collections/{collection_id}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    collection = response.json()

    items: List[Dict[str, Any]] = []
    url = f"{PRESTAC_URL}/collections/{collection_id}/items"
    params = {'limit': 250}
    while url:
        response = requests.get(url, params=params)
        response.raise_for_status()
        page = response.json()
        items.extend(page.get('features', []))
        url = next((l['href'] for l in page.get('links', []) if l.get('rel') == 'next'), None)
        params = None  # next link already carries its own query params

    if logger:
        logger.info(f"Fetched preSTAC collection '{collection_id}' with {len(items)} item(s)")

    return collection, items


def _delete_prestac_collection(
    collection_id: str,
    items: List[Dict[str, Any]],
    logger: Any = None
) -> None:
    """
    Delete a collection and all its items from preSTAC. Best-effort: logs
    warnings on failure but never raises, since the copy to production STAC
    has already succeeded by the time this is called - a failed cleanup here
    just leaves a stale preSTAC entry behind, it doesn't lose any data.
    """
    for item in items:
        item_id = item.get('id')
        response = requests.delete(
            f"{PRESTAC_URL}/collections/{collection_id}/items/{item_id}",
            headers=_WRITE_HEADERS
        )
        if response.status_code not in (200, 202, 404) and logger:
            logger.warning(
                f"Failed to delete preSTAC item '{item_id}' in collection "
                f"'{collection_id}': {response.status_code} {response.text}"
            )

    response = requests.delete(f"{PRESTAC_URL}/collections/{collection_id}", headers=_WRITE_HEADERS)
    if response.status_code not in (200, 202, 404) and logger:
        logger.warning(
            f"Failed to delete preSTAC collection '{collection_id}': "
            f"{response.status_code} {response.text}"
        )


def move_prestac_collection_to_stac(collection_id: str, logger: Any = None) -> bool:
    """
    Move a STAC collection (and all its items) from preSTAC to production STAC:
    copies it to production STAC first, and only deletes the preSTAC original
    once that copy has succeeded.

    Returns True if the collection is present in production STAC by the end
    (whether newly created or already existing there), False on unrecoverable error.
    """
    fetched = _fetch_prestac_collection_and_items(collection_id, logger)
    if fetched is None:
        if logger:
            logger.warning(f"preSTAC collection '{collection_id}' not found, cannot migrate")
        return False

    collection, items = fetched
    _rewrite_stac_refs(collection)

    response = requests.post(f"{STAC_URL}/collections", json=collection, headers=_WRITE_HEADERS)
    if response.status_code not in (200, 201, 409):
        if logger:
            logger.warning(
                f"Failed to create STAC collection '{collection_id}': "
                f"{response.status_code} {response.text}"
            )
        return False

    for item in items:
        _rewrite_stac_refs(item)
        item['collection'] = collection_id

        item_response = requests.post(
            f"{STAC_URL}/collections/{collection_id}/items",
            json=item,
            headers=_WRITE_HEADERS
        )
        if item_response.status_code not in (200, 201, 409) and logger:
            logger.warning(
                f"Failed to copy STAC item '{item.get('id')}' in collection "
                f"'{collection_id}': {item_response.status_code} {item_response.text}"
            )

    if logger:
        logger.info(
            f"Copied preSTAC collection '{collection_id}' ({len(items)} item(s)) "
            f"to production STAC"
        )

    _delete_prestac_collection(collection_id, items, logger)

    return True


def migrate_prestac_resources(dataset: Dict[str, Any], logger: Any = None) -> None:
    """
    Scan a CKAN package's resources for URLs pointing into preSTAC, move the
    referenced collections (and their items) to production STAC, and rewrite
    those resource URLs to point at production STAC instead.

    Mutates dataset['resources'] in place. Never raises - a failed move just
    leaves the resource URL pointing at preSTAC (which still resolves, since
    nothing is deleted from preSTAC unless the copy to production STAC already
    succeeded), so it never blocks dataset approval.
    """
    resources = dataset.get('resources') or []
    moved: Dict[str, bool] = {}

    for resource in resources:
        url = resource.get('url') or ''
        match = _PRESTAC_RESOURCE_RE.match(url)
        if not match:
            continue

        collection_id = match.group(1)

        if collection_id not in moved:
            try:
                moved[collection_id] = move_prestac_collection_to_stac(collection_id, logger)
            except Exception as e:
                if logger:
                    logger.error(f"Error moving preSTAC collection '{collection_id}': {e}")
                moved[collection_id] = False

        if moved[collection_id]:
            resource['url'] = STAC_URL + url[len(PRESTAC_URL):]
        elif logger:
            logger.warning(f"Leaving resource url unchanged (preSTAC move failed): {url}")

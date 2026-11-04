"""POST /ndp/metadata_convert - Flask view.

Converts one metadata record (DataCite, Dublin Core, schema.org Dataset,
DCAT/DCAT-AP, ISO 19115 or STAC) into NDP's CKAN package JSON, using the
vendored, deterministic `metadata_convert` library in this folder (see
__init__.py's docstring).

By default this also creates the converted package in PREKAN - via
prekan_create.create_in_prekan(), a direct in-process call (see that module's
docstring for why this isn't an HTTP call to /ndp/package_create). Pass
dry_run: true to just get the mapped JSON, warnings and unmapped fields back
without creating anything.
"""

import json
import logging
import traceback

from flask import request

from ckanext.ndpcatalogadditions.package_validation import ndp_package_validate

from .core import metadata_convert as run_metadata_convert
from .detect import FORMATS, FormatError
from .fetch import fetch_metadata_content

logger = logging.getLogger(__name__)

_CONTENT_TYPE_BY_FORMAT = {
    # (sniffed first char -> (mimetype, resource format label))
    "datacite": {"{": ("application/json", "JSON"), "<": ("application/xml", "XML")},
    "dublincore": {"{": ("application/json", "JSON"), "<": ("application/xml", "XML")},
    "schemaorg": {"{": ("application/ld+json", "JSON-LD")},
    "dcat": {"{": ("application/ld+json", "JSON-LD"), "<": ("application/rdf+xml", "RDF-XML")},
    "iso19115": {"<": ("application/xml", "XML")},
    "stac": {"{": ("application/json", "JSON")},
}
_DEFAULT_CONTENT_TYPE = ("text/turtle", "TTL")  # dcat's non-JSON, non-XML case


def _guess_content_type(source_format: str, text: str) -> tuple:
    by_first_char = _CONTENT_TYPE_BY_FORMAT.get(source_format, {})
    first = text.lstrip()[:1]
    return by_first_char.get(first, _DEFAULT_CONTENT_TYPE)


def _add_provenance(package: dict, source_format: str, content: str, metadata_url, warnings: list):
    """Attach the full original record so it's kept alongside the mapped package,
    per the ticket's "original source metadata kept alongside the mapped record"
    requirement.

    Always runs, not just when metadata_url is given. Where the original is
    stored differs by case, because of a real CKAN/Postgres constraint found
    via a live test: resource.url has its own btree index
    (idx_package_resource_url) capped at ~2.7KB per row, so embedding a whole
    metadata record as a data: URI in a resource's url broke package_create
    outright on anything beyond a trivial record (a real STAC item's ~20KB
    JSON blew past it by 2.5x). package_extra.value has no such index - it's
    a plain, unbounded TEXT column - so that's where the raw content actually
    lives; a resource is still added either way, just for visibility, never
    carrying the payload itself:
      - metadata_url given: the resource's url is that real URL (short,
        fine for the index) - this is the common, cheap case.
      - inline content only: extras.sourceMetadataContent holds the raw
        content verbatim, and the resource gets url="" (CKAN's own
        established convention here for "no URL available") plus a
        description pointing at that extras key.
    """
    mimetype, fmt_label = _guess_content_type(source_format, content)
    extras = package.setdefault("extras", [])
    extras.append({"key": "sourceFormat", "value": source_format})

    if metadata_url:
        resource = {
            "name": f"Original {source_format} source record",
            "description": f"Original source metadata record ({source_format}) that this "
                            f"CKAN package was converted from by metadata_convert.",
            "url": metadata_url,
            "mimetype": mimetype,
            "format": fmt_label,
            "status": "active",
        }
        extras.append({"key": "sourceMetadataUrl", "value": metadata_url})
    else:
        extras.append({"key": "sourceMetadataContent", "value": content})
        resource = {
            "name": f"Original {source_format} source record",
            "description": f"Original source metadata record ({source_format}) that this CKAN "
                            f"package was converted from by metadata_convert - no metadata_url was "
                            f"given, so the raw content is stored in extras:sourceMetadataContent "
                            f"instead (a resource's url can't hold an arbitrarily large value).",
            "url": "",
            "status": "active",
        }

    package.setdefault("resources", []).append(resource)


def convert_metadata(content: str, source_format: str = "auto", metadata_url: str = None,
                      owner_org: str = None, private: bool = True) -> dict:
    """The actual conversion + wrapping logic, kept separate from Flask glue so
    it's unit-testable without a request context.

    Returns the full response payload (never raises FormatError/ValueError -
    those are the caller's job to catch and turn into a 400).
    """
    result = run_metadata_convert(content, source_format)
    package = result.package

    if owner_org:
        package["owner_org"] = owner_org
    # Safe default: a freshly-converted, not-yet-reviewed record stays private
    # until a human promotes it, same spirit as CKAN's own default.
    package["private"] = bool(private)

    warnings = list(result.warnings)
    _add_provenance(package, result.source_format, content, metadata_url, warnings)

    # Soft validation: never raises, never blocks - just keeps this endpoint's
    # notion of "valid NDP package" in sync with package_validation.py as that
    # file evolves, instead of duplicating its rules here.
    ndp_result = ndp_package_validate(json.dumps(package), check_urls=False)
    for err in ndp_result.get("errors", []):
        warnings.append(f"ndp_validation: {err}")

    return {
        "ckan_json": ndp_result.get("package", package),
        "source_format": result.source_format,
        "external_id": result.external_id,
        "warnings": warnings,
        "unmapped_fields": result.unmapped,
        "errors": result.errors,
    }


def metadata_convert_view():
    if request.method != 'POST':
        return "Method not allowed", 405

    # Imported lazily (not at module level, unlike package_validation above):
    # keycloak_token.py pulls in `ckan.plugins`, a real CKAN runtime dependency
    # that isn't needed just to run the conversion logic, and keeping it out of
    # convert.py's module-level imports lets convert_metadata() be unit-tested
    # outside a full CKAN environment.
    from ckanext.ndpcatalogadditions.keycloak_token import get_user_info

    auth_header = request.headers.get('Authorization', '')
    token = auth_header.split('Bearer ', 1)[-1].strip() if 'Bearer ' in auth_header else None
    if not token or not get_user_info(token):
        return "Not authorized: missing or invalid bearer token", 401

    try:
        body = request.get_json()
        if not isinstance(body, dict):
            return "Error: request body must be a JSON object", 400

        source_format = body.get('source_format') or 'auto'
        if source_format != 'auto' and source_format not in FORMATS:
            return f"Error: source_format must be 'auto' or one of {', '.join(FORMATS)}", 400

        metadata_url = body.get('metadata_url')
        content = body.get('content')
        if content is None and not metadata_url:
            return "Error: either 'content' or 'metadata_url' is required", 400

        if content is None:
            try:
                content = fetch_metadata_content(metadata_url)
            except ValueError as e:
                return f"Error: {e}", 400
        elif not isinstance(content, str):
            content = json.dumps(content)

        payload = convert_metadata(
            content, source_format,
            metadata_url=metadata_url,
            owner_org=body.get('owner_org'),
            private=body.get('private', True),
        )

        # Dry-run is opt-in (ticket's own wording: a "dry-run option"), so the
        # baseline behavior of a plain call is to actually register the
        # dataset in PREKAN - matching "transforms it ... and creates it in
        # PREKAN" as one action.
        dry_run = bool(body.get('dry_run', False))
        payload['dry_run'] = dry_run
        if not dry_run:
            from .prekan_create import create_in_prekan
            payload.update(create_in_prekan(payload['ckan_json']))

        return payload

    except FormatError as e:
        return f"Error: {e}", 400
    except Exception as e:
        traceback.print_exc()
        return f'Error: {str(e)}', 400

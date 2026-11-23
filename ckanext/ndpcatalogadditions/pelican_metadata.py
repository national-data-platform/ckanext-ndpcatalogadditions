"""
Pelican object-commit metadata publish receiver.

Implements the catalog side of Pelican's webhook contract (see
docs/metadata-publish-design.md in the Pelican repo, pinned at commit
6fac86f061a0c816d07fb4d41e7bebc3fbcf6897) and the NDP-specific design
decisions recorded in docs/pelican-metadata-publish-design.md in this repo.

v1 scope: object.committed only, json body only. See that doc for why
object.updated / object.deleted and the multipart/blob variant are deferred.
"""

import json
import logging
import mimetypes
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import sqlalchemy as sa

import ckan.logic as logic
import ckan.model as model
from ckan.lib.munge import munge_title_to_name
from jose import jwt
from jose.exceptions import JWTError
from flask import request

from ckanext.ndpcatalogadditions import controller
from ckanext.ndpcatalogadditions.controller import (
    add_or_update_ndp_creator_md5,
    calculate_md5,
    generate_random_password,
    process_user_and_organization,
)
from ckanext.ndpcatalogadditions.package_validation import ndp_package_validate

import requests

logger = logging.getLogger(__name__)

# Reserved keys Pelican itself writes onto event["object"]; everything else
# in that dict is uploader-supplied custom metadata. See design doc 3.4.
PELICAN_RESERVED_OBJECT_KEYS = {"path", "size", "etag", "created_at"}

# Our own protocol keys, layered on top of Pelican's custom-field mechanism.
# "dataset" picks the package within the namespace's org; "expected_count"
# is the completion signal. Neither is a CKAN package field.
DATASET_FIELD_KEY = "dataset"
EXPECTED_COUNT_FIELD_KEY = "expected_count"

# Resource-level keys: they describe the file's resource, not the dataset,
# so they are never merged into the package. "resource" groups files into
# one logical-unit resource (a folder); see design doc 3.6 and section 6.
RESOURCE_GROUP_KEY = "resource"
RESOURCE_FORMAT_KEY = "format"
RESOURCE_DESCRIPTION_KEY = "resource_description"
RESOURCE_FIELD_KEYS = {RESOURCE_GROUP_KEY, RESOURCE_FORMAT_KEY, RESOURCE_DESCRIPTION_KEY}

# A grouped resource points at a folder, not a file.
FOLDER_MIMETYPE = "inode/directory"

# How long an event waits for another event on the same dataset before
# giving up with a 503 (Pelican retries). Kept short: a waiting request
# holds a web worker, and Pelican's own per-attempt timeout is 10s.
DATASET_LOCK_WAIT_SECONDS = 5

# Custom fields that map onto top-level CKAN package fields rather than
# becoming extras. These are the only CKAN core fields an uploader may set;
# the rest (name, owner_org, private, state, maintainer, version, ...) are
# protected - see is_protected_field. Values go through CKAN's own
# validators, so e.g. a malformed author_email is a ValidationError (422).
TOP_LEVEL_PACKAGE_FIELDS = {"title", "notes", "tags", "license_id", "author", "author_email"}

# Pelican carries uploader metadata in its X-Pelican-Object-Metadata header,
# an RFC 8941 structured-field dictionary, and the client refuses any key
# that isn't lowercase (SFV_KEY_PATTERN). NDP's own extras are camelCase,
# so uploaders send these snake_case spellings and the receiver stores them
# under NDP's names. The camelCase forms are still accepted if they ever
# arrive. See design doc section 6.
SFV_KEY_PATTERN = re.compile(r"^[a-z*][a-z0-9_\-.*]*$")
NDP_EXTRA_ALIASES = {
    "upload_type": "uploadType",
    "last_update_date": "lastUpdateDate",
    "poc_name": "pocName",
    "poc_email": "pocEmail",
    "poc_website": "pocWebsite",
    "publisher_email": "publisherEmail",
    "publisher_website": "publisherWebsite",
    "creator_email": "creatorEmail",
    "creator_website": "creatorWebsite",
    "docs_url": "docsURL",
    "dataset_page_url": "datasetPageUrl",
    "spatial_cov": "spatialCov",
    "spatial_cov_format": "spatialCovFormat",
    "data_bbox": "dataBbox",
    "data_bbox_format": "dataBboxFormat",
    "spatial_res": "spatialRes",
    "start_date_time": "startDateTime",
    "end_date_time": "endDateTime",
}

# Extras with meaning elsewhere in this extension: ownership fingerprint,
# review state, restricted-access groups. Never settable from an event.
PROTECTED_EXTRA_KEYS = {"ndp_creator_md5", "approval_status", "existingGroupIds"}

SERVICE_USER_NAME = os.getenv(
    "CKANEXT__NDPCATALOGADDITIONS__PELICAN_SERVICE_USER", "pelican-ingest-bot"
)
SERVICE_USER_EMAIL = os.getenv(
    "CKANEXT__NDPCATALOGADDITIONS__PELICAN_SERVICE_USER_EMAIL",
    "pelican-ingest-bot@nationaldataplatform.org",
)
# Fixed, not derived per upload - there is no per-upload identity. See
# design doc 3.8.
SERVICE_USER_NDP_CREATOR_MD5 = calculate_md5(SERVICE_USER_NAME)

TRUSTED_FEDERATION = os.getenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_TRUSTED_FEDERATION")
WEBHOOK_URL = os.getenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_WEBHOOK_URL")

REGISTRY_CACHE_TTL = 3600
JWKS_CACHE_TTL = 600

_registry_cache = {}
_jwks_cache = {}

NAMESPACE_PATTERN = re.compile(r"^/[A-Za-z0-9._\-/]*$")


class PelicanAuthError(Exception):
    """Event failed authentication/verification - maps to a 401 response."""


class PelicanRejectError(Exception):
    """Event is permanently unprocessable - maps to a 422 response."""


class PelicanBusyError(Exception):
    """Another event for the same dataset holds the lock - maps to 503, retried."""


def metadata_value_to_text(value):
    """
    Pelican allows string, number and boolean metadata values (its
    --metadata-file help and WithObjectMetadata); CKAN fields and extras
    are text. Booleans use JSON spelling.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return value


def has_bad_segments(path):
    """
    True if a slash-rooted path has empty, "." or ".." segments. These end up
    in resource urls and registry lookups, and could step outside the
    namespace the token vouches for.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return True
    return any(segment in ("", ".", "..") for segment in path.split("/")[1:])


def get_namespace_org_map():
    raw = os.getenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP is not valid json")
        return {}


def discover_registry(federation):
    cached = _registry_cache.get(federation)
    if cached and time.time() - cached["fetched_at"] < REGISTRY_CACHE_TTL:
        return cached["registry"]

    response = requests.get(f"{federation}/.well-known/pelican-configuration", timeout=10)
    response.raise_for_status()
    registry = response.json()["namespace_registration_endpoint"]
    _registry_cache[federation] = {"registry": registry, "fetched_at": time.time()}
    return registry


def fetch_namespace_jwks(registry, namespace):
    cached = _jwks_cache.get((registry, namespace))
    if cached and time.time() - cached["fetched_at"] < JWKS_CACHE_TTL:
        return cached["jwks"]

    url = f"{registry}/api/v1.0/registry{namespace}/.well-known/issuer.jwks"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    keys = response.json()["keys"]
    _jwks_cache[(registry, namespace)] = {"jwks": keys, "fetched_at": time.time()}
    return keys


def verify_pelican_event(token, event_federation, namespace):
    """
    Verify a Pelican metadata-publish webhook JWT.

    Raises PelicanAuthError on any verification failure. Returns the
    decoded claims on success.
    """
    if not TRUSTED_FEDERATION:
        raise RuntimeError(
            "CKANEXT__NDPCATALOGADDITIONS__PELICAN_TRUSTED_FEDERATION is not configured"
        )
    if not WEBHOOK_URL:
        raise RuntimeError("CKANEXT__NDPCATALOGADDITIONS__PELICAN_WEBHOOK_URL is not configured")

    # federation/namespace on the event are untrusted until the signature
    # checks out. Only ever follow our own configured, trusted federation
    # for key discovery - never the event's own federation url. See design
    # doc 3.2 and the Pelican doc's "Trust anchor" note.
    if event_federation != TRUSTED_FEDERATION:
        raise PelicanAuthError(f"untrusted federation: {event_federation}")

    if not NAMESPACE_PATTERN.match(namespace or "") or has_bad_segments(namespace.rstrip("/") or "/x"):
        raise PelicanAuthError(f"malformed namespace: {namespace}")

    try:
        registry = discover_registry(TRUSTED_FEDERATION)
        keys = fetch_namespace_jwks(registry, namespace)
    except requests.RequestException as e:
        raise PelicanAuthError(f"key discovery failed: {e}")

    try:
        header = jwt.get_unverified_header(token)
        matching_keys = [k for k in keys if k.get("kid") == header.get("kid")]
        if not matching_keys:
            raise PelicanAuthError("no matching key for token kid")

        claims = jwt.decode(
            token,
            matching_keys[0],
            algorithms=["RS256", "ES256"],
            audience=WEBHOOK_URL,
            options={"verify_signature": True},
        )
    except JWTError as e:
        raise PelicanAuthError(f"token verification failed: {e}")

    required_scope = f"pelican.metadata:{namespace}"
    scopes = (claims.get("scope") or "").split()
    if required_scope not in scopes:
        raise PelicanAuthError(f"token missing required scope: {required_scope}")

    return claims


def get_or_create_service_user():
    user = model.User.by_email(SERVICE_USER_EMAIL)
    if user:
        return user

    user = model.User(name=SERVICE_USER_NAME, email=SERVICE_USER_EMAIL)
    user.fullname = "Pelican Ingest Service"
    user.password = generate_random_password()
    user.state = model.State.ACTIVE
    model.Session.add(user)
    try:
        model.Session.commit()
    except sa.exc.IntegrityError:
        # Several first-ever events at once: another worker created it.
        model.Session.rollback()
        user = model.User.by_email(SERVICE_USER_EMAIL)
        if user is None:
            raise
    return user


def set_extra(package, key, value):
    package.setdefault("extras", [])
    for extra in package["extras"]:
        if extra.get("key") == key:
            extra["value"] = value
            return package
    package["extras"].append({"key": key, "value": value})
    return package


def get_extra(package, key, default=None):
    for extra in package.get("extras", []):
        if extra.get("key") == key:
            return extra.get("value")
    return default


def normalize_tags(value):
    if isinstance(value, str):
        names = [t.strip() for t in value.split(",") if t.strip()]
    elif isinstance(value, list):
        names = [str(t).strip() for t in value if str(t).strip()]
    else:
        names = []
    return [{"name": name} for name in names]


def is_protected_field(key):
    """
    Keys uploader metadata must never set. Custom fields are unauthenticated
    as to who uploaded them (design doc 3.2), so they can't be allowed to
    touch ownership (ndp_creator_md5 backs the per-creator edit check),
    review/access state, our own pelican_* bookkeeping, or CKAN's core
    package fields (as extras those are rejected by CKAN outright).
    """
    if key in TOP_LEVEL_PACKAGE_FIELDS:
        return False
    if key in PROTECTED_EXTRA_KEYS or key.startswith("pelican_"):
        return True
    return key in core_package_fields()


def core_package_fields():
    from ckan.logic.schema import default_create_package_schema

    return set(default_create_package_schema().keys())


def merge_custom_fields(package, custom_fields):
    """Apply an event's custom fields onto a package dict, last write wins."""
    for key, value in custom_fields.items():
        if key in (DATASET_FIELD_KEY, EXPECTED_COUNT_FIELD_KEY) or key in RESOURCE_FIELD_KEYS:
            continue
        if is_protected_field(key):
            logger.warning(f"pelican event: ignoring protected metadata field {key!r}")
            continue
        if key == "tags":
            package["tags"] = normalize_tags(value)
        elif key in TOP_LEVEL_PACKAGE_FIELDS:
            package[key] = value
        else:
            set_extra(package, key, value)
    return package


def resource_url_for(federation, path):
    """
    pelican://<federation-host><path> - the form the Pelican client accepts
    and the one existing catalog entries use (pelican://osg-htc.org/...).
    path is already federation-rooted (it starts with the namespace), so the
    namespace is not prepended again. See design doc 3.7.
    """
    host = urlparse(federation or "").netloc
    if not host:
        raise PelicanRejectError(f"event federation is missing or not a url: {federation!r}")
    return f"pelican://{host}{path}"


def guess_mimetype(name):
    # ndp_package_validate requires a mimetype on every resource of a public
    # dataset; the object name is all we have to go on.
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


RFC3339_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:?\d{2})?$"
)


def normalize_resource_timestamp(value):
    """
    A naive UTC ISO string CKAN can validate, or None.

    CKAN's date validator (ckan.lib.helpers.date_str_to_datetime) can't
    parse a UTC offset or a "Z". Pelican's created_at is RFC3339 written by
    Go, so it usually has both and up to nine fraction digits (nanoseconds),
    which Python 3.10's fromisoformat also refuses. So parse it by hand:
    keep microseconds, convert any offset to UTC. A number is taken as Unix
    seconds. Anything else is logged and dropped - a bad timestamp must not
    cost the uploader the whole file (CKAN would reject the event).
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None).isoformat()
        except (OverflowError, OSError, ValueError):
            logger.warning(f"pelican event: ignoring unreadable created_at {value!r}")
            return None
    match = RFC3339_PATTERN.match(str(value).strip())
    if not match:
        logger.warning(f"pelican event: ignoring unreadable created_at {value!r}")
        return None
    date, time_of_day, fraction, offset = match.groups()
    micro = (fraction or "")[:6].ljust(6, "0")
    if offset in (None, "Z", "z"):
        offset = "+00:00"
    elif ":" not in offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    try:
        dt = datetime.fromisoformat(f"{date}T{time_of_day}.{micro}{offset}")
    except ValueError:
        logger.warning(f"pelican event: ignoring unreadable created_at {value!r}")
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def resource_group_folder(namespace, obj, custom_fields):
    """
    The folder named by the optional "resource" key, without a trailing
    slash, or None for an ordinary per-file resource. The folder must sit
    under the namespace and the object under the folder.
    """
    value = custom_fields.get(RESOURCE_GROUP_KEY)
    if value in (None, ""):
        return None
    folder = value.rstrip("/") if isinstance(value, str) else value
    if has_bad_segments(folder) or not folder.startswith(namespace.rstrip("/") + "/"):
        raise PelicanRejectError(f"resource folder {value!r} is not a folder under namespace {namespace}")
    if not obj["path"].startswith(folder + "/"):
        raise PelicanRejectError(f"object path {obj['path']} is outside its resource folder {folder}/")
    return folder


def insert_in_path_order(resources, new_resource):
    """
    Put a new Pelican resource before the first Pelican resource whose url
    sorts after it, so files land in tree order rather than arrival order.
    Existing resources are never moved - a curator's ordering survives.
    """
    for index, existing in enumerate(resources):
        url = existing.get("url") or ""
        if url.startswith("pelican://") and url > new_resource["url"]:
            resources.insert(index, new_resource)
            return
    resources.append(new_resource)


def upsert_resource(package, federation, obj, namespace, custom_fields=None):
    """
    Add or refresh the resource for one object. Identity is the resource
    url: the object's own url, or its folder's url when the uploader set the
    "resource" key, so every file of a logical unit lands on one resource.
    """
    custom_fields = custom_fields or {}
    namespace_prefix = namespace.rstrip("/") + "/"
    folder = resource_group_folder(namespace, obj, custom_fields)
    last_modified = normalize_resource_timestamp(obj.get("created_at"))

    if folder is None:
        url = resource_url_for(federation, obj["path"])
        relative = obj["path"][len(namespace_prefix):]
        # Facts about the object itself - always refreshed from the event.
        refreshed = {
            "url": url,
            "size": obj.get("size"),
            "hash": (obj.get("etag") or "").strip('"'),
        }
        if last_modified:
            refreshed["last_modified"] = last_modified
        defaults = {
            # Path relative to the namespace, so run-1/data.csv and
            # run-2/data.csv stay distinguishable.
            "name": relative,
            "description": f"Pelican object at {obj['path']}",
            "mimetype": guess_mimetype(obj["path"]),
        }
    else:
        url = resource_url_for(federation, folder + "/")
        # No size or hash: one event per file can't keep a total without
        # double-counting redeliveries. A manifest file covers that need.
        refreshed = {"url": url}
        defaults = {
            "name": folder[len(namespace_prefix):],
            "description": f"Pelican folder {folder}/ - browse and fetch its files with Pelican tools",
            "mimetype": FOLDER_MIMETYPE,
        }
    # Fields an ordinary NDP submission carries (public validation requires
    # status and mimetype). Defaults only, so a curator's correction
    # survives a redelivered event.
    defaults["status"] = "active"

    # Explicit uploader values win over defaults, last write wins.
    explicit = {}
    if custom_fields.get(RESOURCE_FORMAT_KEY):
        explicit["format"] = custom_fields[RESOURCE_FORMAT_KEY]
    if custom_fields.get(RESOURCE_DESCRIPTION_KEY):
        explicit["description"] = custom_fields[RESOURCE_DESCRIPTION_KEY]

    package.setdefault("resources", [])
    for existing in package["resources"]:
        if existing.get("url") == url:
            existing.update(refreshed)
            if folder is not None:
                # newest file seen in the folder
                current = parse_timestamp(existing.get("last_modified"))
                incoming = parse_timestamp(last_modified)
                if incoming and (current is None or incoming > current):
                    existing["last_modified"] = last_modified
            for key, value in defaults.items():
                if not existing.get(key):
                    existing[key] = value
            existing.update(explicit)
            return package

    new_resource = {**defaults, **refreshed, **explicit}
    if folder is not None and last_modified:
        new_resource["last_modified"] = last_modified
    insert_in_path_order(package["resources"], new_resource)
    return package


@contextmanager
def dataset_lock(package_name, wait_seconds=None):
    """
    Serialize events for one dataset across web workers (design doc 3.4).
    Each event reads the whole package and saves it back, so two at once
    would lose a resource, and two first events would both try to create
    it.

    Uses a PostgreSQL session-level advisory lock on its own connection,
    not CKAN's session: CKAN actions commit mid-request, which would end a
    transaction-scoped lock early, and the session may switch pooled
    connections between transactions.
    """
    wait_seconds = DATASET_LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    key = f"pelican-dataset:{package_name}"
    connection = model.meta.engine.connect()
    try:
        deadline = time.monotonic() + wait_seconds
        while not connection.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
        ).scalar():
            if time.monotonic() >= deadline:
                raise PelicanBusyError(f"dataset {package_name} is busy with another event")
            time.sleep(0.05)
        try:
            yield
        finally:
            connection.execute(sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
    finally:
        connection.close()


def name_taken_in_catalog(package_name):
    """
    True if an active dataset with this name is already published in the
    NDP (production) catalog. Only a definite "not found" counts as free:
    network errors and any other response (a 502 during an outage, say)
    raise, and the caller turns that into a 500 so Pelican retries rather
    than staging a name that might collide.
    """
    response = requests.post(
        f"{controller.ckan_url}/api/3/action/package_show",
        headers=controller.headers,
        json={"id": package_name},
        timeout=10,
    )
    if response.status_code == 404:
        return False
    if response.status_code != 200:
        raise RuntimeError(
            f"production catalog name check failed: HTTP {response.status_code}"
        )
    return response.json().get("result", {}).get("state") == "active"


# Receiver bookkeeping that only means something in Prekan. Approval strips
# these; pelican_namespace and pelican_dataset stay as provenance on the
# published dataset. See design doc 3.4.
PRODUCTION_STRIPPED_EXTRAS = {"pelican_ingest_status", "pelican_ingest_errors", "pelican_expected_count"}


def strip_pelican_bookkeeping(dataset):
    """Remove Prekan-only Pelican extras from a dataset dict, in place."""
    if dataset.get("extras"):
        dataset["extras"] = [
            extra for extra in dataset["extras"]
            if extra.get("key") not in PRODUCTION_STRIPPED_EXTRAS
        ]
    return dataset


def pelican_package_name(org_name, dataset):
    return munge_title_to_name(f"{org_name}-{dataset}")


def get_or_create_pelican_package(org_name, namespace, dataset, service_user):
    package_name = pelican_package_name(org_name, dataset)

    try:
        package = logic.get_action("package_show")({"ignore_auth": True}, {"id": package_name})
    except logic.NotFound:
        package = None

    if package is not None:
        if package.get("state") != "active":
            # Approval (and rejection) delete the Prekan copy. Updating that
            # deleted copy would answer 2xx while the file never reaches the
            # published dataset, so refuse instead. Adding files to a
            # reviewed dataset is a curator action in v1.
            status = get_extra(package, "approval_status") or package.get("state")
            raise PelicanRejectError(
                f"dataset {package_name} has already been reviewed ({status}); "
                "new files for it must be added by a curator"
            )
        return package, False

    # Same rule ordinary submissions follow (controller.create_package):
    # don't stage a name that's already published, or approval will collide.
    if name_taken_in_catalog(package_name):
        raise PelicanRejectError(f"dataset name is already used in the NDP catalog: {package_name}")

    organization = process_user_and_organization(service_user, org_name)
    package = {
        "name": package_name,
        "owner_org": organization.name,
        # Public, like an ordinary submission. In NDP "private" means
        # restricted to the Keycloak groups in an existingGroupIds extra,
        # not "draft" - Prekan itself is the review queue. Restricted
        # Pelican data is a follow-up. See design doc 3.1.
        "private": False,
        "resources": [],
        "extras": [],
    }
    set_extra(package, "pelican_namespace", namespace)
    set_extra(package, "pelican_dataset", dataset)
    package = add_or_update_ndp_creator_md5(package, SERVICE_USER_NDP_CREATOR_MD5)

    context = {"user": service_user.name}
    package = logic.get_action("package_create")(context, package)
    return package, True


def maybe_finalize(package):
    """
    Once enough objects have landed, run full NDP validation and mark the
    package's ingest status. See design doc 3.4 for the completion signal
    and why there is no automatic handling of a count that is never
    reached or is exceeded - that is left to a human.
    """
    expected_count = get_extra(package, "pelican_expected_count")
    if expected_count is None:
        return package

    try:
        expected_count = int(expected_count)
    except (TypeError, ValueError):
        return package

    if len(package.get("resources", [])) < expected_count:
        return package

    result = ndp_package_validate(json.dumps(package))
    if result["errors"]:
        set_extra(package, "pelican_ingest_status", "incomplete")
        set_extra(package, "pelican_ingest_errors", json.dumps(result["errors"]))
    else:
        set_extra(package, "pelican_ingest_status", "ready")
        set_extra(package, "pelican_ingest_errors", json.dumps([]))

    return package


def process_pelican_event(event):
    """
    Process one already-authenticated object-commit event: resolve namespace
    to org, create or update the target package, merge in this event's custom
    fields, upsert the resource, and finalize if the dataset is now complete.

    Pelican's own JWT verification (verify_pelican_event) is a separate,
    earlier step and is not part of this function - this is everything that
    happens once an event is trusted. Kept separate from the Flask view so it
    can be called directly, e.g. from a test, without going through request
    parsing or auth at all.

    Raises PelicanRejectError for anything permanently unprocessable (maps to
    422 at the HTTP layer). Returns the resulting package dict.
    """
    event_type = event.get("type")
    if event_type != "object.committed":
        # object.updated / object.deleted are deferred to a follow-up.
        raise PelicanRejectError(f"unsupported event type: {event_type}")

    namespace = event.get("namespace")
    obj = event.get("object") or {}

    namespace_org_map = get_namespace_org_map()
    org_name = namespace_org_map.get(namespace)
    if not org_name:
        raise PelicanRejectError(f"unknown or unmapped namespace: {namespace}")

    custom_fields = {
        NDP_EXTRA_ALIASES.get(k, k): metadata_value_to_text(v)
        for k, v in obj.items()
        if k not in PELICAN_RESERVED_OBJECT_KEYS
    }
    dataset = custom_fields.get(DATASET_FIELD_KEY)
    if not dataset:
        raise PelicanRejectError("missing required custom metadata field: dataset")

    if "path" not in obj:
        raise PelicanRejectError("missing required field: object.path")

    # The token's pelican.metadata scope only vouches for this namespace
    # (design doc 3.2), so the object must live under it.
    if has_bad_segments(obj["path"]) or not obj["path"].startswith(namespace.rstrip("/") + "/"):
        raise PelicanRejectError(f"object path {obj['path']} is outside namespace {namespace}")
    # A bad "resource" folder is rejected before anything is created, too.
    resource_group_folder(namespace, obj, custom_fields)

    # Fail on a bad federation before anything is created.
    federation = event.get("federation")
    resource_url_for(federation, obj["path"])

    service_user = get_or_create_service_user()

    # Read-modify-write of the whole package: hold the dataset's lock from
    # the read (or create) through the save. See dataset_lock.
    with dataset_lock(pelican_package_name(org_name, dataset)):
        package, created = get_or_create_pelican_package(
            org_name, namespace, dataset, service_user
        )

        package = merge_custom_fields(package, custom_fields)
        package = upsert_resource(package, federation, obj, namespace, custom_fields)

        if EXPECTED_COUNT_FIELD_KEY in custom_fields:
            set_extra(package, "pelican_expected_count", custom_fields[EXPECTED_COUNT_FIELD_KEY])

        package = maybe_finalize(package)

        context = {"user": service_user.name}
        return logic.get_action("package_update")(context, package)


def pelican_metadata_publish():
    if request.method != "POST":
        return "Method not allowed", 405

    if not request.is_json:
        # v1 is json-only; the multipart/blob variant is deferred. See
        # design doc 3.5. This will never succeed on retry.
        logger.warning(f"pelican event rejected: unsupported content type {request.content_type!r}")
        return "Unsupported content type, expected application/json", 422

    # silent=True matches the rest of the extension (CKAN 2.11 / newer Flask
    # raise on a bad body otherwise); a body that parses but isn't a json
    # object (e.g. "null", a list) is just as permanently unprocessable.
    event = request.get_json(silent=True)
    if not isinstance(event, dict):
        logger.warning("pelican event rejected: body is not a json object")
        return "Malformed json body, expected a json object", 422

    namespace = event.get("namespace")
    federation = event.get("federation")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return "Missing or invalid Authorization header", 401
    token = auth_header[len("Bearer "):]

    try:
        verify_pelican_event(token, federation, namespace)
    except PelicanAuthError as e:
        logger.warning(f"pelican event auth failed: {e}")
        return f"Unauthorized: {e}", 401
    except RuntimeError as e:
        # Our own misconfiguration (trusted federation / webhook url unset).
        # Deliberately not a 422: once config is fixed the same event is
        # fine, so let Pelican keep retrying rather than drop it.
        logger.error(f"pelican receiver misconfigured: {e}")
        return "Receiver not configured", 500

    # Pelican drops a 422 for good, so every outcome is logged with enough
    # to find the upload again.
    object_path = (event.get("object") or {}).get("path")
    try:
        package = process_pelican_event(event)
    except PelicanRejectError as e:
        logger.warning(f"pelican event rejected: {e} (namespace {namespace}, path {object_path})")
        return f"Unprocessable: {e}", 422
    except PelicanBusyError as e:
        # Another event for this dataset is mid-save; Pelican retries.
        logger.info(f"pelican event deferred: {e}")
        return f"Busy: {e}", 503
    except logic.ValidationError as e:
        # CKAN refused the data itself (e.g. an invalid tag) - retrying the
        # same event can't succeed, so tell Pelican to stop.
        logger.warning(
            f"pelican event rejected by CKAN validation: {e.error_dict} (namespace {namespace}, path {object_path})"
        )
        return f"Unprocessable: {e.error_dict}", 422
    except Exception as e:
        logger.exception(f"pelican metadata publish failed (namespace {namespace}, path {object_path})")
        return f"Error: {e}", 500

    logger.info(
        f"pelican event accepted: {package['name']} <- {object_path}"
        f" ({len(package.get('resources') or [])} resources,"
        f" status {get_extra(package, 'pelican_ingest_status') or 'pending'})"
    )
    return "Accepted"

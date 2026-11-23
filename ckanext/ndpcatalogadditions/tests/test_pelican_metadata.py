"""
Tests for pelican_metadata.py.

These exercise process_pelican_event directly, with Pelican's own JWT
verification out of the picture entirely - that's a separate, earlier step
(verify_pelican_event) not covered here. What's under test is everything
that happens once an event is trusted: namespace -> org resolution, package
creation, the progressive custom-field merge across events, resource
upsert, and the expected_count-gated finalize. Real Prekan (test db) package
entries get created, same as the real endpoint would create.
"""
import json

import pytest

from ckan.tests import factories, helpers as test_helpers

from ckanext.ndpcatalogadditions import pelican_metadata
from ckanext.ndpcatalogadditions.tests.fake_pelican import check_metadata_keys

NAMESPACE = "/test-namespace"
ORG_NAME = "pelican-test-org"


def make_event(path, **object_fields):
    # Same key rule as the real Pelican client, so tests can't use metadata
    # an uploader couldn't actually send.
    check_metadata_keys(object_fields)
    return {
        "type": "object.committed",
        "namespace": NAMESPACE,
        "federation": "https://example-federation.test",
        "object": {
            "path": path,
            "size": 1024,
            "etag": '"abc123"',
            "created_at": "2026-10-26T00:00:00Z",
            **object_fields,
        },
    }


@pytest.fixture(autouse=True)
def no_production_catalog(monkeypatch):
    # The production-name check calls the real NDP catalog; tests that care
    # about it override this.
    monkeypatch.setattr(pelican_metadata, "name_taken_in_catalog", lambda name: False)


PUBLIC_FIELDS = {
    "tags": "pelican,test",
    "upload_type": "dataset",
    "last_update_date": "2026-10-26",
    "poc_name": "Test Contact",
    "poc_email": "contact@example.org",
}


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_progressive_merge_creates_and_finalizes_package(monkeypatch):
    # Pre-create the org locally so process_user_and_organization finds it
    # without falling back to a network call to a remote CKAN.
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )

    first = make_event(
        f"{NAMESPACE}/part-1.csv",
        dataset="my-dataset",
        title="My Dataset",
        expected_count=2,
        tags=PUBLIC_FIELDS["tags"],
        upload_type=PUBLIC_FIELDS["upload_type"],
    )
    package = pelican_metadata.process_pelican_event(first)

    # package_show/package_update return owner_org as the org's id, not its
    # name - the name lives under package["organization"].
    assert package["organization"]["name"] == ORG_NAME
    # public, like an ordinary submission - "private" means group-restricted
    # in NDP, not "draft"
    assert package["private"] is False
    assert package["title"] == "My Dataset"
    assert len(package["resources"]) == 1
    resource = package["resources"][0]
    assert resource["url"] == f"pelican://example-federation.test{NAMESPACE}/part-1.csv"
    assert resource["status"] == "active"
    assert resource["mimetype"] == "text/csv"
    # below expected_count - finalize hasn't run yet
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") is None

    second = make_event(
        f"{NAMESPACE}/part-2.csv",
        dataset="my-dataset",
        notes="Two-part test dataset",
        expected_count=2,
        last_update_date=PUBLIC_FIELDS["last_update_date"],
        poc_name=PUBLIC_FIELDS["poc_name"],
        poc_email=PUBLIC_FIELDS["poc_email"],
    )
    package = pelican_metadata.process_pelican_event(second)

    assert len(package["resources"]) == 2
    assert package["notes"] == "Two-part test dataset"
    # title from the first event is still there - last write wins, not last
    # write only
    assert package["title"] == "My Dataset"
    # expected_count reached - full ndp_package_validate ran and passed
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "ready"

    # it's a real Prekan entry, same as any other package
    shown = test_helpers.call_action("package_show", id=package["id"])
    assert shown["name"] == package["name"]
    assert len(shown["resources"]) == 2


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_finalize_marks_incomplete_when_required_fields_still_missing(monkeypatch):
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )

    # expected_count=1 and no title/notes supplied at all - complete by count,
    # but not valid.
    event = make_event(f"{NAMESPACE}/only-file.csv", dataset="bare-dataset", expected_count=1)
    package = pelican_metadata.process_pelican_event(event)

    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "incomplete"
    errors = json.loads(pelican_metadata.get_extra(package, "pelican_ingest_errors"))
    # title isn't actually missing here - CKAN's own package_create defaults
    # title from name when none is supplied, so notes is the one that's
    # genuinely never filled in by this event.
    assert any("notes" in e for e in errors)


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_redelivered_event_is_idempotent(monkeypatch):
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )

    event = make_event(
        f"{NAMESPACE}/part-1.csv",
        dataset="redelivery-dataset",
        title="Redelivery Dataset",
        notes="Checks at-least-once delivery is handled without duplicating resources",
        expected_count=1,
    )

    pelican_metadata.process_pelican_event(event)
    package = pelican_metadata.process_pelican_event(event)

    assert len(package["resources"]) == 1


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_unmapped_namespace_is_rejected(monkeypatch):
    monkeypatch.setenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP", json.dumps({}))

    event = make_event(f"{NAMESPACE}/part-1.csv", dataset="my-dataset")

    with pytest.raises(pelican_metadata.PelicanRejectError):
        pelican_metadata.process_pelican_event(event)


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_missing_dataset_field_is_rejected(monkeypatch):
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )

    event = make_event(f"{NAMESPACE}/part-1.csv")

    with pytest.raises(pelican_metadata.PelicanRejectError):
        pelican_metadata.process_pelican_event(event)


# ---- rules from the design review -------------------------------------------

def _map_namespace(monkeypatch):
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_event_after_approval_is_rejected(monkeypatch):
    # approve_package marks the Prekan copy approved and deletes it; a late
    # event must not "succeed" by updating that invisible copy.
    _map_namespace(monkeypatch)
    package = pelican_metadata.process_pelican_event(
        make_event(f"{NAMESPACE}/part-1.csv", dataset="reviewed", title="Reviewed")
    )
    test_helpers.call_action(
        "package_patch", id=package["id"], extras=package["extras"] + [{"key": "approval_status", "value": "approved"}]
    )
    test_helpers.call_action("package_delete", id=package["id"])

    with pytest.raises(pelican_metadata.PelicanRejectError, match="already been reviewed \\(approved\\)"):
        pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/part-2.csv", dataset="reviewed"))


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_name_already_published_is_rejected(monkeypatch):
    # Same rule as ordinary submissions: don't stage a name production has.
    _map_namespace(monkeypatch)
    monkeypatch.setattr(pelican_metadata, "name_taken_in_catalog", lambda name: True)

    with pytest.raises(pelican_metadata.PelicanRejectError, match="already used in the NDP catalog"):
        pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/a.csv", dataset="taken"))


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_object_outside_namespace_is_rejected(monkeypatch):
    # The token only vouches for NAMESPACE.
    _map_namespace(monkeypatch)
    event = make_event("/someone-elses-namespace/a.csv", dataset="x")

    with pytest.raises(pelican_metadata.PelicanRejectError, match="outside namespace"):
        pelican_metadata.process_pelican_event(event)


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_bad_federation_is_rejected_before_anything_is_created(monkeypatch):
    _map_namespace(monkeypatch)
    event = make_event(f"{NAMESPACE}/a.csv", dataset="no-federation")
    event["federation"] = "not a url"

    with pytest.raises(pelican_metadata.PelicanRejectError, match="federation"):
        pelican_metadata.process_pelican_event(event)
    with pytest.raises(pelican_metadata.logic.NotFound):
        test_helpers.call_action("package_show", id=f"{ORG_NAME}-no-federation")


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_uploader_cannot_set_protected_fields(monkeypatch):
    # Custom metadata is unauthenticated as to the uploader, so it must not
    # reach ownership, review state, access groups, our own bookkeeping, or
    # CKAN core fields.
    _map_namespace(monkeypatch)
    event = make_event(
        f"{NAMESPACE}/a.csv",
        dataset="protected",
        title="Protected",
        ndp_creator_md5="attacker-fingerprint",
        approval_status="approved",
        pelican_ingest_status="ready",
        private="true",
        owner_org="some-other-org",
        poc_name="Allowed Through",
    )
    # Real Pelican can't send a camelCase key, but the receiver must not
    # rely on that.
    event["object"]["existingGroupIds"] = '["some-group"]'
    package = pelican_metadata.process_pelican_event(event)

    get = lambda key: pelican_metadata.get_extra(package, key)
    assert get("ndp_creator_md5") == pelican_metadata.SERVICE_USER_NDP_CREATOR_MD5
    assert get("approval_status") is None
    assert get("existingGroupIds") is None
    assert get("pelican_ingest_status") is None
    assert get("private") is None and package["private"] is False
    assert package["organization"]["name"] == ORG_NAME
    # ordinary custom fields still land as extras
    assert get("pocName") == "Allowed Through"


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_license_and_author_are_package_fields(monkeypatch):
    _map_namespace(monkeypatch)
    event = make_event(
        f"{NAMESPACE}/a.csv",
        dataset="licensed",
        title="Licensed",
        license_id="cc-by",
        author="Jane Doe",
        author_email="jane@example.org",
        maintainer="Not Allowed",
    )
    package = pelican_metadata.process_pelican_event(event)

    assert package["license_id"] == "cc-by"
    assert package["author"] == "Jane Doe"
    assert package["author_email"] == "jane@example.org"
    # set as real package fields, not duplicated as extras
    for key in ("license_id", "author", "author_email"):
        assert pelican_metadata.get_extra(package, key) is None
    # other core fields stay protected
    assert not package.get("maintainer")
    assert pelican_metadata.get_extra(package, "maintainer") is None


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_invalid_author_email_is_rejected_by_ckan(monkeypatch):
    _map_namespace(monkeypatch)
    event = make_event(f"{NAMESPACE}/a.csv", dataset="bad-email", author_email="not-an-email")
    with pytest.raises(pelican_metadata.logic.ValidationError):
        pelican_metadata.process_pelican_event(event)


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_curator_resource_edits_survive_redelivery(monkeypatch):
    _map_namespace(monkeypatch)
    event = make_event(f"{NAMESPACE}/data.bin", dataset="curated", title="Curated")
    package = pelican_metadata.process_pelican_event(event)
    assert package["resources"][0]["mimetype"] == "application/octet-stream"

    resource = package["resources"][0]
    test_helpers.call_action("resource_patch", id=resource["id"], mimetype="application/x-hdf5")

    package = pelican_metadata.process_pelican_event(event)
    assert package["resources"][0]["mimetype"] == "application/x-hdf5"


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_incomplete_draft_fixed_by_reuploading_a_file(monkeypatch):
    # Design doc 6.3: validation re-runs on every event once the count is
    # reached, so re-sending an existing file with the missing key fixes the
    # draft without adding a resource.
    _map_namespace(monkeypatch)
    fields = {k: v for k, v in PUBLIC_FIELDS.items() if k != "poc_email"}
    common = dict(dataset="fixable", title="Fixable", notes="n", expected_count="1")

    package = pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/a.csv", **common, **fields))
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "incomplete"
    assert "pocEmail" in pelican_metadata.get_extra(package, "pelican_ingest_errors")

    package = pelican_metadata.process_pelican_event(
        make_event(f"{NAMESPACE}/a.csv", **common, poc_email="contact@example.org")
    )
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "ready"
    assert len(package["resources"]) == 1


# ---- concurrency (Kai's review 2.1) -----------------------------------------

def _run_concurrently(app, events, monkeypatch, delay=0.2):
    """
    Process events in parallel threads, each with its own app context and
    db session (as separate web workers would). upsert_resource is slowed
    down to widen the read-modify-write window, so an unserialized run
    reliably loses updates.
    """
    import threading
    from ckan import model

    real_upsert = pelican_metadata.upsert_resource

    def slow_upsert(*args, **kwargs):
        import time
        time.sleep(delay)
        return real_upsert(*args, **kwargs)

    monkeypatch.setattr(pelican_metadata, "upsert_resource", slow_upsert)
    errors = []
    start = threading.Barrier(len(events))

    def worker(event):
        with app.flask_app.test_request_context():
            try:
                start.wait()
                pelican_metadata.process_pelican_event(event)
            except Exception as e:  # collected and asserted on below
                errors.append(e)
            finally:
                model.Session.remove()

    threads = [threading.Thread(target=worker, args=(e,)) for e in events]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_concurrent_events_for_one_dataset_lose_nothing(app, monkeypatch):
    # Covers both of Kai's cases: several first events racing to create the
    # package, and concurrent appends to it. Without the lock this loses
    # resources and/or raises "URL already in use".
    _map_namespace(monkeypatch)
    paths = [f"{NAMESPACE}/concurrent/part-{i}.csv" for i in range(5)]
    events = [make_event(p, dataset="concurrent", title="Concurrent") for p in paths]

    errors = _run_concurrently(app, events, monkeypatch)

    assert errors == []
    package = test_helpers.call_action("package_show", id=f"{ORG_NAME}-concurrent")
    assert sorted(r["url"].split("/")[-1] for r in package["resources"]) == [
        f"part-{i}.csv" for i in range(5)
    ]


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_concurrent_appends_to_existing_dataset_lose_nothing(app, monkeypatch):
    # The lost-update case on its own: the package already exists, and each
    # event reads it, adds its resource and saves the whole thing back.
    _map_namespace(monkeypatch)
    pelican_metadata.process_pelican_event(
        make_event(f"{NAMESPACE}/appends/part-0.csv", dataset="appends", title="Appends")
    )
    events = [
        make_event(f"{NAMESPACE}/appends/part-{i}.csv", dataset="appends") for i in range(1, 6)
    ]

    errors = _run_concurrently(app, events, monkeypatch)

    assert errors == []
    package = test_helpers.call_action("package_show", id=f"{ORG_NAME}-appends")
    assert len(package["resources"]) == 6


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_event_gives_up_when_dataset_stays_locked(monkeypatch):
    _map_namespace(monkeypatch)
    monkeypatch.setattr(pelican_metadata, "DATASET_LOCK_WAIT_SECONDS", 0.2)
    name = pelican_metadata.pelican_package_name(ORG_NAME, "locked")

    with pelican_metadata.dataset_lock(name):
        with pytest.raises(pelican_metadata.PelicanBusyError):
            pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/a.csv", dataset="locked"))

    # released afterwards
    pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/a.csv", dataset="locked"))


# ---- logical-unit resources, naming, order (Kai's review section 5) ---------

@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_files_sharing_a_resource_folder_become_one_resource(monkeypatch):
    _map_namespace(monkeypatch)
    folder = f"{NAMESPACE}/gis/roads"
    parts = ["roads.shp", "roads.shx", "roads.dbf", "roads.prj"]
    for i, part in enumerate(parts):
        fields = dict(dataset="gis", resource=folder, format="shapefile")
        if i == 1:
            fields["resource_description"] = "Road centrelines"
        event = make_event(f"{folder}/{part}", **fields)
        event["object"]["created_at"] = f"2026-10-26T00:00:0{i}Z"
        package = pelican_metadata.process_pelican_event(event)

    assert len(package["resources"]) == 1
    resource = package["resources"][0]
    assert resource["url"] == f"pelican://example-federation.test{folder}/"
    assert resource["name"] == "gis/roads"
    assert resource["format"] == "SHP"  # CKAN normalizes known format names
    assert resource["mimetype"] == "inode/directory"
    assert resource["status"] == "active"
    assert resource["description"] == "Road centrelines"
    assert resource["last_modified"].startswith("2026-10-26T00:00:03")
    # resource-level keys never become dataset extras
    for key in ("resource", "format", "resource_description"):
        assert pelican_metadata.get_extra(package, key) is None


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_grouped_and_single_files_mix_and_count(monkeypatch):
    _map_namespace(monkeypatch)
    common = dict(dataset="mixed", title="Mixed", notes="n", expected_count="2", **PUBLIC_FIELDS)
    pelican_metadata.process_pelican_event(
        make_event(f"{NAMESPACE}/mixed/zarr/0.0", resource=f"{NAMESPACE}/mixed/zarr/", format="zarr", **common)
    )
    pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/mixed/zarr/0.1", resource=f"{NAMESPACE}/mixed/zarr", **common))
    package = pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/mixed/README.md", **common))

    assert [r["name"] for r in package["resources"]] == ["mixed/README.md", "mixed/zarr"]
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "ready"


@pytest.mark.parametrize("folder, path", [
    (f"{NAMESPACE}/gis/roads", f"{NAMESPACE}/gis/rivers/rivers.shp"),  # file outside its folder
    ("/other-namespace/roads", "/other-namespace/roads/a.shp"),       # folder outside namespace
    (f"{NAMESPACE}/gis/../roads", f"{NAMESPACE}/gis/../roads/a.shp"),  # dot-dot
])
@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_bad_resource_folder_is_rejected_before_anything_is_created(monkeypatch, folder, path):
    _map_namespace(monkeypatch)
    with pytest.raises(pelican_metadata.PelicanRejectError):
        pelican_metadata.process_pelican_event(make_event(path, dataset="badfolder", resource=folder))
    with pytest.raises(pelican_metadata.logic.NotFound):
        test_helpers.call_action("package_show", id=f"{ORG_NAME}-badfolder")


@pytest.mark.parametrize("path", [
    f"{NAMESPACE}/../escape.csv",
    f"{NAMESPACE}/./a.csv",
    f"{NAMESPACE}//a.csv",
    f"{NAMESPACE}/dir/",
])
@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_object_path_with_bad_segments_is_rejected(monkeypatch, path):
    _map_namespace(monkeypatch)
    with pytest.raises(pelican_metadata.PelicanRejectError):
        pelican_metadata.process_pelican_event(make_event(path, dataset="segments"))


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_same_file_name_in_different_folders_stays_distinct_and_ordered(monkeypatch):
    _map_namespace(monkeypatch)
    for run in ("run-3", "run-1", "run-2"):
        package = pelican_metadata.process_pelican_event(
            make_event(f"{NAMESPACE}/{run}/data.csv", dataset="runs")
        )
    # named by path, inserted in tree order rather than arrival order
    assert [r["name"] for r in package["resources"]] == [
        "run-1/data.csv", "run-2/data.csv", "run-3/data.csv",
    ]


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_curator_name_and_description_survive_redelivery(monkeypatch):
    _map_namespace(monkeypatch)
    event = make_event(f"{NAMESPACE}/data.csv", dataset="curated2")
    package = pelican_metadata.process_pelican_event(event)
    test_helpers.call_action(
        "resource_patch", id=package["resources"][0]["id"], name="Main table", description="Curated"
    )
    package = pelican_metadata.process_pelican_event(event)
    assert package["resources"][0]["name"] == "Main table"
    assert package["resources"][0]["description"] == "Curated"


# ---- production name check and approval (Kai's review 3.2, 3.3) ------------

class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


@pytest.mark.parametrize("status, body, expected", [
    (404, {"success": False, "error": {"__type": "Not Found Error"}}, False),
    (200, {"result": {"state": "active"}}, True),
    (200, {"result": {"state": "deleted"}}, False),
])
def test_name_taken_in_catalog_answers(monkeypatch, status, body, expected):
    monkeypatch.undo()  # drop the autouse stub of this very function
    monkeypatch.setattr(pelican_metadata.requests, "post", lambda *a, **k: _Response(status, body))
    assert pelican_metadata.name_taken_in_catalog("x") is expected


@pytest.mark.parametrize("status", [403, 500, 502, 503])
def test_name_taken_in_catalog_raises_when_production_cannot_answer(monkeypatch, status):
    monkeypatch.undo()
    monkeypatch.setattr(pelican_metadata.requests, "post", lambda *a, **k: _Response(status))
    with pytest.raises(RuntimeError):
        pelican_metadata.name_taken_in_catalog("x")


def test_strip_pelican_bookkeeping_keeps_provenance():
    dataset = {"extras": [
        {"key": k, "value": "v"} for k in (
            "pelican_ingest_status", "pelican_ingest_errors", "pelican_expected_count",
            "pelican_namespace", "pelican_dataset", "pocName",
        )
    ]}
    pelican_metadata.strip_pelican_bookkeeping(dataset)
    assert [e["key"] for e in dataset["extras"]] == ["pelican_namespace", "pelican_dataset", "pocName"]


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_number_and_boolean_metadata_values_become_text(monkeypatch):
    # Pelican's --metadata-file allows string, number and boolean values.
    _map_namespace(monkeypatch)
    event = make_event(
        f"{NAMESPACE}/a.csv", dataset="typed", title="Typed", notes="n",
        expected_count=1, final_product=True, version_number=2.5,
        **{k: v for k, v in PUBLIC_FIELDS.items()},
    )
    package = pelican_metadata.process_pelican_event(event)

    get = lambda key: pelican_metadata.get_extra(package, key)
    assert get("pelican_expected_count") == "1"
    assert get("final_product") == "true"
    assert get("version_number") == "2.5"
    assert get("pelican_ingest_status") == "ready"


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_snake_case_keys_become_ndp_extras(monkeypatch):
    # Pelican's metadata header only allows lowercase keys, so NDP's
    # camelCase extras arrive as snake_case and are stored under NDP's names.
    _map_namespace(monkeypatch)
    event = make_event(
        f"{NAMESPACE}/a.csv", dataset="aliased", title="Aliased", notes="n",
        docs_url="https://example.org/docs", spatial_res="10 meter",
        some_custom_key="kept as is",
    )
    package = pelican_metadata.process_pelican_event(event)

    keys = {e["key"]: e["value"] for e in package["extras"]}
    assert keys["docsURL"] == "https://example.org/docs"
    assert keys["spatialRes"] == "10 meter"
    assert keys["some_custom_key"] == "kept as is"
    assert "docs_url" not in keys and "spatial_res" not in keys


def test_every_alias_is_a_valid_pelican_key():
    for alias in pelican_metadata.NDP_EXTRA_ALIASES:
        assert pelican_metadata.SFV_KEY_PATTERN.match(alias), alias


def test_fake_pelican_refuses_keys_real_pelican_refuses():
    with pytest.raises(ValueError, match="lowercase"):
        make_event(f"{NAMESPACE}/a.csv", dataset="x", uploadType="dataset")


@pytest.mark.parametrize("created_at, expected", [
    # Go's RFC3339Nano, as real Pelican sends it: nine fraction digits
    ("2026-11-23T20:22:11.123456789Z", "2026-11-23T20:22:11.123456"),
    ("2026-11-23T20:22:11.5Z", "2026-11-23T20:22:11.500000"),
    ("2026-11-23T20:22:11Z", "2026-11-23T20:22:11"),
    ("2026-11-23T14:22:11.123456789-06:00", "2026-11-23T20:22:11.123456"),
    ("2026-11-23T20:22:11+0000", "2026-11-23T20:22:11"),
    ("2026-11-23T20:22:11", "2026-11-23T20:22:11"),
    (1795465331, "2026-11-23T20:22:11"),
    ("not a date", None),
    ("", None),
    (None, None),
])
def test_normalize_resource_timestamp(created_at, expected):
    assert pelican_metadata.normalize_resource_timestamp(created_at) == expected


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_go_nanosecond_created_at_is_accepted(monkeypatch):
    # First real upload on ndp-test: CKAN rejected the whole event because
    # created_at had nine fraction digits.
    _map_namespace(monkeypatch)
    event = make_event(f"{NAMESPACE}/a.csv", dataset="nanos", title="Nanos", notes="n")
    event["object"]["created_at"] = "2026-11-23T20:22:11.123456789Z"
    package = pelican_metadata.process_pelican_event(event)
    assert package["resources"][0]["last_modified"] == "2026-11-23T20:22:11.123456"

    # an unreadable timestamp costs the field, not the file
    event = make_event(f"{NAMESPACE}/b.csv", dataset="nanos")
    event["object"]["created_at"] = "yesterday-ish"
    package = pelican_metadata.process_pelican_event(event)
    assert len(package["resources"]) == 2

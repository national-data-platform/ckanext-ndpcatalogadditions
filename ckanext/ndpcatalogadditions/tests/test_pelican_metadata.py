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

NAMESPACE = "/test-namespace"
ORG_NAME = "pelican-test-org"


def make_event(path, **object_fields):
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
    "uploadType": "dataset",
    "lastUpdateDate": "2026-10-26",
    "pocName": "Test Contact",
    "pocEmail": "contact@example.org",
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
        uploadType=PUBLIC_FIELDS["uploadType"],
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
        lastUpdateDate=PUBLIC_FIELDS["lastUpdateDate"],
        pocName=PUBLIC_FIELDS["pocName"],
        pocEmail=PUBLIC_FIELDS["pocEmail"],
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
        existingGroupIds='["some-group"]',
        pelican_ingest_status="ready",
        private="true",
        owner_org="some-other-org",
        pocName="Allowed Through",
    )
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
    fields = {k: v for k, v in PUBLIC_FIELDS.items() if k != "pocEmail"}
    common = dict(dataset="fixable", title="Fixable", notes="n", expected_count="1")

    package = pelican_metadata.process_pelican_event(make_event(f"{NAMESPACE}/a.csv", **common, **fields))
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "incomplete"
    assert "pocEmail" in pelican_metadata.get_extra(package, "pelican_ingest_errors")

    package = pelican_metadata.process_pelican_event(
        make_event(f"{NAMESPACE}/a.csv", **common, pocEmail="contact@example.org")
    )
    assert pelican_metadata.get_extra(package, "pelican_ingest_status") == "ready"
    assert len(package["resources"]) == 1

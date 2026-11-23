"""
HTTP-level tests for the Pelican webhook view (pelican_metadata_publish).

Unlike test_pelican_metadata.py, which calls process_pelican_event directly,
these go through the real Flask route with a real signed JWT from
fake_pelican - so signature, audience, expiry, scope, federation trust and
the response contract (2xx / 401 / 422 / 500) are all exercised. Key
discovery is answered in-process by the fake; nothing touches the network.
"""
import json

import pytest

from ckan.tests import factories

from ckanext.ndpcatalogadditions import pelican_metadata
from ckanext.ndpcatalogadditions.tests.fake_pelican import FakePelican, generate_private_pem

NAMESPACE = "/fake-namespace"
ORG_NAME = "pelican-webhook-test-org"
WEBHOOK_PATH = "/ndp/pelican_metadata_publish"

pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions"),
    pytest.mark.usefixtures("with_plugins", "clean_db"),
]


@pytest.fixture
def fake(monkeypatch):
    factories.Organization(name=ORG_NAME)
    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: ORG_NAME}),
    )
    # The production-name check calls the real NDP catalog.
    monkeypatch.setattr(pelican_metadata, "name_taken_in_catalog", lambda name: False)
    return FakePelican().install(monkeypatch)


def post(app, body, token=None, path=WEBHOOK_PATH, raw=False):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if raw:
        res = app.post(path, data=body, headers=headers, content_type="application/json")
    else:
        res = app.post(path, json=body, headers=headers)
    return res.status_code, res.get_data(as_text=True)


def event(fake, **custom):
    custom.setdefault("dataset", "webhook-dataset")
    return fake.make_event(NAMESPACE, **custom)


# ---- accepted ---------------------------------------------------------------

@pytest.mark.parametrize("path", [WEBHOOK_PATH, "/"])
def test_signed_event_is_accepted_and_creates_package(app, fake, path):
    # "/" is the route real Pelican uses (it was given the bare site url).
    body = event(fake, title="Webhook Dataset", expected_count=1)

    status, text = post(app, body, fake.mint_token(NAMESPACE), path=path)

    assert (status, text) == (200, "Accepted")
    package = pelican_metadata.logic.get_action("package_show")(
        {"ignore_auth": True}, {"id": f"{ORG_NAME}-webhook-dataset"}
    )
    assert package["title"] == "Webhook Dataset"
    # federation host from the event (the fake's is 127.0.0.1:8099)
    assert package["resources"][0]["url"] == f"pelican://127.0.0.1:8099{body['object']['path']}"
    assert package["private"] is False


def test_keys_are_cached_across_events(app, fake):
    post(app, event(fake), fake.mint_token(NAMESPACE))
    # Registry going away after the first fetch doesn't matter while the
    # cached JWKS is still fresh.
    fake.registry_down = True
    status, _ = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 200


# ---- 401: authentication failures (Pelican retries these) -------------------

def test_missing_authorization_header(app, fake):
    status, text = post(app, event(fake))
    assert status == 401
    assert "Authorization" in text


def test_expired_token(app, fake):
    status, text = post(app, event(fake), fake.mint_token(NAMESPACE, lifetime=-60))
    assert status == 401
    assert "expired" in text.lower()


def test_signature_from_unknown_key(app, fake):
    # Same kid, different key - only the signature check can catch this.
    token = fake.mint_token(NAMESPACE, private_pem=generate_private_pem())
    status, text = post(app, event(fake), token)
    assert status == 401
    assert "verification failed" in text


def test_unknown_kid(app, fake):
    status, text = post(app, event(fake), fake.mint_token(NAMESPACE, kid="some-other-key"))
    assert status == 401
    assert "no matching key" in text


def test_token_for_another_audience(app, fake):
    token = fake.mint_token(NAMESPACE, audience="http://some-other-catalog.test")
    status, _ = post(app, event(fake), token)
    assert status == 401


def test_scope_for_another_namespace(app, fake):
    # Correctly signed, but the origin is only trusted for a different
    # namespace than the event claims.
    token = fake.mint_token(NAMESPACE, scope="pelican.metadata:/someone-else")
    status, text = post(app, event(fake), token)
    assert status == 401
    assert "scope" in text


def test_untrusted_federation(app, fake):
    body = fake.make_event(NAMESPACE, federation="https://evil-federation.test", dataset="x")
    status, text = post(app, body, fake.mint_token(NAMESPACE))
    assert status == 401
    assert "untrusted federation: https://evil-federation.test" in text


def test_namespace_not_in_registry(app, fake):
    fake.namespaces = {"/only-this-one"}
    status, text = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 401
    assert "key discovery failed" in text


def test_registry_unreachable(app, fake):
    fake.registry_down = True
    status, text = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 401
    assert "key discovery failed" in text


# ---- 422: permanently unprocessable (Pelican stops retrying) ----------------

@pytest.mark.parametrize("raw_body", ["null", "[1, 2]", "{not json"])
def test_body_not_a_json_object(app, fake, raw_body):
    status, _ = post(app, raw_body, fake.mint_token(NAMESPACE), raw=True)
    assert status == 422


def test_unmapped_namespace_with_valid_token(app, fake, monkeypatch, caplog):
    monkeypatch.setenv("CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP", "{}")
    body = event(fake)
    status, text = post(app, body, fake.mint_token(NAMESPACE))
    assert status == 422
    assert "unmapped namespace" in text
    # Pelican drops a 422 for good, so the reason must reach our log.
    assert f"pelican event rejected: unknown or unmapped namespace: {NAMESPACE}" in caplog.text
    assert body["object"]["path"] in caplog.text


def test_accepted_event_is_logged(app, fake, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="ckanext.ndpcatalogadditions.pelican_metadata")
    body = event(fake, title="Logged", expected_count=5)
    status, _ = post(app, body, fake.mint_token(NAMESPACE))
    assert status == 200
    assert (
        f"pelican event accepted: {ORG_NAME}-webhook-dataset <- {body['object']['path']}"
        " (1 resources, status pending)"
    ) in caplog.text


def test_missing_dataset_field_with_valid_token(app, fake):
    body = fake.make_event(NAMESPACE)  # no dataset custom field
    status, text = post(app, body, fake.mint_token(NAMESPACE))
    assert status == 422
    assert "dataset" in text


# ---- 500: our own misconfiguration / transient failures (Pelican retries) ---

def test_unreachable_remote_org_lookup_fails_fast(app, fake, monkeypatch):
    # Mapped org that doesn't exist locally -> process_user_and_organization
    # asks the remote CKAN. If that remote never answers, the request must
    # fail (500, so Pelican retries) instead of holding the worker forever.
    from ckanext.ndpcatalogadditions import controller

    monkeypatch.setenv(
        "CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP",
        json.dumps({NAMESPACE: "org-missing-locally"}),
    )
    seen = {}
    real_post = controller.requests.post

    def hanging_remote(url, *args, **kwargs):
        if url.endswith("/api/3/action/organization_show"):
            seen["timeout"] = kwargs.get("timeout")
            raise controller.requests.Timeout("remote CKAN did not answer")
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(controller.requests, "post", hanging_remote)

    status, _ = post(app, event(fake), fake.mint_token(NAMESPACE))

    assert status == 500
    assert seen["timeout"] is not None
    assert seen["timeout"] <= 30


def test_receiver_without_trusted_federation(app, fake, monkeypatch):
    monkeypatch.setattr(pelican_metadata, "TRUSTED_FEDERATION", None)
    status, text = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert (status, text) == (500, "Receiver not configured")


def test_ckan_validation_error_is_422(app, fake):
    # CKAN rejects the tag itself - retrying the same event can't help.
    status, text = post(app, event(fake, tags="not!a!valid!tag"), fake.mint_token(NAMESPACE))
    assert status == 422
    assert "tags" in text.lower() or "tag" in text.lower()


# ---- response codes from Kai's review (3.1, 3.2, 2.1) -----------------------

def test_unexpected_value_error_is_500_not_422(app, fake, monkeypatch):
    # e.g. a malformed JSON reply from production during the org lookup:
    # our side failed, so Pelican should retry rather than drop the event.
    def broken(*args, **kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(pelican_metadata, "get_or_create_pelican_package", broken)
    status, _ = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 500


def test_production_outage_during_name_check_is_500(app, fake, monkeypatch):
    def outage(name):
        raise RuntimeError("production catalog name check failed: HTTP 503")

    monkeypatch.setattr(pelican_metadata, "name_taken_in_catalog", outage)
    status, _ = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 500


def test_busy_dataset_is_503(app, fake, monkeypatch):
    monkeypatch.setattr(pelican_metadata, "DATASET_LOCK_WAIT_SECONDS", 0.2)
    name = pelican_metadata.pelican_package_name(ORG_NAME, "webhook-dataset")
    with pelican_metadata.dataset_lock(name):
        status, text = post(app, event(fake), fake.mint_token(NAMESPACE))
    assert status == 503
    assert "busy" in text


@pytest.mark.parametrize("namespace", ["/fake-namespace/..", "/fake-namespace//x", "/./fake-namespace"])
def test_namespace_with_bad_segments_is_rejected(app, fake, namespace):
    body = fake.make_event(namespace, path=f"{namespace}/a.csv", dataset="x")
    status, text = post(app, body, fake.mint_token(namespace))
    assert status == 401
    assert "malformed namespace" in text

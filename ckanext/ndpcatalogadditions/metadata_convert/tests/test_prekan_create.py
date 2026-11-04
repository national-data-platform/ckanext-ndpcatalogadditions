"""Tests for prekan_create.create_in_prekan() - the in-process mirror of
controller.create_package(), used instead of an HTTP self-call (see
prekan_create.py's module docstring for why). Everything it imports (CKAN
itself, and controller.py's helpers) is faked here, the same way conftest.py
fakes package_validation's import path - these tests exercise prekan_create's
own orchestration, not controller.py's or CKAN's real behavior.
"""

import sys
import types

import pytest

from metadata_convert.prekan_create import create_in_prekan


class FakeUser:
    def __init__(self, name="klin_sdsc_edu", email="klin@ucsd.edu"):
        self.name = name
        self.email = email


class FakeOrg:
    def __init__(self, name="burnpro3d"):
        self.name = name


class FakeShowResponse:
    """The collision-check response: package_show against the production catalog."""
    def __init__(self, status_code=404, state=None):
        self.status_code = status_code
        self._state = state

    def json(self):
        return {"result": {"state": self._state}}


@pytest.fixture
def fakes(monkeypatch):
    """Registers fake ckan/ckan.logic/ckan.lib.munge/ckanext.ndpcatalogadditions.controller
    modules, with mutable call-recording so each test can assert on them.
    """
    calls = {"remove_member": [], "package_create_args": None,
             "process_user_and_organization_args": None}

    # -- fake ckan.logic --
    created_dataset = {"id": "abc-123", "name": "dataset-name", "title": "T"}

    def fake_package_create(context, dataset_dict):
        calls["package_create_args"] = (context, dataset_dict)
        return created_dataset

    def fake_get_action(name):
        assert name == "package_create"
        return fake_package_create

    logic_mod = types.ModuleType("ckan.logic")
    logic_mod.get_action = fake_get_action
    ckan_pkg = types.ModuleType("ckan")
    ckan_pkg.logic = logic_mod
    monkeypatch.setitem(sys.modules, "ckan", ckan_pkg)
    monkeypatch.setitem(sys.modules, "ckan.logic", logic_mod)

    # -- fake ckan.lib.munge --
    lib_mod = types.ModuleType("ckan.lib")
    munge_mod = types.ModuleType("ckan.lib.munge")
    munge_mod.munge_title_to_name = lambda title: title.lower().replace(" ", "-")
    lib_mod.munge = munge_mod
    monkeypatch.setitem(sys.modules, "ckan.lib", lib_mod)
    monkeypatch.setitem(sys.modules, "ckan.lib.munge", munge_mod)

    # -- fake ckanext.ndpcatalogadditions.controller --
    controller = types.ModuleType("ckanext.ndpcatalogadditions.controller")
    controller.get_or_create_user = lambda: FakeUser()
    controller.ckan_url = "https://ndp-test.sdsc.edu/catalog"
    controller.headers = {"X-CKAN-API-Key": "fake-sysadmin-key"}

    def fake_process_user_and_organization(user, org_name, capacity='editor'):
        calls["process_user_and_organization_args"] = (user, org_name)
        return FakeOrg(name=org_name)

    controller.process_user_and_organization = fake_process_user_and_organization
    controller.process_private_setting = lambda dataset_dict, email: None
    controller.get_username_from_keycloak_token = lambda: ("klin_sdsc_edu", "klin@ucsd.edu", [], "user-id-123")
    controller.calculate_md5 = lambda s: f"md5({s})"

    def fake_add_or_update_ndp_creator_md5(dataset_json, md5_value):
        dataset_json.setdefault("extras", []).append({"key": "ndp_creator_md5", "value": md5_value})
        return dataset_json

    controller.add_or_update_ndp_creator_md5 = fake_add_or_update_ndp_creator_md5
    controller.remove_member = lambda user, organization: calls["remove_member"].append((user, organization))
    monkeypatch.setitem(sys.modules, "ckanext.ndpcatalogadditions.controller", controller)

    # -- the collision check itself uses the real `requests` module --
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeShowResponse(status_code=404))

    return calls, created_dataset


def test_create_in_prekan_success_with_owner_org(fakes, monkeypatch):
    calls, created_dataset = fakes
    out = create_in_prekan({"title": "T", "name": "dataset-name", "owner_org": "BurnPro3D"})

    assert out == {"created": True, "package": created_dataset}
    assert calls["process_user_and_organization_args"][1] == "BurnPro3D"
    assert len(calls["remove_member"]) == 1  # removed after create, since owner_org was set
    context, dataset_dict = calls["package_create_args"]
    assert dataset_dict["owner_org"] == "BurnPro3D"  # organization.name (from the fake), not a copy of the input dict
    assert any(e["key"] == "ndp_creator_md5" for e in dataset_dict["extras"])


def test_create_in_prekan_without_owner_org_skips_org_handling(fakes):
    calls, _ = fakes
    out = create_in_prekan({"title": "T", "name": "dataset-name"})

    assert out["created"] is True
    assert calls["process_user_and_organization_args"] is None
    assert calls["remove_member"] == []


def test_create_in_prekan_derives_name_when_missing(fakes):
    calls, _ = fakes
    create_in_prekan({"title": "My Dataset Title"})
    _, dataset_dict = calls["package_create_args"]
    assert dataset_dict["name"] == "my-dataset-title"


def test_create_in_prekan_name_collision_blocks_create(fakes, monkeypatch):
    calls, _ = fakes
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeShowResponse(status_code=200, state="active"))

    out = create_in_prekan({"title": "T", "name": "dataset-name"})

    assert out["created"] is False
    assert "used in the NDP catalog" in out["create_error"]
    assert calls["package_create_args"] is None  # never got that far


def test_create_in_prekan_never_raises_on_internal_error(fakes, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ckanext.ndpcatalogadditions.controller",
        sys.modules["ckanext.ndpcatalogadditions.controller"],
    )

    def boom():
        raise RuntimeError("keycloak unreachable")

    sys.modules["ckanext.ndpcatalogadditions.controller"].get_or_create_user = boom
    out = create_in_prekan({"title": "T", "name": "dataset-name"})

    assert out == {"created": False, "create_error": "keycloak unreachable"}

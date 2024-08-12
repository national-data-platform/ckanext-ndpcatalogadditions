"""Tests for views.py."""

import pytest

import ckanext.ndpcatalogadditions.validators as validators


import ckan.plugins.toolkit as tk


@pytest.mark.ckan_config("ckan.plugins", "ndpcatalogadditions")
@pytest.mark.usefixtures("with_plugins")
def test_ndpcatalogadditions_blueprint(app, reset_db):
    resp = app.get(tk.h.url_for("ndpcatalogadditions.page"))
    assert resp.status_code == 200
    assert resp.body == "Hello, ndpcatalogadditions!"

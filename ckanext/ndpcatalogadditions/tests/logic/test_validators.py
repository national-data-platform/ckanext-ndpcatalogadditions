"""Tests for validators.py."""

import pytest

import ckan.plugins.toolkit as tk

from ckanext.ndpcatalogadditions.logic import validators


def test_ndpcatalogadditions_reauired_with_valid_value():
    assert validators.ndpcatalogadditions_required("value") == "value"


def test_ndpcatalogadditions_reauired_with_invalid_value():
    with pytest.raises(tk.Invalid):
        validators.ndpcatalogadditions_required(None)

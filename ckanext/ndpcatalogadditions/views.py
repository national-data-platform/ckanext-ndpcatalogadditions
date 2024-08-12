from flask import Blueprint


ndpcatalogadditions = Blueprint(
    "ndpcatalogadditions", __name__)


def page():
    return "Hello, ndpcatalogadditions!"


ndpcatalogadditions.add_url_rule(
    "/ndpcatalogadditions/page", view_func=page)


def get_blueprints():
    return [ndpcatalogadditions]

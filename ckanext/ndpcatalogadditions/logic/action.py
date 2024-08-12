import ckan.plugins.toolkit as tk
import ckanext.ndpcatalogadditions.logic.schema as schema


@tk.side_effect_free
def ndpcatalogadditions_get_sum(context, data_dict):
    tk.check_access(
        "ndpcatalogadditions_get_sum", context, data_dict)
    data, errors = tk.navl_validate(
        data_dict, schema.ndpcatalogadditions_get_sum(), context)

    if errors:
        raise tk.ValidationError(errors)

    return {
        "left": data["left"],
        "right": data["right"],
        "sum": data["left"] + data["right"]
    }


def get_actions():
    return {
        'ndpcatalogadditions_get_sum': ndpcatalogadditions_get_sum,
    }

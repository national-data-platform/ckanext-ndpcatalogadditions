"""Create a converted package in PREKAN, without an HTTP call to /ndp/package_create.

Why this exists: the obvious way to reuse /ndp/package_create would be an
internal HTTP self-call from within /ndp/metadata_convert. That was tried and
reverted - ckan2 runs CKAN's single-threaded development server (`ckan run`,
no --threaded), so a request can never successfully call itself: the one
worker thread is busy *making* the call and so can never *service* it. It
isn't just slow, it deadlocks until our own client-side timeout gives up.

controller.create_package() itself was deliberately left unmodified (per
earlier decision) rather than refactored into a plain callable, so this
module is a second, independent implementation of its orchestration -
duplicated on purpose, not shared. What IS shared, unchanged, by direct
import: every one of its building blocks (get_or_create_user,
process_user_and_organization, process_private_setting,
get_username_from_keycloak_token, add_or_update_ndp_creator_md5,
calculate_md5, remove_member, ckan_url, headers) and the single genuinely
new thing - calling `logic.get_action('package_create')` - is a plain
in-process CKAN action call, never an HTTP request, so there's no threading
dependency here at all.

KEEP IN SYNC: if controller.create_package()'s own logic changes, this
function's mirrored sequence (below) should be re-checked against it.
"""

def create_in_prekan(ckan_json: dict) -> dict:
    """Create ckan_json as a new PREKAN dataset, mirroring create_package()'s
    own steps exactly, but as a direct in-process call - no HTTP round trip.

    Runs inside the current Flask request context (same as create_package()
    itself): get_or_create_user() etc. read the incoming request's bearer
    token themselves, same as they do for the real /ndp/package_create route.

    Returns {"created": True, "package": {...}} on success, or
    {"created": False, "create_error": "..."} on failure. Never raises.
    """
    # Imported lazily, not at module level: these pull in real CKAN (ckan.logic,
    # ckan.lib.munge) and controller.py's own heavy imports, neither of which
    # this module otherwise needs just to be imported - same reasoning as
    # convert.py's lazy keycloak_token import.
    import requests
    import ckan.logic as logic
    from ckan.lib.munge import munge_title_to_name
    from ckanext.ndpcatalogadditions.controller import (
        add_or_update_ndp_creator_md5,
        calculate_md5,
        ckan_url,
        get_or_create_user,
        get_username_from_keycloak_token,
        headers,
        process_private_setting,
        process_user_and_organization,
        remove_member,
    )

    try:
        user = get_or_create_user()
        dataset_dict = dict(ckan_json)  # create_package() mutates its dict; work on a copy
        organization = None

        if 'owner_org' in dataset_dict:
            organization = process_user_and_organization(user, dataset_dict['owner_org'])
            dataset_dict['owner_org'] = organization.name
        if 'name' not in dataset_dict:
            dataset_dict['name'] = munge_title_to_name(dataset_dict['title'])

        # Same cross-catalog collision check create_package() does: ckan_url
        # here is the *production* catalog (how controller.py's module-level
        # ckan_url is used everywhere else in that file) - a different host
        # than the one this code runs on, so no self-call risk.
        response = requests.post(
            f'{ckan_url}/api/3/action/package_show',
            headers=headers,
            json={'id': dataset_dict['name']},
        )
        if response.status_code == 200:
            remote_package = response.json()
            if remote_package["result"]["state"] == "active":
                return {"created": False,
                        "create_error": f"The dataset name is used in the NDP catalog: {dataset_dict['name']}."}

        process_private_setting(dataset_dict, user.email)

        username, email, roles, user_id = get_username_from_keycloak_token()
        dataset_dict = add_or_update_ndp_creator_md5(dataset_dict, calculate_md5(user_id))

        context = {'user': user.name}
        dataset = logic.get_action('package_create')(context, dataset_dict)

        if organization is not None:
            remove_member(user, organization)

        return {"created": True, "package": dataset}

    except Exception as e:
        return {"created": False, "create_error": str(e)}

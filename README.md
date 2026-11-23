
# ckanext-ndpcatalogadditions

This CKAN plugin provides following endpoints to support NDP users submitting new datasets to Prekan, as well as modifying/deleting datasets that have already been submitted. Users must use a valid Keycloak token as a Bearer token to access these endpoints. Submitted datasets are not formally transferred to the NDP catalog until they have been reviewed.

### For Users Submitting Datasets

* ##### POST <CKAN_URL>/ndp/package_create

  Create a new dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.create.package_create
  
* ##### POST <CKAN_URL>/ndp/package_update

  Update a dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.create.package_update
  
* ##### POST <CKAN_URL>/ndp/package_delete

  Delete a dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.create.package_delete

  Deleting a dataset does not physically remove the dataset entirely.
  The dataset is simply marked as deleted in Prekan so that it no longer appears in the list of
  datasets and in search results. To actually delete a dataset call package_purge as an administrator.
  
* ##### POST <CKAN_URL>/ndp/package_purge

  Purge a dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.delete.dataset_purge

  Please note that the admin privilege is required to invoke this endpoint.
  
* ##### GET/POST <CKAN_URL>/ndp/my_package_list

  List all datasets submitted by the current user that have not yet been reviewed.

* ##### GET/POST <CKAN_URL>/ndp/my_reviewed_package_list

  List all datasets submitted by the current user that have been reviewed.

This plugin creates a new Prekan account for the user, if the information in the Keycloak token used by the user does not have a corresponding account in Prekan.

The plugin also creates a new Prekan organization if the dataset belongs to an organization that does not exist in Prekan.

When a user includes the organization to which the dataset belongs in a request for the dataset, the user will be added to that organization as an editor.

### For Users Reviewing Datasets

Only users with admin or review identities can use the following endpoints:

* ##### POST <CKAN_URL>/ndp/package_approve

  Approve a dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.delete.dataset_purge

  This endpoint copies the specified dataset to the production CKAN catalog. The correspondence between the dataset and its creator and the organization it 
  belongs to is also copied to the production CKAN's catalog. After a successful copy operation, the dataset in Prekan is marked as deleted, and associate
  the reviewer name, the review time and the review result “approved” to the dataset.
  
* ##### POST <CKAN_URL>/ndp/package_reject

  Reject a dataset in Prekan by submitting a JSON string with the fields specified in this link:
  https://docs.ckan.org/en/2.10/api/#ckan.logic.action.delete.dataset_purge

  This endpoint will make the specified dataset as deleted in Prekan, and the reviewer name, the review time and the review result “rejected” are assoicated
  to the dataset.

* ##### GET/POST <CKAN_URL>/ndp/package_list

  List all datasets that have not yet been reviewed.

## Requirements

Compatibility with core CKAN versions:

| CKAN version    | Compatible?   |
| --------------- | ------------- |
| 2.6 and earlier | not tested    |
| 2.7             | yes           |
| 2.8             | yes           |
| 2.9             | yes           |


* "yes"
* "not tested" - I can't think of a reason why it wouldn't work
* "not yet" - there is an intention to get it working
* "no"


## Installation

To install ckanext-ndpcatalogadditions:

1. Activate your CKAN virtual environment, for example:

     . /usr/lib/ckan/default/bin/activate

2. Clone the source and install it on the virtualenv

    git clone https://github.com/SDSC/ckanext-ndpcatalogadditions.git
    cd ckanext-ndpcatalogadditions
    pip install -e .
	pip install -r requirements.txt

3. Add `ndpcatalogadditions` to the `ckan.plugins` setting in your CKAN
   config file (by default the config file is located at
   `/etc/ckan/default/ckan.ini`).

4. Restart CKAN. For example if you've deployed CKAN with Apache on Ubuntu:

     sudo service apache2 reload

## Configuration

All settings but one are environment variables on the CKAN container, read
when the extension loads. Change a value by updating the environment and
recreating the container. The exception, `ckanext.ndp.jupyterhub_endpoint`, is
a `ckan.ini` option (below).

This extension runs on the Prekan (staging) instance and talks to a separate
production CKAN, which curator approval publishes to.

### Core

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `CKANEXT__KEYCLOAK__REDIRECT_URI` | yes | none | The Keycloak SSO redirect URI, e.g. `https://ndp-test.sdsc.edu/catalog2/user/sso_login`. The production CKAN URL is derived from it by dropping `/user/sso_login` and replacing `catalog2` with `catalog`; org and user lookups, the published-name check and approval all go there. CKAN fails to start if it is unset. |
| `CKANEXT__NDPCATALOGADDITIONS__API_KEY` | yes | none | API token for the production CKAN, sent in the `Authorization` header on every call to it. |
| `CKANEXT__KEYCLOAK__SERVER_URL` | yes | none | Keycloak server used to verify bearer tokens on API requests. |
| `CKANEXT__KEYCLOAK__REALM_NAME` | yes | none | Keycloak realm for the same check. |
| `NDP_KEYCLOAK_API_URL` | no | `https://idp.nationaldataplatform.org/api` | NDP identity API: user information from a token, group names, and group attributes for restricted datasets. |
| `NDP_CLIENT` | for restricted datasets | `NDP_CLIENT` | Client id used to log in to the NDP identity API. |
| `NDP_SECRET` | for restricted datasets | none | Secret for that client. |
| `CKAN_SITE_URL` | yes | none (`https://ndp-test.sdsc.edu` for metrics) | Base URL of the NDP site. Approval and rejection emails are sent through `<CKAN_SITE_URL>/workspaces-api/email/send_email`, and the daily metrics page links to it. Also CKAN core's own site URL setting. |
| `email_secret` | for emails | none | Shared secret sent with each email request. Note the lowercase name. |
| `SOLR_INDEX_URL` | no | `http://awesome-compute.sdsc.edu:8081/v1/ckan-api/secure/add_to_index` | External search-index service called for each dataset approved into production. |
| `CKANEXT__NDPCATALOGADDITIONS__POP_SECRET_TOKEN` | for `/ndp/get_ep_prekan_token` | none | Shared secret callers of that endpoint send in the `X-Secret-Token` header. |
| `CONTEXTUAL_INSIGHTS_URL` | no | `https://vdc-192.chpc.utah.edu/testddapi/pipelines/trigger` | Pipeline triggered after approval for datasets with the extra `enable_contextual_insight` set to `true`. |
| `CONTEXTUAL_INSIGHTS_USERNAME` | no | `utah_university` | Basic-auth user for that pipeline. |
| `CONTEXTUAL_INSIGHTS_PASSWORD` | for contextual insights | none | Basic-auth password for that pipeline. |

`ckan.ini` option:

| Option | Required | Default | What it does |
| --- | --- | --- | --- |
| `ckanext.ndp.jupyterhub_endpoint` | no | none | JupyterHub URL exposed to templates through the `ndp_jupyterhub_endpoint` helper. |

### Pelican metadata publish

These settings configure the receiver for Pelican `object.committed` webhooks
at `POST /ndp/pelican_metadata_publish`. Leave them unset to disable it: every
event then gets a 500 and Pelican keeps retrying.

The receiver is also bound to `POST /`, Prekan's site root, because the test
origin was configured with the bare site URL. Any POST to `/` goes to the
receiver (CKAN's own `/` is GET-only, and the receiver rejects anything without
a valid Pelican token). Remove that rule in `plugin.py` once origins use the
specific path.
See [the design doc](docs/pelican-metadata-publish-design.md), section 6, for
the uploader metadata convention.

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `CKANEXT__NDPCATALOGADDITIONS__PELICAN_TRUSTED_FEDERATION` | yes | none | URL of the only Pelican federation whose events are accepted, e.g. `https://osg-htc.org`. Signing keys are discovered from it. Events from any other federation get a 401. |
| `CKANEXT__NDPCATALOGADDITIONS__PELICAN_WEBHOOK_URL` | yes | none | This receiver's URL exactly as configured in Pelican. Tokens must name it as their audience. |
| `CKANEXT__NDPCATALOGADDITIONS__PELICAN_NAMESPACE_ORG_MAP` | yes | `{}` | JSON object mapping each Pelican namespace to a CKAN org name, e.g. `{"/example-ns": "example-org"}`. Exact match only. Events for a namespace not in the map get a 422 and are dropped. A mapped org missing from Prekan is created automatically, so check names against production. If the value is not valid JSON, it is logged and treated as empty. |
| `CKANEXT__NDPCATALOGADDITIONS__PELICAN_SERVICE_USER` | no | `pelican-ingest-bot` | CKAN user that creates and owns Pelican datasets. Created on first use. |
| `CKANEXT__NDPCATALOGADDITIONS__PELICAN_SERVICE_USER_EMAIL` | no | `pelican-ingest-bot@nationaldataplatform.org` | Email for that user. |

## Developer installation

To install ckanext-ndpcatalogadditions for development, activate your CKAN virtualenv and
do:

    git clone https://github.com/SDSC/ckanext-ndpcatalogadditions.git
    cd ckanext-ndpcatalogadditions
    python setup.py develop
    pip install -r dev-requirements.txt


## Tests

To run the tests, do:

    pytest --ckan-ini=test.ini


## Releasing a new version of ckanext-ndpcatalogadditions

If ckanext-ndpcatalogadditions should be available on PyPI you can follow these steps to publish a new version:

1. Update the version number in the `setup.py` file. See [PEP 440](http://legacy.python.org/dev/peps/pep-0440/#public-version-identifiers) for how to choose version numbers.

2. Make sure you have the latest version of necessary packages:

    pip install --upgrade setuptools wheel twine

3. Create a source and binary distributions of the new version:

       python setup.py sdist bdist_wheel && twine check dist/*

   Fix any errors you get.

4. Upload the source distribution to PyPI:

       twine upload dist/*

5. Commit any outstanding changes:

       git commit -a
       git push

6. Tag the new release of the project on GitHub with the version number from
   the `setup.py` file. For example if the version number in `setup.py` is
   0.0.1 then do:

       git tag 0.0.1
       git push --tags

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)

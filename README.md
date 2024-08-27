
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

  List all datasets submitted by the current user.

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

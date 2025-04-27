
import os
import random
import string
import traceback
import requests
import json
import hashlib
import re
from datetime import datetime

import ckan.model as model
import ckan.logic as logic
from ckan.plugins import toolkit
from ckan.authz import is_sysadmin
from ckan.lib.munge import munge_title_to_name
from ckan.lib.dictization.model_dictize import package_dictize
from ckanext.ndpcatalogadditions.keycloak_token import get_user_info
from flask import request, jsonify


server_url = os.getenv('CKANEXT__KEYCLOAK__REDIRECT_URI')
ckan_url = server_url.replace('/user/sso_login', '')
ckan_url = ckan_url.replace('catalog2', 'catalog')
api_key = os.getenv('CKANEXT__NDPCATALOGADDITIONS__API_KEY')
headers = {
    'X-CKAN-API-Key': api_key,
    'Content-Type': 'application/json'
}
site_url = os.getenv('CKAN_SITE_URL')
email_secret=os.getenv('email_secret')

NDP_ADMIN_API = os.getenv('NDP_ADMIN_API_URL')
NDP_ADMIN_USERNAME = os.getenv('NDP_ADMIN_USERNAME')
NDP_ADMIN_PASSWORD = os.getenv('NDP_ADMIN_PASSWORD')

def post_request(api_url, endpoint, data, headers=None):
    url = f"{api_url}{endpoint}"
    response = requests.post(url, json=data, headers=headers)
    return response.json()

def get_request(api_url, endpoint, headers=None):
    url = f"{api_url}{endpoint}"
    response = requests.get(url, headers=headers)
    return response.json()


def generate_random_password(length=32):
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for i in range(length))


def is_reviewer():
    # Get the Authorization header
    auth_header = request.headers.get('Authorization')

    # Extract the Bearer Token if the header exists
    if auth_header and auth_header.startswith('Bearer '):
        bearer_token = auth_header[len('Bearer '):]
    else:
        raise ValueError('Missing or invalid KeyCloak token')

    user_info = get_user_info(bearer_token)
    return "data_approver" in user_info['roles']


def is_admin():
    # Get the Authorization header
    auth_header = request.headers.get('Authorization')
    
    # Extract the Bearer Token if the header exists
    if auth_header and auth_header.startswith('Bearer '):
        bearer_token = auth_header[len('Bearer '):]
    else:
        raise ValueError('Missing or invalid KeyCloak token')

    user_info = get_user_info(bearer_token)
    return "ndp_admin" in user_info['roles']


def get_or_create_user():

    # Get the Authorization header
    auth_header = request.headers.get('Authorization')

    # Extract the Bearer Token if the header exists
    if auth_header and auth_header.startswith('Bearer '):
        bearer_token = auth_header[len('Bearer '):]
    else:
        raise ValueError('Missing or invalid KeyCloak token')
    user_info = get_user_info(bearer_token)
    
    username = user_info['username'].replace('.', '_').replace('@', '_')
    # user = model.User.get(username)
    user = model.User.by_email(user_info['email'])
    if not user:
        # Create a new user
        user = model.User(name=username, email=user_info['email'])
        user.fullname = user_info['name']
        user.password = generate_random_password()
        user.state = model.State.ACTIVE
        model.Session.add(user)
        model.Session.commit()
    return user


def process_user_and_organization(user, org_name):
    organization = model.Group.get(munge_title_to_name(org_name))
    if not organization:
        # Check remote site                                                                                                                                                  
        data = { 'id': org_name }
        response = requests.post(f'{ckan_url}/api/3/action/organization_show', headers=headers, json=data)
        if response.status_code == 200:
            remote_organization = response.json()['result']
            organization = model.Group.get(remote_organization['name'])
            if not organization:
                del remote_organization['id']
                organization = model.Group(name=remote_organization['name'],
                                           title=remote_organization['title'],
                                           description=remote_organization['description'],
                                           type='organization',
                                           is_organization=True)
                model.Session.add(organization)
                model.Session.commit()
        else:    
            # Create the organization object
            organization = model.Group(name=munge_title_to_name(org_name),
                                       title=org_name,
                                       description="Created by admin when creating a new dataset",
                                       type='organization',
                                       is_organization=True)
            model.Session.add(organization)
            model.Session.commit()
         
    member = model.Member(group=organization, table_id=user.id, table_name='user', capacity='editor')
    model.Session.add(member)
    model.Session.commit()   
    return organization
    

def get_or_create_remote_user(username, email, fullname):

    user_show_url = f'{ckan_url}/api/3/action/user_show'
    response = requests.get(user_show_url, headers=headers, params={'id': username})
    
    if response.status_code == 200:
        user_info = response.json()['result']
        return user_info
    
    elif "Not Found" in response.text:
        # create a new user account
        api_url = f'{ckan_url}/api/3/action/user_create'

        # User information
        data = {
            'name': username,
            'email': email,
            'fullname': fullname,
            'password': generate_random_password(),    
        }

        # Make the API request
        response = requests.post(api_url, data=json.dumps(data), headers=headers)

        # Check the response
        if response.status_code == 200:
            new_user = response.json()['result']
            return new_user
        else:
            raise ValueError(f"Error creating user: {response.text}")
    else:
        raise ValueError(f"Failed to retrieve user info: {response.text}")


def process_remote_user_and_organization(remote_user, organization):
    
    data = { 'id': organization.name }
    response = requests.post(f'{ckan_url}/api/3/action/organization_show', headers=headers, json=data)
    if response.status_code == 200:
        remote_organization = response.json()['result']
    else:
        # create a new organization in the remote CKAN
        org_data = {
            "name": organization.name,
            "title": organization.title,
            "description": organization.description
        }
        response = requests.post(f'{ckan_url}/api/3/action/organization_create', headers=headers, json=org_data)
        if response.status_code == 200:
            remote_organization = response.json()['result']
        else:
            raise ValueError(f"Failed to create organization: {response.text}")
    
    # add the user as an editor to the remote organization
    member_data = {
        'id': remote_organization['id'],
        'username': remote_user['name'],
        'role': 'editor'
    }
    response = requests.post(f'{ckan_url}/api/3/action/organization_member_create', headers=headers, json=member_data)
    if response.status_code != 200:
        raise Value(f"Failed to add user to organization: {response.text}")

    return remote_organization
    

def create_api_token(username):
    api_url = f'{ckan_url}/api/3/action/api_token_create'
    data = {
        'name': 'dataset_token',
        'user': username
    }
    response = requests.post(api_url, data=json.dumps(data), headers=headers)
    if response.status_code == 200:
        new_token = response.json()['result']['token']
        return new_token
    else:
        raise ValueError(f"Error creating API token: {response.text}")


def delete_api_token(token):
    api_url = f'{ckan_url}/api/3/action/api_token_revoke'
    data = {
        'token': token,
    }
    response = requests.post(api_url, data=json.dumps(data), headers=headers)
    if response.status_code != 200:
        raise ValueError(f"Error creating API token: {response.text}")
    

def save_remote_dataset(remote_user, dataset):
    token = create_api_token(remote_user['name'])
    try:
        api_url = f"{ckan_url}/api/3/action/package_create"
        new_headers = {
            'X-CKAN-API-Key': token,
            'Content-Type': 'application/json'
        }
        response = requests.post(api_url, data=json.dumps(dataset), headers=new_headers)
        if response.status_code == 200:
            created_package = response.json()['result']
            return created_package
        else:
            raise ValueError(f"Failed to create dataset: {response.text}   {json.dumps(dataset, indent=4)}")
    finally:
        delete_api_token(token)

    
def get_accept_notification_text(fullname, title, submit_date):
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">

    <p>Dear {fullname},</p>

    <p>Thank you for submitting your dataset, <strong>“{title},”</strong> to the National Data Platform (NDP) on {submit_date[:10]}.</p>

    <p>We are pleased to inform you that, after careful evaluation by our reviewers, your dataset meets the NDP acceptance criteria and has been recognized for its high quality. As a result, we are delighted to include it in the NDP Catalog.</p>

    <p>We sincerely appreciate your valuable contribution and hope you will continue to support the NDP by sharing more high-quality datasets in the future.</p>

    <p>Best regards,</p>

    <p>The NDP Team</p>

</body>
</html>
"""


def get_reject_notification_text(fullname, title, submit_date):
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Template</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">

    <p>Dear {fullname},</p>

    <p>Thank you for submitting your dataset, <strong>“{title}”</strong>, to the National Data Platform (NDP) on {submit_date[:10]}. We appreciate your time and effort in contributing to our platform.</p>

    <p>After a thorough review by our team, we regret to inform you that your dataset does not currently meet the NDP acceptance criteria. While we are unable to include it in the NDP Catalog at this time, we encourage you to review our guidelines and consider making revisions.</p>

    <p>We would be happy to review a revised submission, should you choose to update your dataset in line with our criteria. Your contributions are important to us, and we hope to see more of your work in the future.</p>

    <p>Best regards,</p>

    <p>The NDP Team</p>

</body>
</html>
"""


def send_email(email_address, email_text):
    # Define the URL and headers
    url = f'{site_url}/workspaces-api/email/send_email'
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/json',
    }

    # Define the data payload
    data = {
        "to_email": email_address,
        "subject": "Your Dataset Submission to NDP",      
        "body": email_text,         
        "secret": email_secret       
    }

    # Make the POST request
    response = requests.post(url, headers=headers, json=data)

    # Check the response
    if response.status_code != 200:
        raise ValueError(f"Failed to send email. {response.text}")
    

def create_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()
            if 'owner_org' in dataset_dict.keys():
                organization = process_user_and_organization(user, dataset_dict['owner_org'])
                dataset_dict['owner_org'] = organization.name
            if not 'name' in dataset_dict.keys():
                dataset_dict['name'] = munge_title_to_name(dataset_dict['title'])

            # check if the name is used in NDP catalog
            data = { 'id': dataset_dict['name'] }
            response = requests.post(f'{ckan_url}/api/3/action/package_show', headers=headers, json=data)
            if response.status_code == 200:
                remote_package = response.json()
                if remote_package["result"]["state"] == "active":
                    raise ValueError(f"The dataset name is used in the NDP catalog: {dataset_dict['name']}.")

            process_private_setting(dataset_dict, user.email)
            
            context = {'user': user.name}
            dataset = logic.get_action('package_create')(context, dataset_dict)                
            return dataset
        except Exception as e:
            traceback.print_exc() 
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def update_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()
            if 'owner_org' in dataset_dict.keys():
                organization = process_user_and_organization(user, dataset_dict['owner_org'])
                dataset_dict['owner_org'] = organization.name

            logger.info("Process private setting")    
            process_private_setting(dataset_dict, user.email)
            context = {'user': user.name}
            result = logic.get_action('package_update')(context, dataset_dict)
            return result
        except Exception as e:
            traceback.print_exc()            
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def delete_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()            
            context = {'user': user.id}
            logic.get_action('package_delete')(context, dataset_dict)
            return f"The package '{dataset_dict['id']}' is deleted."
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def purge_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()
            context = {'user': user.id}
            logic.get_action('dataset_purge')(context, dataset_dict)
            return f"The package '{dataset_dict['id']}' is purged."
        except logic.NotAuthorized:
            return "Not authorized to purge this dataset", 401            
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def list_my_packages():
    if request.method == 'POST' or request.method == 'GET':
        try:
            user = get_or_create_user()
            context = {'user': user.id}
            search_dict = {
                'q': f'creator_user_id:{user.id}',
                'include_private': True,
                'rows': 1000  
            }
            result = logic.get_action('package_search')(context, search_dict)
            return result
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def list_my_reviewed_packages():
    if request.method == 'POST' or request.method == 'GET':
        try:
            user = get_or_create_user()
            deleted_datasets = model.Session.query(model.Package) \
                                            .filter(model.Package.state == 'deleted') \
                                            .filter(model.Package.creator_user_id == user.id) \
                                            .join(model.PackageExtra) \
                                            .filter(model.PackageExtra.key == 'approval_status') \
                                            .all()
            
            # Convert the result into the same format as package_search
            context = {'model': model, 'session': model.Session, 'user': user.id}
            package_dicts = [package_dictize(pkg, context) for pkg in deleted_datasets]

            # Convert the result into the same format as package_search
            result = {
                'count': len(package_dicts),
                'results': package_dicts,
                'facets': {},
                'search_facets': {}
            }
            return result
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods
    

def approve_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()

            if not user.sysadmin and not is_reviewer():
                return "Not authorized to approve this dataset.", 401
            
            # actions in the production catalog
            #    1. find the creator and the owner_org of the dataset
            #    2. create a user for the creator if doesn't exist 
            #    3. create a organization for the owner_org if doesn't exists 
            #    4  add the creator as an editor to the owner_org
            #    5. create the dataset

            # get the dataset with ignore_auth. Note that the reviewer may not has the permission to view this package if it is private
            context = {'ignore_auth': True}
            dataset = logic.get_action('package_show')(context, {'id': dataset_dict['id']})
            if dataset['state'] == 'deleted':
                return f"The dataset '{dataset['name']}' was already deleted. Can not approve it.", 401
                
            creator_user_id = dataset['creator_user_id']

            # create a remote user if doesn't exist
            creator = model.User.get(creator_user_id)
            creator_name = creator.name
            email = creator.email
            fullname = creator.fullname
            remote_user = get_or_create_remote_user(creator_name, email, fullname)

            # create a remote organization if doesn't exist and add the remote user as an editor
            remote_organization = None
            if 'owner_org' in dataset.keys():
                organization = model.Group.get(dataset['owner_org'])
                remote_organization = process_remote_user_and_organization(remote_user, organization)

            # delete dataset id
            del dataset['id']
            
            # delete the creator_user_id
            del dataset['creator_user_id'] 

            # change the owner_org id
            if remote_organization:
                dataset['owner_org'] = remote_organization['id']
                del dataset['organization']

            # delete package_id from each resource
            if 'resources' in dataset.keys():
                for resource in dataset['resources']:
                    del resource['package_id']
                    del resource['id']
                    
            # save the dataset to the remote CKAN
            remote_dataset = save_remote_dataset(remote_user, dataset)
                
            # action in the local catalog
            #    1. add the approval information to the dataset
            #    2. delete the dataset

            extras = dataset['extras']
            extras.append({'key': 'approval_status', 'value': 'approved'})
            extras.append({'key': 'approval_user', 'value': user.name})
            extras.append({'key': 'approval_time', 'value': datetime.now().isoformat()})
            update_dict = {
                'id': dataset_dict['id'],
                'extras': extras
            }
            logic.get_action('package_patch')(context, update_dict)

            # delete this dataset with ignore_auth context
            logic.get_action('package_delete')(context, dataset_dict)

            # send an accept notification
            title = dataset['title']
            submit_date = dataset['metadata_created']
            send_email(email, get_accept_notification_text(fullname, title, submit_date))
            
            # return f"The package '{dataset['name']}' is moved to the production catalog."
            return remote_dataset
        
        except logic.NotAuthorized:
            traceback.print_exc()
            return "Not authorized to approve this dataset.", 401            
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods
    


def reject_package():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            dataset_dict = request.get_json()

            if not user.sysadmin and not is_reviewer():
                return "Not authorized to approve this dataset.", 401

            # fetch the dataset
            context = {'ignore_auth': True}
            dataset = logic.get_action('package_show')(context, {'id': dataset_dict['id']})

            # fetch the creator
            creator_user_id = dataset['creator_user_id']
            creator = model.User.get(creator_user_id)
            email = creator.email
            fullname = creator.fullname
            
            # Note that the reviewer may not has the permission to view this package if it is private
            context = {'ignore_auth': True}
            extras = dataset['extras']
            extras.append({'key': 'approval_status', 'value': 'rejected'})
            extras.append({'key': 'approval_user', 'value': user.name})
            extras.append({'key': 'approval_time', 'value': datetime.now().isoformat()})
            update_dict = {
                'id': dataset_dict['id'],
                'extras': extras
            }
            logic.get_action('package_patch')(context, update_dict)
            logic.get_action('package_delete')(context, {'id': dataset_dict['id']})

            # send a reject notification
            title = dataset['title']
            submit_date = dataset['metadata_created']
            send_email(email, get_reject_notification_text(fullname, title, submit_date))

            return f"The dataset '{dataset_dict['id']}' is rejected and deleted."
        except logic.NotAuthorized:
            traceback.print_exc()
            return "Not authorized to approve this dataset.", 401            
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def list_all_packages():
    if request.method == 'POST' or request.method == 'GET':
        try:
            user = get_or_create_user()
            if not user.sysadmin and not is_reviewer():
                return "Not authorized to list all datasets.", 401

            context = {'user': 'ckan_admin', 'ignore_auth': True}
            search_dict = {
                'q': '*:*',
                'rows': 1000,
                'include_private': True 
            }
            results = logic.get_action('package_search')(context, search_dict)
            # return results
        
            package_list = results["results"]

            # packages = []
            for package in package_list:
                creator_id = package.get('creator_user_id')
                if creator_id:
                    user = model.Session.query(model.User).filter(model.User.id == creator_id).first()
                    if user:
                        package['creator_fullname'] = user.fullname
                        package['creator_email'] = user.email
                        # if package['creator_fullname'] and package['creator_email']:
                        #    packages.append(package)
            return results

            
            # Convert the result into the same format as package_search
            # result = {
            #    'count': len(packages),
            #    'results': packages,
            #    'facets': {},
            #    'search_facets': {}
            #}
            #return result

            
            return json.dumps(packages, indent=4)
            
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


##################################################################

import logging

logger = logging.getLogger(__name__)


def get_username_from_keycloak_token():

    # Get the Authorization header
    auth_header = request.headers.get('Authorization')

    # Extract the Bearer Token if the header exists
    if auth_header and auth_header.startswith('Bearer '):
        bearer_token = auth_header[len('Bearer '):]
    else:
        raise ValueError('Missing or invalid KeyCloak token')


    
    server_url = os.getenv('CKANEXT__KEYCLOAK__SERVER_URL')
    realm = os.getenv('CKANEXT__KEYCLOAK__REALM_NAME')
    client_id = "account"

    from ckanext.ndp.keycloak_token import verify_and_decode_token
    decoded_token = verify_and_decode_token(bearer_token, server_url, realm, client_id)
    logger.info(f"decoded_token: {decoded_token}")    
        
        

    
    user_info = get_user_info(bearer_token)
    username = user_info['username'].replace('.', '_').replace('@', '_')
    return username, user_info['email'], user_info['roles'], user_info['id']


def get_allowed_groups(package):
    allowed_groups = []
    if "extras" in package:
        extras = package["extras"]
        for extra in extras:
            if extra["key"] == 'allowed_groups':
                allowed_groups = json.loads(extra["value"])
                break
    return allowed_groups


def get_allowed_users(package):
    allowed_users = []
    if "extras" in package:
        extras = package["extras"]
        for extra in extras:
            if extra["key"] == 'allowed_users':
                allowed_users = json.loads(extra["value"])
                break
    return allowed_users


def encrpyt_allowed_users(package):
    if "extras" in package:
        extras = package["extras"]
        for extra in extras:
            if extra["key"] == 'allowed_users':
                allowed_users = json.loads(extra["value"])
                extra["value"] = [ calculate_md5(email) for email in allowed_users ] 
                return
                

def is_user_in_groups(user_id, allowed_groups):
    logger.info(f"Check if {user_id} is in the groups {allowed_groups}")

    ndp_admin_token = post_request(NDP_ADMIN_API, "/auth/token", {
        "username": NDP_ADMIN_USERNAME,
        "password": NDP_ADMIN_PASSWORD
    })["access_token"]

    ndp_admin_headers = {
        "Authorization": f"Bearer {ndp_admin_token}"
    }
    
    user_groups = get_request(NDP_ADMIN_API, f"/users/full-permissions?userID={user_id}", headers=ndp_admin_headers)
    logger.info(f"user_groups: {json.dumps(user_groups)}")

    for acl_entry in allowed_groups:
        client_name = acl_entry['client']
        group_name = acl_entry['group']
        subgroup_name = acl_entry['subgroup']
        
        # Check if the client exists in membership data
        if client_name in user_groups:
            client_data = user_groups[client_name]
            
            # Check if the group exists in this client
            if 'groups' in client_data and group_name in client_data['groups']:
                group_data = client_data['groups'][group_name]
                
                # If subgroup is empty/None, just check group access
                if not subgroup_name:
                    return True
                
                # Check if the subgroup exists in this group
                if ('subgroups' in group_data and 
                    subgroup_name in group_data['subgroups']):
                    return True
    
    # No matches found
    return False
    

def get_remote_user(username):
    user_show_url = f'{ckan_url}/api/3/action/user_show'
    response = requests.get(user_show_url, headers=headers, params={'id': username})
    if response.status_code == 200:
        user_info = response.json()['result']
        return user_info
    else:
        raise ValueError(f"Failed to retrieve user info: {username}")


def update_creator_fingerprint(data, fingerprint_value):
    # Check if extras exists
    if "extras" not in data:
        # If extras doesn't exist, create it with creator_fingerprint
        data["extras"] = [{"key": "creator", "value": fingerprint_value}]
    else:
        # If extras exists, check if creator_fingerprint is in it
        fingerprint_exists = False
        for item in data["extras"]:
            if item.get("key") == "creator":
                # Update existing fingerprint
                item["value"] = fingerprint_value
                fingerprint_exists = True
                break
                
        # If creator_fingerprint doesn't exist, add it
        if not fingerprint_exists:
            data["extras"].append({"key": "creator", "value": fingerprint_value})
    
    # Convert back to JSON string
    # logger.info(f"update_creator_fingerprint: {json.dumps(data)}")
    return data


def calculate_md5(input_string):
    # Convert string to bytes if it's not already
    if isinstance(input_string, str):
        input_bytes = input_string.encode('utf-8')
    else:
        input_bytes = input_string
        
    # Calculate MD5
    md5_hash = hashlib.md5()
    md5_hash.update(input_bytes)
    
    # Return hexadecimal representation
    return md5_hash.hexdigest()


def check_group_exists(client_list, path_json):
    """
    Check if the specified client, group, and optionally subgroup exist in the client list.
    Handles missing keys in path_json gracefully.
    
    Args:
        client_list (dict): The client list JSON structure
        path_json (dict): JSON with client, group, and optional subgroup keys
        
    Returns:
        bool: Success status
    """
    # Check if client_list is a valid dictionary
    if not isinstance(client_list, dict):
        return False
    
    # Validate required keys in path_json
    if not isinstance(path_json, dict):
        return False
    
    if "client" not in path_json:
        return False
    
    client_name = path_json.get("client")
    
    # Check if client exists
    if client_name not in client_list:
        return False
    
    client = client_list[client_name]
    
    # If only checking for client existence
    if "group" not in path_json:
        return True
    
    group_name = path_json.get("group")
    
    # Check if groups key exists and is a dictionary
    if "groups" not in client or not isinstance(client["groups"], dict):
        return False
    
    # Check if specific group exists
    if group_name not in client["groups"]:
        return False
    
    group = client["groups"][group_name]
    
    # If no subgroup specified, we've confirmed the group exists
    if "subgroup" not in path_json or path_json["subgroup"] is None:
        return True
    
    subgroup_name = path_json["subgroup"]
    
    # Check if subgroups key exists and is a dictionary
    if "subgroups" not in group or not isinstance(group["subgroups"], dict):
        return False
    
    # Check if specific subgroup exists
    if subgroup_name not in group["subgroups"]:
        return False
    
    # If we get here, the full path exists
    return True


def validate_groups(groups):
    logger.info(f"Validate groups {groups}")

    ndp_admin_token = post_request(NDP_ADMIN_API, "/auth/token", {
        "username": NDP_ADMIN_USERNAME,
        "password": NDP_ADMIN_PASSWORD
    })["access_token"]

    ndp_admin_headers = {
        "Authorization": f"Bearer {ndp_admin_token}"
    }

    list_clients = get_request(NDP_ADMIN_API, "/clients", headers=ndp_admin_headers)
    # logger.info(json.dumps(list_clients, indent=4))
    # logger.info(json.dumps(groups, indent=4))

    for group in groups:
        if not check_group_exists(list_clients, group):
            raise ValueError(f"Invalid group: {json.dumps(group)}")
    

def validate_users(emails):
    logger.info(f"Validate users {emails}")
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    for email in emails:
        if not bool(re.match(pattern, email)):
            raise ValueError(f"Invalid email: {email}")


def process_private_setting(dataset_dict, email):
    if 'private' in dataset_dict.keys() and  dataset_dict['private']:
        # setup/overwrite extras/creator_fingerprint
        # update_creator_fingerprint(dataset_dict, calculate_md5(email))
        update_creator_fingerprint(dataset_dict, email)
        
        # get allowed groups
        allowed_groups = get_allowed_groups(dataset_dict)
        logger.info(f"Found the allowed_groups: {allowed_groups}")

        # validate allowed groups
        validate_groups(allowed_groups)

        # get allowed users
        allowed_users = get_allowed_users(dataset_dict)
        logger.info(f"Found the allowed_users: {allowed_users}")

        # validate allowed userss
        validate_users(allowed_users)
        
    if (not 'private' in dataset_dict.keys() or not dataset_dict['private']) and "extras" in dataset_dict:
        # remove extras/allowed_groups and extras/creator_fingerprint
        dataset_dict["extras"] = [item for item in dataset_dict["extras"] if \
                                  item.get("key") != "allowed_groups" and item.get("key") != "creator" and item.get("key") != "allowed_users"]

    
def get_approved_package():
    if request.method == 'POST' or request.method == 'GET':
        # Get the ID parameter from the request
        id = toolkit.request.args.get('id')
        if not id:
            return f'Error: Missing required parameter: id', 401
        logger.info(f"Got get_approved_package request: {id}")
        
        try:
            # Get the package from the given id
            package_show_url = f'{ckan_url}/api/3/action/package_show'
            response = requests.get(package_show_url, headers=headers, params={'id': id})
            if response.status_code == 200:
                package = response.json()['result']
                if package["state"] == 'active':
                    if package["private"]:
                        # Get CKAN username
                        username, email, roles, user_id = get_username_from_keycloak_token()
                        logger.info(f"Got CKAN username and email and roles: {username}, {email}, {roles}")
            
                        # Check access permission by groups
                        allowed_groups = get_allowed_groups(package)
                        logger.info(f"Found the allowed_groups: {allowed_groups}")

                        allowed_users = get_allowed_users(package)
                        logger.info(f"Found the allowed_users: {allowed_users}")

                        # check if the user is the creator or an admin
                        try:
                            user = get_remote_user(username)
                            logger.info(f"Found ckan user id: {user['id']}, sysadmin: {user['sysadmin']}")
                            if (user['sysadmin'] or is_admin() or package["creator_user_id"] == user['id']):
                                logger.info(f"The current user is { 'an admin' if user['sysadmin'] else 'the creator'}")
                                return json.dumps(package, indent=4)
                        except:
                            # this user doesn't have a CKAN account
                            pass

                        # check if the user is in the access control list
                        try:
                            if is_user_in_groups(user_id, allowed_groups) or email in allowed_users:
                                logger.info("The current user is not the creator and an admin")
                                encrpyt_allowed_users(package)
                                return json.dumps(package, indent=4)
                        except:
                            traceback.print_exc()
                            pass

                        # deny this user to access this package
                        return f'Error: Unauthorized', 401
                        
                    else:
                        return json.dumps(package, indent=4)
                else:
                    return f'Error: the package is not active', 400
            return f"Not Found Error", 400
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 400
        
    return "Method not allowed", 405  # For unsupported methods


def my_approved_packages():
    try:
        # Get CKAN username
        username, email, roles, id = get_username_from_keycloak_token()
        logger.info(f"Got CKAN username and email: {username}, {email}")

        # Get CKAN user
        try:
            user = get_remote_user(username)
            logger.info(f"Found ckan user id: {user['id']}")
        except:
            # this user doesn't have a CKAN account                                                                                                
            return "[]"
            
        # Search packages by creator_user_id
        package_search_url = f'{ckan_url}/api/3/action/package_search'
        response = requests.get(package_search_url,
                                headers=headers,
                                params={'fq': f"creator_user_id:{user['id']}",
                                        'include_private': True,
                                        'rows': 1000})
        if response.status_code == 200:
            packages = response.json()['result']
            return json.dumps(packages, indent=4)
        else:
            return response.text, 400
    except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 400


def update_my_approved_package():
    if request.method == 'POST':
        try:
            dataset_dict = request.get_json()
            logger.info(f"Update package: {dataset_dict['id'] if 'id' in dataset_dict else dataset_dict['name']}")

            # Get CKAN username
            username, email, roles, id = get_username_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}")

            token = create_api_token(username)
            try:
                # should we allow to change a public dataset to a private dataset?
                api_url = f"{ckan_url}/api/3/action/package_update"
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }

                # Validate allowed_groups
                process_private_setting(dataset_dict, email) 
                response = requests.post(api_url, data=json.dumps(dataset_dict), headers=new_headers)
                if response.status_code == 200:
                    return response.json()['result']
                else:
                    raise ValueError(f"Failed to create dataset: {response.text}")
            finally:
                delete_api_token(token)
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 400
    else:
        return "Method not allowed", 405 

    

def get_api_token():
    if request.method == 'POST' or request.method == 'GET':
        # Get the ID parameter from the request
        email = toolkit.request.args.get('email')
        if not email:
            return f'Error: Missing required parameter: email', 401
        logger.info(f"Got get_api_token request: {email}")

        try:
            user = get_or_create_user()
            logger.info(f"invoker: {user.fullname}  {user.email}")
            if not user.sysadmin and not is_admin() and not user.email == 'kaiucsd@gmail.com':
                return "Not authorized", 401

            user = model.User.by_email(email)
            if not user:
                # Create a new user
                username = email.replace('.', '_').replace('@', '_')
                user = model.User(name=username, email=email)
                user.fullname = username
                user.password = generate_random_password()
                user.state = model.State.ACTIVE
                model.Session.add(user)
                model.Session.commit()
                logger.info("user created")

            token_dict = {
                'name': f'{user.name}_{int(datetime.now().timestamp())}',
                'user': user.name
            }

            context = {'model': model, 'session': model.Session, 'user': 'ckan_admin'}
            result = logic.get_action('api_token_create')(context, token_dict)
            result['name'] = token_dict['name']
            return result  # Returns the created token details
                                
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 400

        
        return email
    else:
        return "Method not allowed", 405



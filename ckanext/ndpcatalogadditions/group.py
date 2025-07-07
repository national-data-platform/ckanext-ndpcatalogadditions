import os
import random
import string
import traceback
import requests
import json

import ckan.model as model
import ckan.logic as logic
from ckan.plugins import toolkit
from ckan import authz
from ckanext.ndp.keycloak_token import get_user_info
from ckanext.ndp.controller import get_or_create_remote_user, create_api_token, delete_api_token, ckan_url, api_key
from flask import request, jsonify


import logging

logger = logging.getLogger(__name__)

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
    user = model.User.get(username)
    if not user:
        # Create a new user
        user = model.User(name=username, email=user_info['email'])
        user.fullname = user_info['name']
        user.password = generate_random_password()
        user.state = model.State.ACTIVE
        model.Session.add(user)
        model.Session.commit()
    return user


def validate_group_type(group_type: str):
    # Only allow workspace, classroom, project and module
    return group_type == 'user_catalog' or group_type == 'pathfinder_catalog' or group_type == 'datahub_catalog';


def validate_group_data(group_data, username):
    name = group_data.get('name', '')
    group_type = group_data.get('type', '')
            
    # validate the group type; must be workspace, project, classroom or module.
    if 'type' in group_data.keys():
        if not validate_group_type(group_data['type']):
            raise ValueError(f'Invalid group type: {group_data["type"]}. Must be one of user_catalog, pathfinder_catalog, and datahub_catalog.')
    else:
        raise ValueError(f'No group type is provided')
    
    if not name == '':
        if group_type == 'user_catalog':
            prefix = f'user-cc-{username.replace("_", "-")}-'
            if not name.startswith(prefix):
                raise ValueError({'name': f'this user_catalog name must start with "{prefix}"'})
        elif group_type == 'pathfinder_catalog':
            prefix = f'pathfinder-cc-'
            if not name.startswith(prefix):
                raise ValueError({'name': f'pathfinder_catalog names must start with "{prefix}"'})
        elif group_type == 'datahub_catalog':
            prefix = f'data-hub-cc-'
            if not name.startswith(prefix):
                raise ValueError({'name': f'datahub_catalog names must start with "{prefix}"'})


def get_user_info_from_keycloak_token():

    # Get the Authorization header
    auth_header = request.headers.get('Authorization')

    # Extract the Bearer Token if the header exists
    if auth_header and auth_header.startswith('Bearer '):
        bearer_token = auth_header[len('Bearer '):]
    else:
        raise ValueError('Missing or invalid KeyCloak token')
        
    user_info = get_user_info(bearer_token)
    username = user_info['username'].replace('.', '_').replace('@', '_')
    return username, user_info['email'], user_info['roles'], user_info['id'], user_info['name']


def get_username(user_id):
    url = f"{ckan_url}/api/3/action/user_show"
    headers = {
        'X-CKAN-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    data = {"id": user_id}
    resp = requests.post(url, data=json.dumps(data), headers=headers)
    if resp.status_code == 200:
        return resp.json()['result']['name']
    else:
        raise Exception(f"Could not fetch username for user_id {user_id}: {resp.text}")


def get_members(group_id):
    new_headers = {
        'X-CKAN-API-Key': api_key,
        'Content-Type': 'application/json'
    }

    # fetch all users in the group
    api_url = f"{ckan_url}/api/3/action/member_list"
    member_data = {
        "id": group_id,
        "object_type": "user"
    }
    response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
    if response.status_code == 200:
        members = response.json()["result"]
    else:
        raise ValueError(f"Failed to update group: {response.text}")
                
    # set up the users in the group
    return [
        {"name": get_username(user_id), "capacity": capacity} for (user_id, obj_type, capacity) in members if obj_type == "user"
    ]
    

def create_group():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            group_data = request.get_json()
            validate_group_data(group_data, username)
                
            # disable creating organization
            group_data['is_organization'] = False

            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")
            
            # setup creator
            extras = group_data.get('extras', [])
            found = False
            for extra in extras:
                if extra.get('key') == 'creator':
                    extra['value'] = remote_user['id']
                    found = True
                    break
            if not found:
                extras.append({
                    'key': 'creator',
                    'value': remote_user['id']
                })
            group_data['extras'] = extras
            logger.info(json.dumps(group_data, indent=4))

            try:
                api_url = f"{ckan_url}/api/3/action/group_create"
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.post(api_url, data=json.dumps(group_data), headers=new_headers)
                if response.status_code == 200:
                    created_group = response.json()['result']
                    return created_group
                else:
                    raise ValueError(f"Failed to create group: {response.text}")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def show_group():
    if request.method == 'POST' or request.method == 'GET':
        # Get the ID parameter from the request
        id = toolkit.request.args.get('id')
        if not id:
            return f'Error: Missing required parameter: id', 401
        logger.info(f"Got show_group request: {id}")

        try:
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")
        
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")

            new_headers = {
                'X-CKAN-API-Key': token,
                'Content-Type': 'application/json'
            }
        except:
            new_headers = None

        try:
            # Get the group from the given id
            group_show_url = f'{ckan_url}/api/3/action/group_show'
            response = requests.get(group_show_url, params={'id': id}, headers=new_headers)
            if response.status_code == 200:
                group = response.json()['result']

                # list members
                try:
                    members = get_members(id)
                    found = False
                    for member in members:
                        if member['name'] == remote_user['name']:
                            found = True
                    if found:
                        group['users'] = members
                except:
                    pass
                return group
            else:
                return f"Not Found Error", 400
        except logic.NotFound as e:
            return f"Group not found: '{id}'.", 401
        except Exception as e:
            traceback.print_exc()
            return f'Error: {str(e)}', 401
    return "Method not allowed", 405  # For unsupported methods
    

def update_group():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            group_data = request.get_json()
            validate_group_data(group_data, username)

            # Get the ID parameter from the request
            id = group_data.get('id', None)
            if not id:
                return f'Error: Missing required parameter: id', 401
            logger.info(f"Got update_group id: {id}")

            # disable creating organization
            group_data['is_organization'] = False

            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")

            try:
                new_headers = {
                    'X-CKAN-API-Key': api_key,
                    'Content-Type': 'application/json'
                }

                # make sure the extras/creator is not changed
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                response = requests.get(group_show_url, params={'id': id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                    logger.info(json.dumps(old_group, indent=4))
                
                    # setup creator                                                                                                                              
                    extras = group_data.get('extras', [])
                    found = False
                    for extra in extras:
                        if extra.get('key') == 'creator':
                            extra['value'] = remote_user['id']
                            found = True
                            break
                    if not found:
                        extras.append({
                            'key': 'creator',
                            'value': remote_user['id']
                        })
                    group_data['extras'] = extras
                    logger.info(json.dumps(group_data, indent=4))
                else:
                    raise ValueError(f"Failed to check group: {response.text}")
                
                # fetch all users in the group
                api_url = f"{ckan_url}/api/3/action/member_list"
                member_data = {
                    "id": group_data['id'],
                    "object_type": "user"
                }
                response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                if response.status_code == 200:
                    members = response.json()["result"]
                else:
                    raise ValueError(f"Failed to update group: {response.text}")
                
                # set up the users in the group
                logger.info(json.dumps(members, indent=4))
                group_data['users'] = [
                    {"name": get_username(user_id), "capacity": capacity} for (user_id, obj_type, capacity) in members if obj_type == "user"
                ]
                logger.info(json.dumps(group_data, indent=4))
                
                api_url = f"{ckan_url}/api/3/action/group_update"
                response = requests.post(api_url, data=json.dumps(group_data), headers=new_headers)
                if response.status_code == 200:
                    return response.json()
                else:
                    raise ValueError(f"Failed to update group: {response.text}")
            finally:
                delete_api_token(token)
                
        except Exception as e:
            return f'Error: {str(e)}', 401
    return "Method not allowed", 405  # For unsupported methods


def delete_group():
    if request.method == 'POST':
        try:
            group_data = request.get_json()

            # extract group_id                                                                                                                                   
            group_id = group_data.get('id', None)
            if group_id == None:
                raise ValueError(f"Missing group id")
            
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")
                       
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])

            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))

                # check the current user is an admin of the group
                members = get_members(group_id)
                found = False
                for member in members:
                    if member['name'] == remote_user['name'] and member['capacity'] == 'Admin':
                        found = True
                if found:
                    # delete the group by using the admin api because deleting groups by uses is disabled
                    api_url = f"{ckan_url}/api/3/action/group_delete"
                    new_headers = {
                        'X-CKAN-API-Key': api_key,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(group_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to delete group: {response.text}")
                else:
                    return f'Not aurhorized to delete this group', 403
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def purge_group():
    if request.method == 'POST':
        try:
            group_data = request.get_json()

            # extract group_id                                                                                                                                   
            group_id = group_data.get('id', None)
            if group_id == None:
                raise ValueError(f"Missing group id")
            
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")
                       
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])

            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))

                # check the current user is an admin of the group
                members = get_members(group_id)
                found = False
                for member in members:
                    if member['name'] == remote_user['name'] and member['capacity'] == 'Admin':
                        found = True
                if found:
                    # delete the group by using the admin api because deleting groups by uses is disabled
                    api_url = f"{ckan_url}/api/3/action/group_purge"
                    new_headers = {
                        'X-CKAN-API-Key': api_key,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(group_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to purge group: {response.text}")
                else:
                    return f'Not aurhorized to purge this group', 403
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def create_member():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            member_data = request.get_json()

            # extract group_id
            group_id = member_data.get('group_id', None)
            if group_id == None:
                raise ValueError(f"Missing group_id")
            
            # extract user_id
            user_id = member_data.get('user_id', None)
            if user_id == None:
                raise ValueError(f"Missing user_id")
            
            # extract role
            role = member_data.get('role', None)
            if role == None:
                raise ValueError(f"Missing role")
            
            member_data = {
                "id": group_id,
                "object": user_id,
                "object_type": "user",
                "capacity": role
            }
            
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")
            
            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))
                
                # check the current user is an admin of the group
                members = get_members(group_id)
                found = False
                for member in members:
                    if member['name'] == remote_user['name'] and member['capacity'] == 'Admin':
                        found = True
                if found:
                    api_url = f"{ckan_url}/api/3/action/member_create"
                    new_headers = {
                        'X-CKAN-API-Key': api_key,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to add member: {response.text}")
                else:
                    raise ValueError(f"Not authorized to add a member")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods
    

def delete_member():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            member_data = request.get_json()

            # extract group_id
            group_id = member_data.get('group_id', None)
            if group_id == None:
                raise ValueError(f"Missing group_id")
            
            # extract user_id
            user_id = member_data.get('user_id', None)
            if user_id == None:
                raise ValueError(f"Missing user_id")
                        
            member_data = {
                "id": group_id,
                "object": user_id,
                "object_type": "user"
            }
            
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")
            
            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))

                # check the current user is an admin of the group
                members = get_members(group_id)
                found = False
                for member in members:
                    if member['name'] == remote_user['name'] and member['capacity'] == 'Admin':
                        found = True
                if found:
                    api_url = f"{ckan_url}/api/3/action/member_delete"
                    new_headers = {
                        'X-CKAN-API-Key': api_key,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to delete member: {response.text}")
                else:
                    raise ValueError(f"Not authorized to delete member")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def my_groups():
    if request.method == 'GET':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")

            try:
                api_url = f"{ckan_url}/api/3/action/group_list_authz"
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                data_dict = {
                    'am_member': True
                }
                response = requests.post(api_url, data=json.dumps(data_dict), headers=new_headers)
                if response.status_code == 200:
                    return response.json()
                else:
                    raise ValueError(f"Failed to access groups: {response.text}")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


from flask import jsonify

def list_groups():
    if request.method == 'GET':
        try:
            api_url = f"{ckan_url}/api/3/action/group_list_authz"
            new_headers = {
                'X-CKAN-API-Key': api_key,
                'Content-Type': 'application/json'
            }
            data_dict = {}
            response = requests.post(api_url, data=json.dumps(data_dict), headers=new_headers)
            
            if response.status_code == 200:
                # Parse the JSON response
                response_data = response.json()
                
                # Filter the items
                catalog_types = ["datahub_catalog", "user_catalog", "pathfinder_catalog"]
                filtered_items = [item for item in response_data["result"] if item["type"] in catalog_types]

                group_details = []
                for group in filtered_items:
                    logger.info(f"Load the details of {group['name']}")
                    show_url = f"{ckan_url}/api/3/action/group_show"
                    show_response = requests.get(show_url, headers=new_headers, params={'id': group['id']})
                    if show_response.status_code == 200:
                        group_data = show_response.json()["result"]
                        # logger.info(f"{group_data}")
                        group_details.append(group_data)
                # logger.info(f"{group_details}")
                        
                # Return as JSON response
                # return jsonify(filtered_items)
                return jsonify(group_details)
            else:
                raise ValueError(f"Failed to access groups: {response.text}")
                
        except requests.exceptions.RequestException as e:
            return jsonify({'error': f'Request Error: {str(e)}'}), 500
        except json.JSONDecodeError as e:
            return jsonify({'error': f'JSON Decode Error: {str(e)}'}), 500
        except KeyError as e:
            return jsonify({'error': f'Key Error - missing field in response: {str(e)}'}), 500
        except Exception as e:
            return jsonify({'error': f'Error: {str(e)}'}), 500     

        
def create_subgroup():
    if request.method == 'POST':
        try:
            user = get_or_create_user()
            group_data = request.get_json()
            context = {'user': user.name}
            
            # get the parent group
            if not "parent_id" in group_data.keys():
                raise ValueError(f"Missing parent_id")
            parent_id = group_data['parent_id']

            group = model.Group.get(parent_id)
            if group is None:
                # Handle the case where the group doesn't exist
                raise Exception(f"Group with ID {parent_id} not found.")
            
            is_admin = authz.has_user_permission_for_group_or_org(parent_id, user.id, 'admin')
            if not is_admin:
                raise ValueError(f"Not authorized to create a subgroup. The current user is not an admin of the parent group.")
            
            # disable creating organization
            group_data['is_organization'] = False
            del group_data['parent_id']
            subgroup_data = group_data
            subgroup_data['type'] = f"sub-{group.type}"
            
            subgroup = logic.get_action('group_create')(context, subgroup_data)
            
            # set the subgroup
            member_data = { 'id': parent_id, 'object': subgroup['name'], 'object_type': 'group', 'capacity': 'public' }
            logic.get_action('member_create')(context, member_data)

            # should we copy the admin members from the parent group to the subgroup?
            
            parent_group = logic.get_action('group_show')(context, {"id": parent_id, 'include_groups': True, "include_users": True, "include_datasets": True})
            return parent_group
        except Exception as e:
            traceback.print_exc()
            return f'Error100: {str(e)}', 401

    return "Method not allowed", 405  # For unsupported methods


def group_package_add():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            request_data = request.get_json()

            # extract group_id
            group_id = request_data.get('group_id', None)
            if group_id == None:
                raise ValueError(f"Missing group_id")
            
            # extract user_id
            package_id = request_data.get('package_id', None)
            if package_id == None:
                raise ValueError(f"Missing package_id")
            
            member_data = {
                "id": group_id,
                "object": package_id,
                "object_type": "package",
                "capacity": "group"
            }
            
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")
            
            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': api_key,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))
                
                # add the package to the group using the user token for pathfinder or datahub catalog
                if old_group['type'] == 'pathfinder_catalog' or old_group['type'] == 'datahub_catalog':
                    api_url = f"{ckan_url}/api/3/action/member_create"
                    new_headers = {
                        'X-CKAN-API-Key': token,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to add dataset: {response.text}")
                elif old_group['type'] == 'user_catalog':
                    api_url = f"{ckan_url}/api/3/action/member_create"
                    new_headers = {
                        'X-CKAN-API-Key': api_key,
                        'Content-Type': 'application/json'
                    }
                    response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                    if response.status_code == 200:
                        return response.json()
                    else:
                        raise ValueError(f"Failed to add dataset: {response.text}")
                else:
                    raise ValueError(f"Not authorized to add a dataset")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401
        
    return "Method not allowed", 405  # For unsupported methods


def group_package_delete():
    if request.method == 'POST':
        try:                    
            # get user info
            username, email, roles, uid, fullname = get_user_info_from_keycloak_token()
            logger.info(f"Got CKAN username and email: {username}, {email}, {uid}, {fullname}")

            request_data = request.get_json()

            # extract group_id
            group_id = request_data.get('group_id', None)
            if group_id == None:
                raise ValueError(f"Missing group_id")
            
            # extract user_id
            package_id = request_data.get('package_id', None)
            if package_id == None:
                raise ValueError(f"Missing package_id")
            
            member_data = {
                "id": group_id,
                "object": package_id,
                "object_type": "package",
                "capacity": "group"
            }
            
            # get or create a remote user and create an api token
            remote_user = get_or_create_remote_user(username, email, fullname)
            token = create_api_token(remote_user['name'])
            logger.info(f"Create a token for: {remote_user['name']}")
            
            try:
                # load the group for validating its type as catalog
                group_show_url = f'{ckan_url}/api/3/action/group_show'
                new_headers = {
                    'X-CKAN-API-Key': api_key,
                    'Content-Type': 'application/json'
                }
                response = requests.get(group_show_url, params={'id': group_id}, headers=new_headers)
                if response.status_code == 200:
                    old_group = response.json()['result']
                else:
                    raise ValueError(f"Failed to access the group: {response.text}")
                
                # check the type of the group is a catalog
                validate_group_type(old_group.get('type', ''))
                
                api_url = f"{ckan_url}/api/3/action/member_delete"
                new_headers = {
                    'X-CKAN-API-Key': token,
                    'Content-Type': 'application/json'
                }
                response = requests.post(api_url, data=json.dumps(member_data), headers=new_headers)
                if response.status_code == 200:
                    return response.json()
                else:
                    raise ValueError(f"Failed to add dataset: {response.text}")
            finally:
                delete_api_token(token)
        except Exception as e:
            return f'Error: {str(e)}', 401
        
    return "Method not allowed", 405  # For unsupported methods

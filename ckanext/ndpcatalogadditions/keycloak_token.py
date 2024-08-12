
import os

import requests
from jose import jwt
from jose.exceptions import JWTError
from ckan.plugins import toolkit


def verify_and_decode_token(token, server_url, realm, client_id):
    # Fetch the public key from Keycloak
    key_url = f"{server_url}/realms/{realm}/protocol/openid-connect/certs"
    response = requests.get(key_url)
    keys = response.json()['keys']

    try:
        # Verify and decode the token
        header = jwt.get_unverified_header(token)
        key = [k for k in keys if k['kid'] == header['kid']][0]
        decoded_token = jwt.decode(
            token,
            key,
            algorithms=['RS256'],
            audience=client_id,
            options={"verify_signature": True}
        )
        return decoded_token
    except JWTError as e:
        print(f"Token verification failed: {str(e)}")
        return None


def extract_user_info(decoded_token):
    # Extract relevant user information
    user_info = {
        'username': decoded_token.get('preferred_username'),
        'email': decoded_token.get('email'),
        'name': decoded_token.get('name'),
        'given_name': decoded_token.get('given_name'),
        'family_name': decoded_token.get('family_name'),
        'roles': decoded_token.get('realm_access', {}).get('roles', [])
    }
    return user_info


def get_user_info(token: str):

    server_url = os.getenv('CKANEXT__KEYCLOAK__SERVER_URL')
    realm = os.getenv('CKANEXT__KEYCLOAK__REALM_NAME')
    client_id = "account"
    
    decoded_token = verify_and_decode_token(token, server_url, realm, client_id)
    if decoded_token:
        user_info = extract_user_info(decoded_token)
        return user_info
    else:
        raise toolkit.NotAuthorized('Invalid Keycloak token')




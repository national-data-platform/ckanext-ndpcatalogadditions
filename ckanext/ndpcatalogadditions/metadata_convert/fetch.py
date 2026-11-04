"""Fetch a metadata record from a URL, for the metadata_url input path."""

import requests

# Some metadata APIs/WAFs reject the default "python-requests/x.x" User-Agent
# outright (see package_validation.py's _is_url_alive fix for the same issue)
# - identify ourselves honestly instead of spoofing a browser.
_HEADERS = {
    'User-Agent': 'NDP-MetadataConvert/1.0 (+https://nationaldataplatform.org)',
    'Accept': 'application/json, application/xml, application/ld+json, text/turtle, */*',
}


def fetch_metadata_content(url: str, timeout: int = 20) -> str:
    """GET the metadata record at url and return its text body.

    Raises ValueError (caught by the view as a 400) on any failure - a bad
    metadata_url is a client error, not a server error.
    """
    try:
        response = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        raise ValueError(f"could not fetch metadata_url: {e}")
    if response.status_code >= 400:
        raise ValueError(f"metadata_url returned HTTP {response.status_code}")
    if not response.text.strip():
        raise ValueError("metadata_url returned an empty body")
    return response.text

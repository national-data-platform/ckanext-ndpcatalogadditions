import json
import re
import requests
from datetime import datetime
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

def ndp_package_validate(package_json: str, check_urls: bool = True) -> Dict[str, Any]:
    """
    Validate a CKAN package JSON string based on NDP requirements.
    
    Args:
        package_json: JSON string representing a CKAN package
        check_urls: Whether to check if URLs are alive (default: True)
        
    Returns:
        Dictionary with 'valid' (bool) and 'errors' (list) keys
    """
    errors = []
    
    # Parse JSON
    try:
        package = json.loads(package_json)
    except json.JSONDecodeError as e:
        return {
            'valid': False,
            'errors': [f'Invalid JSON: {str(e)}']
        }
    
    # Determine if dataset is private
    is_private = package.get('private', False)
    
    # Check required fields for both public and private datasets
    _check_required_common_fields(package, errors)
    
    # Check required fields for public datasets only
    if not is_private:
        _check_required_public_fields(package, errors)
    
    # Validate field formats
    _validate_field_formats(package, errors, check_urls)
    
    # Handle temporal field creation
    _handle_temporal_fields(package, errors)
    
    # Handle spatial field creation
    _handle_spatial_field(package, errors)
    
    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'package': package  # Return modified package with temporal field if created
    }


def _check_required_common_fields(package: Dict, errors: List[str]) -> None:
    """Check required fields for both public and private datasets."""
    # Top-level required fields
    if not package.get('title'):
        errors.append('Missing required field: title')

    if not package.get('notes'):
        errors.append('Missing required field: notes')

    # Check resources
    resources = package.get('resources', [])
    if not resources:
        errors.append('Missing required field: at least one resource is required')
    else:
        for idx, resource in enumerate(resources):
            if not resource.get('name'):
                errors.append(f'Missing required field: resource[{idx}]:name')
            if not resource.get('description'):
                errors.append(f'Missing required field: resource[{idx}]:description')


def _check_required_public_fields(package: Dict, errors: List[str]) -> None:
    """Check required fields for public datasets only."""
    # Tags
    tags = package.get('tags', [])
    if not tags or len(tags) == 0:
        errors.append('Missing required field for public dataset: tags')
    
    # Extras for public datasets
    extras = package.get('extras', [])
    extras_dict = {item['key']: item['value'] for item in extras if isinstance(item, dict)}

    required_public_extras = ['uploadType', 'lastUpdateDate', 'pocName', 'pocEmail']
    for extra_key in required_public_extras:
        if extra_key not in extras_dict or not extras_dict[extra_key]:
            errors.append(f'Missing required field for public dataset: extras:{extra_key}')

    # publisherEmail is only required when no organization is chosen (i.e. an
    # individual/person publisher) - datasets published under an organization
    # don't need it.
    if not package.get('owner_org') and not extras_dict.get('publisherEmail'):
        errors.append('Missing required field for public dataset: extras:publisherEmail (required when no organization is selected)')

    # Resources for public datasets
    resources = package.get('resources', [])
    if resources:
        for idx, resource in enumerate(resources):
            if not resource.get('mimetype'):
                errors.append(f'Missing required field for public dataset: resource[{idx}]:mimetype')
            # if not resource.get('format'):
            #    errors.append(f'Missing required field for public dataset: resource[{idx}]:format')
            if not resource.get('status'):
                errors.append(f'Missing required field for public dataset: resource[{idx}]:status')


def _validate_field_formats(package: Dict, errors: List[str], check_urls: bool = True) -> None:
    """Validate formats of optional fields if they appear."""
    extras = package.get('extras', [])
    extras_dict = {item['key']: item['value'] for item in extras if isinstance(item, dict)}
    
    # Email validations
    email_fields = ['publisherEmail', 'creatorEmail', 'pocEmail']
    for field in email_fields:
        if field in extras_dict and extras_dict[field]:
            if not _is_valid_email(extras_dict[field]):
                errors.append(f'Invalid email format: extras:{field}')
    
    # URL validations (excluding DOI which has its own format)
    url_fields = ['publisherWebsite', 'creatorWebsite', 'pocWebsite', 'docsURL', 'datasetPageUrl']
    for field in url_fields:
        if field in extras_dict and extras_dict[field]:
            if not _is_valid_url(extras_dict[field]):
                errors.append(f'Invalid URL format: extras:{field}')
    
    # DOI validation (can be identifier or URL)
    if 'doi' in extras_dict and extras_dict['doi']:
        if not _is_valid_doi(extras_dict['doi']):
            errors.append('Invalid DOI format: extras:doi. Expected format: "10.xxxx/xxxxx" or "https://doi.org/10.xxxx/xxxxx"')

    # uploadType validation
    if 'uploadType' in extras_dict and extras_dict['uploadType']:
        valid_upload_types = ['dataset', 'model', 'service', 'docker-image', 'application', 'code']
        if extras_dict['uploadType'] not in valid_upload_types:
            errors.append(f"Invalid extras:uploadType. Must be one of {valid_upload_types}")
            
    # Spatial coverage format and data validation
    if 'spatialCovFormat' in extras_dict and extras_dict['spatialCovFormat']:
        valid_formats = ['text', 'geojson', 'wkt']
        if extras_dict['spatialCovFormat'] not in valid_formats:
            errors.append(f"Invalid extras:spatialCovFormat. Must be one of {valid_formats}")
        
        # Validate spatialCov based on format
        if extras_dict['spatialCovFormat'] in ['geojson', 'wkt']:
            if 'spatialCov' not in extras_dict or not extras_dict['spatialCov']:
                errors.append(f"extras:spatialCov is required when spatialCovFormat is '{extras_dict['spatialCovFormat']}'")
            else:
                _validate_spatial_data(extras_dict['spatialCov'], extras_dict['spatialCovFormat'], 'spatialCov', errors)
    
    # Data bounding box format and data validation
    if 'dataBboxFormat' in extras_dict and extras_dict['dataBboxFormat']:
        valid_formats = ['string', 'geojson', 'wkt']
        if extras_dict['dataBboxFormat'] not in valid_formats:
            errors.append(f"Invalid extras:dataBboxFormat. Must be one of {valid_formats}")
        
        # Validate dataBbox based on format
        if extras_dict['dataBboxFormat'] in ['geojson', 'wkt']:
            if 'dataBbox' not in extras_dict or not extras_dict['dataBbox']:
                errors.append(f"extras:dataBbox is required when dataBboxFormat is '{extras_dict['dataBboxFormat']}'")
            else:
                _validate_bbox(extras_dict['dataBbox'], extras_dict['dataBboxFormat'], errors)
    
    # Spatial resolution - must start with a number (units can follow, e.g., "10 meter")
    if 'spatialRes' in extras_dict:
        value = extras_dict['spatialRes'].strip() if extras_dict['spatialRes'] else ''
        if not value:
            errors.append('extras:spatialRes cannot be empty')
        else:
            # Extract the numeric prefix (e.g., "10 meter" -> 10.0)
            match = re.match(r'^[+-]?(\d+(\.\d*)?|\.\d+)', value)
            if not match:
                errors.append('Invalid extras:spatialRes: Must start with a number (e.g., "10 meter", "5.5 km")')
    
    # DateTime validations
    datetime_fields = ['startDateTime', 'endDateTime']
    for field in datetime_fields:
        if field in extras_dict and extras_dict[field]:
            if not _is_valid_datetime(extras_dict[field]):
                errors.append(f'Invalid date/time format: extras:{field}')
    
    # Temporal validation
    if 'temporal' in extras_dict and extras_dict['temporal']:
        if not _is_valid_temporal(extras_dict['temporal']):
            errors.append('Invalid extras:temporal format. Expected: {"startTime": "ISO8601", "endTime": "ISO8601"}')
    
    # Resource URL validation and availability check
    resources = package.get('resources', [])
    for idx, resource in enumerate(resources):
        if 'url' in resource and resource['url']:
            if not _is_valid_url(resource['url']):
                errors.append(f'Invalid URL format: resource[{idx}]:url')
            elif check_urls:
                if not _is_url_alive(resource['url']):
                    errors.append(f'URL is not accessible: resource[{idx}]:url ({resource["url"]})')
        if 'status' in resource and resource['status']:
            valid_status = ['active', 'deprecated']
            if resource['status'] not in valid_status:
                errors.append(f'Invalid: resource[{idx}]:status. Must be one of {valid_status}')
        if 'mimetype' in resource and resource['mimetype']:
            pattern = re.compile(r'^[a-zA-Z0-9!#$&^_-]+(/[a-zA-Z0-9!#$&^_.+-]+)?$')
            if not bool(pattern.match(resource['mimetype'].strip())):
                errors.append(f'Invalid: resource[{idx}]:mimetype.')
            
def _handle_temporal_fields(package: Dict, errors: List[str]) -> None:
    """Create temporal field from startDateTime and endDateTime if needed."""
    extras = package.get('extras', [])
    extras_dict = {item['key']: item['value'] for item in extras if isinstance(item, dict)}
    
    has_start = 'startDateTime' in extras_dict and extras_dict['startDateTime']
    has_end = 'endDateTime' in extras_dict and extras_dict['endDateTime']
    has_temporal = 'temporal' in extras_dict and extras_dict['temporal']
    
    if has_start and has_end and not has_temporal:
        # Create temporal field
        try:
            temporal = {
                'startTime': _convert_to_iso8601(extras_dict['startDateTime']),
                'endTime': _convert_to_iso8601(extras_dict['endDateTime'])
            }
            
            # Add to extras
            package.setdefault('extras', []).append({
                'key': 'temporal',
                'value': json.dumps(temporal)
            })
        except Exception as e:
            errors.append(f'Failed to create temporal field from startDateTime and endDateTime: {str(e)}')


def _handle_spatial_field(package: Dict, errors: List[str]) -> None:
    """Create spatial field from dataBbox or spatialCov if they are in geojson format."""
    extras = package.get('extras', [])
    extras_dict = {item['key']: item['value'] for item in extras if isinstance(item, dict)}
    
    has_spatial = 'spatial' in extras_dict and extras_dict['spatial']

    # Only create spatial field if it doesn't already exist
    if not has_spatial:
        # Check if dataBbox is geojson format
        if extras_dict.get('dataBboxFormat') == 'geojson' and extras_dict.get('dataBbox'):
            try:
                # Validate it's valid JSON before copying
                json.loads(extras_dict['dataBbox'])
                
                # Add to extras
                package.setdefault('extras', []).append({
                    'key': 'spatial',
                    'value': extras_dict['dataBbox']
                })
                return  # Exit after creating spatial from dataBbox
            except json.JSONDecodeError:
                # If dataBbox is invalid, don't create spatial from it
                pass
            except Exception as e:
                errors.append(f'Failed to create spatial field from dataBbox: {str(e)}')
                return
        
        # If dataBbox doesn't exist or isn't geojson, check spatialCov
        if extras_dict.get('spatialCovFormat') == 'geojson' and extras_dict.get('spatialCov'):
            try:
                # Validate it's valid JSON before copying
                json.loads(extras_dict['spatialCov'])
                
                # Add to extras
                package.setdefault('extras', []).append({
                    'key': 'spatial',
                    'value': extras_dict['spatialCov']
                })
            except json.JSONDecodeError:
                # If spatialCov is invalid, don't create spatial from it
                pass
            except Exception as e:
                errors.append(f'Failed to create spatial field from spatialCov: {str(e)}')


def _is_valid_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def _is_valid_doi(doi: str) -> bool:
    """
    Validate DOI format.
    Accepts both:
    - Direct identifier: 10.xxxx/xxxxx
    - URL format: https://doi.org/10.xxxx/xxxxx or http://dx.doi.org/10.xxxx/xxxxx

    DOI registrant code (the number after 10.) must be >= 1000.
    """
    doi = doi.strip()

    # Pattern for DOI identifier (starts with 10. followed by registrant code and suffix)
    # DOI format: 10.{registrant code}/{suffix}
    doi_pattern = r'^10\.(\d{4,9})/[-._;()/:A-Za-z0-9]+$'

    # Check if it's a direct DOI identifier
    match = re.match(doi_pattern, doi)
    if match:
        registrant_code = int(match.group(1))
        return registrant_code >= 1000

    # Check if it's a DOI URL
    if doi.startswith('https://doi.org/') or doi.startswith('http://doi.org/'):
        doi_part = doi.replace('https://doi.org/', '').replace('http://doi.org/', '')
        match = re.match(doi_pattern, doi_part)
        if match:
            registrant_code = int(match.group(1))
            return registrant_code >= 1000
        return False

    # Also accept dx.doi.org format
    if doi.startswith('https://dx.doi.org/') or doi.startswith('http://dx.doi.org/'):
        doi_part = doi.replace('https://dx.doi.org/', '').replace('http://dx.doi.org/', '')
        match = re.match(doi_pattern, doi_part)
        if match:
            registrant_code = int(match.group(1))
            return registrant_code >= 1000
        return False

    return False


def _is_valid_url(url: str) -> bool:
    """Validate URL format with stricter domain validation."""
    try:
        result = urlparse(url)

        if not result.scheme:
            return False

        if result.scheme in ["http", "https", "ftp", "file"]:
            # For network-based URLs, netloc (host) is required
            if not result.netloc:
                return False

            # Reject URLs with userinfo (username:password@host) - these are likely malformed domains
            if '@' in result.netloc:
                return False

            # Validate hostname/domain: only allow alphanumeric, hyphens, dots, and underscores
            # Remove port from netloc
            hostname = result.netloc.split(':')[0]

            # Domain validation: labels separated by dots, each label contains alphanumeric, hyphens, underscores
            # Special characters like @, !, $, %, etc. should not appear in domain names
            domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?)*$'
            return bool(re.match(domain_pattern, hostname))

        elif result.scheme == "pelican":
            # For custom schemes like pelican://, allow path without netloc
            return bool(result.path)
        else:
            # For other schemes, do basic validation
            return bool(result.netloc or result.path)

    except Exception:
        return False


def _is_url_alive(url: str, timeout: int = 5) -> bool:
    """Check if URL is accessible by sending a HEAD request."""

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        # Non-HTTP(S) schemes (e.g., pelican://) are not checked online
        return True

    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return True
        # Some servers (e.g. our own STAC/preSTAC) don't support HEAD and
        # reply with 405 rather than raising - fall back to GET below.
    except requests.exceptions.RequestException:
        pass

    # If HEAD failed (exception) or came back with an error status, try GET
    # before concluding the URL is really unreachable.
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
        if response.status_code < 400:
            return True
    except requests.exceptions.RequestException:
        pass

    # Last resort: some servers (e.g. WAFs/security plugins) block requests'
    # default "python-requests/x.x" User-Agent outright (406/403) regardless
    # of HEAD vs GET. Retry once with an honest, self-identifying UA before
    # concluding the URL is really unreachable - this only ever changes the
    # outcome for URLs that would otherwise be reported dead above.
    identifying_headers = {
        'User-Agent': 'NDP-URL-Validator/1.0 (+https://nationaldataplatform.org)'
    }
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True,
                                 stream=True, headers=identifying_headers)
        return response.status_code < 400
    except requests.exceptions.RequestException:
        return False


def _is_valid_datetime(dt_str: str) -> bool:
    """Validate datetime format."""
    formats = [
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    ]
    
    for fmt in formats:
        try:
            datetime.strptime(dt_str, fmt)
            return True
        except ValueError:
            continue
    return False


def _convert_to_iso8601(dt_str: str) -> str:
    """Convert datetime string to ISO8601 format."""
    formats = [
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            continue
    
    # If already in good format, return as is
    return dt_str


def _is_valid_temporal(temporal_str: str) -> bool:
    """Validate temporal field format."""
    try:
        temporal = json.loads(temporal_str) if isinstance(temporal_str, str) else temporal_str
        if not isinstance(temporal, dict):
            return False
        
        if 'startTime' not in temporal or 'endTime' not in temporal:
            return False
        
        # Validate both are valid datetime strings
        return _is_valid_datetime(temporal['startTime']) and _is_valid_datetime(temporal['endTime'])
    except Exception:
        return False


def _validate_bbox(data: str, format_type: str, errors: List[str]) -> None:
    """Validate bounding box data - must be a valid bbox, not just any geometry."""
    if format_type == 'geojson':
        try:
            # Parse the string as JSON
            geojson = json.loads(data)
            
            # Validate GeoJSON structure
            if not isinstance(geojson, dict):
                errors.append('Invalid GeoJSON for extras:dataBbox: must be a valid JSON object')
                return
            
            if 'type' not in geojson:
                errors.append('Invalid GeoJSON for extras:dataBbox: missing "type" property')
                return
            
            geojson_type = geojson['type']
            
            # For bounding boxes, we expect a Polygon (rectangle) or a bbox array
            if geojson_type == 'Polygon':
                if 'coordinates' not in geojson:
                    errors.append('Invalid GeoJSON for extras:dataBbox: Polygon missing "coordinates" property')
                    return
                
                # Validate it's a rectangular polygon (5 points forming a rectangle)
                coords = geojson['coordinates']
                if not isinstance(coords, list) or len(coords) == 0:
                    errors.append('Invalid GeoJSON for extras:dataBbox: invalid coordinates structure')
                    return
                
                # Get the outer ring
                outer_ring = coords[0]
                if not isinstance(outer_ring, list) or len(outer_ring) < 4:
                    errors.append('Invalid GeoJSON for extras:dataBbox: bounding box polygon must have at least 4 points')
                    return
                
                # Check if it forms a rectangle (all points should align to create a box)
                # Extract unique x and y coordinates
                x_coords = set()
                y_coords = set()
                for point in outer_ring:
                    if len(point) >= 2:
                        x_coords.add(point[0])
                        y_coords.add(point[1])
                
                # A rectangle should have exactly 2 unique x values and 2 unique y values
                if len(x_coords) != 2 or len(y_coords) != 2:
                    errors.append('Invalid GeoJSON for extras:dataBbox: Polygon is not a valid bounding box (must be rectangular)')
                    return
                    
            elif geojson_type == 'Feature':
                if 'geometry' not in geojson:
                    errors.append('Invalid GeoJSON for extras:dataBbox: Feature missing "geometry" property')
                    return
                
                geometry = geojson['geometry']
                if geometry.get('type') != 'Polygon':
                    errors.append('Invalid GeoJSON for extras:dataBbox: bounding box Feature must contain a Polygon geometry')
                    return
                
                # Recursively validate the polygon geometry
                _validate_bbox(json.dumps(geometry), format_type, errors)
                
            else:
                errors.append(f'Invalid GeoJSON for extras:dataBbox: type "{geojson_type}" is not valid for a bounding box. Expected Polygon or Feature with Polygon geometry.')
                return
                
        except json.JSONDecodeError:
            errors.append('Invalid GeoJSON for extras:dataBbox: not valid JSON format. Expected valid GeoJSON Polygon representing a bounding box.')
        except Exception as e:
            errors.append(f'Error validating GeoJSON for extras:dataBbox: {str(e)}')
    
    elif format_type == 'wkt':
        try:
            # Validate WKT format for bounding box
            wkt_data = data.strip().upper()
            
            # Bounding boxes should be POLYGON in WKT
            if not wkt_data.startswith('POLYGON'):
                errors.append('Invalid WKT for extras:dataBbox: bounding box must be a POLYGON')
                return
            
            # Check for basic WKT structure (must have parentheses)
            if '(' not in data or ')' not in data:
                errors.append('Invalid WKT for extras:dataBbox: missing coordinate parentheses')
                return
            
            # Check for balanced parentheses
            open_count = data.count('(')
            close_count = data.count(')')
            if open_count != close_count:
                errors.append('Invalid WKT for extras:dataBbox: unbalanced parentheses')
                return
            
            # Extract coordinates section
            coord_section = data[data.find('('):data.rfind(')')+1]
            
            # Parse coordinates to validate it's a rectangle
            # Remove outer parentheses for polygon ring
            coord_section = coord_section.strip()
            if coord_section.startswith('((') and coord_section.endswith('))'):
                coord_section = coord_section[2:-2]
            elif coord_section.startswith('(') and coord_section.endswith(')'):
                coord_section = coord_section[1:-1]
            
            # Split by comma to get individual points
            points = coord_section.split(',')
            if len(points) < 4:
                errors.append('Invalid WKT for extras:dataBbox: bounding box must have at least 4 points')
                return
            
            # Parse coordinates
            parsed_points = []
            for point in points:
                coords = point.strip().split()
                if len(coords) >= 2:
                    try:
                        x = float(coords[0])
                        y = float(coords[1])
                        parsed_points.append((x, y))
                    except ValueError:
                        errors.append('Invalid WKT for extras:dataBbox: invalid numeric coordinates')
                        return
            
            if len(parsed_points) < 4:
                errors.append('Invalid WKT for extras:dataBbox: insufficient valid coordinate points')
                return
            
            # Check if it forms a rectangle
            x_coords = set(p[0] for p in parsed_points)
            y_coords = set(p[1] for p in parsed_points)
            
            # A rectangle should have exactly 2 unique x values and 2 unique y values
            if len(x_coords) != 2 or len(y_coords) != 2:
                errors.append('Invalid WKT for extras:dataBbox: Polygon is not a valid bounding box (must be rectangular)')
                return
                
        except Exception as e:
            errors.append(f'Error validating WKT for extras:dataBbox: {str(e)}')


def _validate_spatial_data(data: str, format_type: str, field_name: str, errors: List[str]) -> None:
    """Validate spatial data by parsing as GeoJSON or WKT."""
    if format_type == 'geojson':
        try:
            # Parse the string as JSON
            geojson = json.loads(data)
            
            # Validate GeoJSON structure
            if not isinstance(geojson, dict):
                errors.append(f'Invalid GeoJSON for extras:{field_name}: must be a valid JSON object')
                return
            
            if 'type' not in geojson:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: missing "type" property')
                return
            
            geojson_type = geojson['type']
            valid_types = ['Point', 'LineString', 'Polygon', 'MultiPoint', 
                          'MultiLineString', 'MultiPolygon', 'GeometryCollection', 
                          'Feature', 'FeatureCollection']
            
            if geojson_type not in valid_types:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: invalid type "{geojson_type}"')
                return
            
            # Validate geometry types have coordinates
            geometry_types = ['Point', 'LineString', 'Polygon', 'MultiPoint', 
                            'MultiLineString', 'MultiPolygon']
            if geojson_type in geometry_types and 'coordinates' not in geojson:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: missing "coordinates" property')
                return
            
            # Validate GeometryCollection has geometries
            if geojson_type == 'GeometryCollection' and 'geometries' not in geojson:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: GeometryCollection missing "geometries" property')
                return
            
            # Validate Feature has geometry
            if geojson_type == 'Feature' and 'geometry' not in geojson:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: Feature missing "geometry" property')
                return
            
            # Validate FeatureCollection has features
            if geojson_type == 'FeatureCollection' and 'features' not in geojson:
                errors.append(f'Invalid GeoJSON for extras:{field_name}: FeatureCollection missing "features" property')
                return
                
        except json.JSONDecodeError:
            errors.append(f'Invalid GeoJSON for extras:{field_name}: not valid JSON format. Expected valid GeoJSON object with type and coordinates.')
        except Exception as e:
            errors.append(f'Error validating GeoJSON for extras:{field_name}: {str(e)}')
    
    elif format_type == 'wkt':
        try:
            # Validate WKT format
            wkt_data = data.strip().upper()
            
            # Define valid WKT geometry types
            wkt_types = [
                'POINT', 'LINESTRING', 'POLYGON', 
                'MULTIPOINT', 'MULTILINESTRING', 'MULTIPOLYGON',
                'GEOMETRYCOLLECTION', 'CIRCULARSTRING', 'COMPOUNDCURVE',
                'CURVEPOLYGON', 'MULTICURVE', 'MULTISURFACE',
                'POLYHEDRALSURFACE', 'TIN', 'TRIANGLE'
            ]
            
            # Check if WKT starts with a valid geometry type
            valid_start = False
            for wkt_type in wkt_types:
                if wkt_data.startswith(wkt_type):
                    valid_start = True
                    break
            
            if not valid_start:
                errors.append(f'Invalid WKT for extras:{field_name}: does not start with a valid geometry type')
                return
            
            # Check for basic WKT structure (must have parentheses)
            if '(' not in data or ')' not in data:
                errors.append(f'Invalid WKT for extras:{field_name}: missing coordinate parentheses')
                return
            
            # Check for balanced parentheses
            open_count = data.count('(')
            close_count = data.count(')')
            if open_count != close_count:
                errors.append(f'Invalid WKT for extras:{field_name}: unbalanced parentheses')
                return
            
            # Extract coordinates section and validate it contains numbers
            coord_section = data[data.find('('):data.rfind(')')+1]
            # Remove parentheses and commas to check for numeric values
            coord_values = coord_section.replace('(', '').replace(')', '').replace(',', ' ').split()
            
            if not coord_values:
                errors.append(f'Invalid WKT for extras:{field_name}: no coordinates found')
                return
            
            # Check that we have numeric coordinate values
            numeric_values = []
            for val in coord_values:
                try:
                    float(val)
                    numeric_values.append(val)
                except ValueError:
                    pass
            
            if not numeric_values:
                errors.append(f'Invalid WKT for extras:{field_name}: no valid numeric coordinates found')
                return
                
        except Exception as e:
            errors.append(f'Error validating WKT for extras:{field_name}: {str(e)}')


# Example usage
if __name__ == '__main__':
    # Example public dataset
    public_package = {
        "title": "Sample Dataset",
        "notes": "This is a sample dataset",
        "private": False,
        "tags": [{"name": "sample"}],
        "extras": [
            {"key": "uploadType", "value": "manual"},
            {"key": "issueDate", "value": "2024-01-01"},
            {"key": "lastUpdateDate", "value": "2024-01-15"},
            {"key": "dataType", "value": "tabular"},
            {"key": "pocName", "value": "John Doe"},
            {"key": "pocEmail", "value": "john.doe@example.com"},
            {"key": "doi", "value": "10.5281/zenodo.1001234"},
            {"key": "startDateTime", "value": "2024-01-01T00:00:00Z"},
            {"key": "endDateTime", "value": "2024-12-31T23:59:59Z"},
            {"key": "spatialCovFormat", "value": "geojson"},
            # {"key": "dataBbox", "value": "{\"type\":\"Polygon\",\"coordinates\":[[[-105.284,40.0075],[-105.284,40.0165],[-105.272,40.0165],[-105.272,40.0075],[-105.284,40.0075]]]}"},
            {"key": "spatialCov", "value": "{\"type\":\"FeatureCollection\",\"features\":[{\"type\":\"Feature\",\"geometry\":{\"type\":\"Polygon\",\"coordinates\":[[[-105.284,40.0075],[-105.284,40.0165],[-105.272,40.0165],[-105.272,40.0075],[-105.284,40.0075]]]},\"properties\":{}}]}"}
        ],
        "resources": [
            {
                "name": "Data File",
                "description": "Main data file",
                "mimetype": "text/csv",
                "format": "CSV",
                "status": "active",
                "url": "https://example.com/data.csv"
            }
        ]
    }
    
    result = ndp_package_validate(json.dumps(public_package))
    print(f"Valid: {result['valid']}")
    if result['errors']:
        print("Errors:")
        for error in result['errors']:
            print(f"  - {error}")
    else:
        print("No errors found!")
        
    # Check if temporal was created
    extras_dict = {item['key']: item['value'] for item in result['package'].get('extras', [])}
    if 'temporal' in extras_dict:
        print(f"\nTemporal field created: {extras_dict['temporal']}")
    
    # Check if spatial was created
    if 'spatial' in extras_dict:
        print(f"Spatial field created from geojson data: {extras_dict['spatial']}")
       

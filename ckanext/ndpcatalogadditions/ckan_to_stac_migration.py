"""
CKAN Organization to STAC Collection Migration Script

This script converts CKAN organizations into STAC collections.
It creates the organizational structure in STAC without migrating datasets,
as datasets should only be migrated selectively (spatial-temporal data only).

STAC Collection Required Fields (Minimal):
- stac_version: "1.0.0" (or latest)
- type: "Collection"
- id: Unique identifier
- description: Text description
- license: License identifier or "proprietary"
- extent: Spatial and temporal extents
- links: Array of link objects (at least self, root)

Additional Recommended Fields:
- title: Human-readable title
- keywords: Array of keywords for discoverability
- providers: Organizations involved in the data
- summaries: Summary statistics of items
"""

import requests
import json
from typing import Dict, List, Any, Optional
from datetime import datetime


# =============================================================================
# STAC Configuration Constants
# TODO: Move to environment variables later
# =============================================================================
STAC_ENABLED = False
STAC_URL = "https://ndp-test.sdsc.edu/stac"
STAC_API_KEY = "replaceDIMMsoon"  # TODO: Update with actual key


class CKANToSTACMigration:
    """Migrate CKAN organizations to STAC collections"""
    
    def __init__(
        self, 
        ckan_url: str, 
        stac_url: str, 
        ckan_api_key: Optional[str] = None,
        stac_auth_type: Optional[str] = None,
        stac_api_key: Optional[str] = None,
        stac_bearer_token: Optional[str] = None,
        stac_username: Optional[str] = None,
        stac_password: Optional[str] = None
    ):
        """
        Initialize the migration tool
        
        Args:
            ckan_url: Base URL of CKAN instance (e.g., "https://data.example.org")
            stac_url: Base URL of STAC API (e.g., "https://ndp-test.sdsc.edu/stac")
            ckan_api_key: Optional CKAN API key for authentication
            stac_auth_type: Type of STAC authentication ("bearer", "apikey", "basic", or None)
            stac_api_key: API key for STAC (if using apikey auth)
            stac_bearer_token: Bearer token for STAC (if using bearer auth)
            stac_username: Username for STAC (if using basic auth)
            stac_password: Password for STAC (if using basic auth)
        """
        self.ckan_url = ckan_url.rstrip('/')
        self.stac_url = stac_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.ckan_headers = {}
        self.stac_headers = {'Content-Type': 'application/json'}
        self.stac_auth = None
        
        # Configure CKAN authentication
        if ckan_api_key:
            self.ckan_headers['Authorization'] = ckan_api_key
        
        # Configure STAC authentication
        if stac_auth_type == 'bearer' and stac_bearer_token:
            # OAuth2 Bearer Token (e.g., Microsoft Planetary Computer, Azure)
            self.stac_headers['Authorization'] = f'Bearer {stac_bearer_token}'
            
        elif stac_auth_type == 'apikey' and stac_api_key:
            # API Key in header (common pattern)
            self.stac_headers['X-API-Key'] = stac_api_key
            # Some APIs use different header names, you may need to adjust:
            # self.stac_headers['Authorization'] = f'ApiKey {stac_api_key}'
            
        elif stac_auth_type == 'basic' and stac_username:
            # HTTP Basic Authentication (e.g., Planet STAC API)
            import base64
            credentials = f"{stac_username}:{stac_password or ''}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self.stac_headers['Authorization'] = f'Basic {encoded}'
            # Alternative: use requests.auth
            # from requests.auth import HTTPBasicAuth
            # self.stac_auth = HTTPBasicAuth(stac_username, stac_password or '')
    
    def get_ckan_organizations(self) -> List[Dict[str, Any]]:
        """
        Fetch all organizations from CKAN
        
        Returns:
            List of organization dictionaries
        """
        print("Fetching CKAN organizations...")
        
        # First get list of organization IDs
        response = requests.get(
            f"{self.ckan_url}/api/3/action/organization_list",
            headers=self.ckan_headers
        )
        response.raise_for_status()
        org_ids = response.json()['result']
        
        print(f"Found {len(org_ids)} organizations")
        
        # Then get full details for each organization
        organizations = []
        for org_id in org_ids:
            response = requests.get(
                f"{self.ckan_url}/api/3/action/organization_show",
                params={'id': org_id},
                headers=self.ckan_headers
            )
            response.raise_for_status()
            org_data = response.json()['result']
            organizations.append(org_data)
            print(f"  - {org_data.get('title', org_data.get('name'))}")
        
        return organizations
    
    def ckan_org_to_stac_collection(self, ckan_org: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a CKAN organization to a STAC collection
        
        Args:
            ckan_org: CKAN organization dictionary
        
        Returns:
            STAC collection dictionary
        """
        # Example CKAN organization structure:
        # {
        #   "id": "dc8653cf-eb54-4c53-8c49-019a43de02ef",
        #   "name": "california-interagency-treatment-tracking-system",
        #   "title": "California Interagency Treatment Tracking System",
        #   "description": "...",
        #   "image_url": "...",
        #   "created": "2025-12-09T19:43:54.861416",
        #   "is_organization": true,
        #   "state": "active"
        # }
        
        org_id = ckan_org.get('name', ckan_org.get('id'))  # Use name as ID (URL-friendly)
        org_title = ckan_org.get('title', org_id)
        org_description = ckan_org.get('description', f"Collection for {org_title}")
        
        # Create STAC collection
        stac_collection = {
            "stac_version": "1.0.0",
            "type": "Collection",
            "id": org_id,
            "title": org_title,
            "description": org_description,
            
            # License - default to proprietary unless specified
            # You may want to customize this based on your organization's policy
            "license": "proprietary",
            
            # Extent - initially empty, will be populated when items are added
            # This is REQUIRED by STAC spec
            "extent": {
                "spatial": {
                    "bbox": [[-180, -90, 180, 90]]  # Global extent as placeholder
                },
                "temporal": {
                    "interval": [[None, None]]  # Open-ended temporal extent
                }
            },
            
            # Links - REQUIRED by STAC spec
            "links": [
                {
                    "rel": "root",
                    "href": f"{self.stac_url}/",
                    "type": "application/json"
                },
                {
                    "rel": "self",
                    "href": f"{self.stac_url}/collections/{org_id}",
                    "type": "application/json"
                }
            ]
        }
        
        # Add optional keywords if available
        keywords = []
        if 'tags' in ckan_org:
            keywords = [tag['name'] for tag in ckan_org['tags']]
        if keywords:
            stac_collection['keywords'] = keywords
        
        # Add provider information from CKAN organization
        providers = []
        if org_title:
            provider = {
                "name": org_title,
                "roles": ["producer", "licensor"]
            }
            if org_description:
                provider["description"] = org_description[:500]  # Truncate if too long
            if 'image_url' in ckan_org and ckan_org['image_url']:
                provider["url"] = ckan_org['image_url']
            providers.append(provider)
        
        if providers:
            stac_collection['providers'] = providers
        
        # Add creation timestamp as metadata
        if 'created' in ckan_org:
            stac_collection['created'] = ckan_org['created']
        
        # Add custom metadata linking back to CKAN (optional)
        stac_collection['x-ckan-org-id'] = ckan_org.get('id')
        stac_collection['x-ckan-org-name'] = ckan_org.get('name')
        
        return stac_collection
    
    def create_stac_collection(self, collection: Dict[str, Any]) -> bool:
        """
        Create a collection in the STAC API
        
        Args:
            collection: STAC collection dictionary
        
        Returns:
            True if successful, False otherwise
        """
        collection_id = collection['id']
        print(f"Creating STAC collection: {collection_id}")
        
        try:
            # POST to /collections endpoint with authentication
            response = requests.post(
                f"{self.stac_url}/collections",
                json=collection,
                headers=self.stac_headers,
                auth=self.stac_auth  # For Basic Auth if configured
            )
            
            if response.status_code in [200, 201]:
                print(f"  ✓ Successfully created collection: {collection_id}")
                return True
            elif response.status_code == 409:
                print(f"  ⚠ Collection already exists: {collection_id}")
                return False
            elif response.status_code == 401:
                print(f"  ✗ Authentication failed: {response.status_code}")
                print(f"    Response: {response.text}")
                print(f"    Hint: Check your STAC authentication credentials")
                return False
            elif response.status_code == 403:
                print(f"  ✗ Authorization failed (forbidden): {response.status_code}")
                print(f"    Response: {response.text}")
                print(f"    Hint: Your credentials may not have permission to create collections")
                return False
            else:
                print(f"  ✗ Failed to create collection: {response.status_code}")
                print(f"    Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"  ✗ Error creating collection: {e}")
            return False

    def create_stac_item(self, item: Dict[str, Any], collection_id: str) -> bool:
        """
        Create an item in a STAC collection

        Args:
            item: STAC item dictionary (from ckan_package_to_stac_item())
            collection_id: The ID of the collection to add the item to

        Returns:
            True if successful, False otherwise
        """
        item_id = item.get('id', 'unknown')
        print(f"Creating STAC item: {item_id} in collection: {collection_id}")

        try:
            # POST to /collections/{collection_id}/items endpoint with authentication
            response = requests.post(
                f"{self.stac_url}/collections/{collection_id}/items",
                json=item,
                headers=self.stac_headers,
                auth=self.stac_auth  # For Basic Auth if configured
            )

            if response.status_code in [200, 201]:
                print(f"  ✓ Successfully created item: {item_id}")
                return True
            elif response.status_code == 409:
                print(f"  ⚠ Item already exists: {item_id}")
                return False
            elif response.status_code == 401:
                print(f"  ✗ Authentication failed: {response.status_code}")
                print(f"    Response: {response.text}")
                print(f"    Hint: Check your STAC authentication credentials")
                return False
            elif response.status_code == 403:
                print(f"  ✗ Authorization failed (forbidden): {response.status_code}")
                print(f"    Response: {response.text}")
                print(f"    Hint: Your credentials may not have permission to create items")
                return False
            elif response.status_code == 404:
                print(f"  ✗ Collection not found: {collection_id}")
                print(f"    Hint: The collection must exist before adding items to it")
                return False
            elif response.status_code == 400:
                print(f"  ✗ Bad request - invalid item data: {response.status_code}")
                print(f"    Response: {response.text[:500]}")
                return False
            else:
                print(f"  ✗ Failed to create item: {response.status_code}")
                print(f"    Response: {response.text}")
                return False

        except Exception as e:
            print(f"  ✗ Error creating item: {e}")
            return False

    def migrate_organizations(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Migrate all CKAN organizations to STAC collections
        
        Args:
            dry_run: If True, only print what would be done without making changes
        
        Returns:
            Dictionary with migration statistics
        """
        stats = {
            'total_orgs': 0,
            'collections_created': 0,
            'collections_skipped': 0,
            'collections_failed': 0,
            'collections': []
        }
        
        # Fetch organizations from CKAN
        organizations = self.get_ckan_organizations()
        stats['total_orgs'] = len(organizations)
        
        print(f"\n{'='*60}")
        print(f"Converting {len(organizations)} CKAN organizations to STAC collections")
        print(f"Mode: {'DRY RUN (no changes will be made)' if dry_run else 'LIVE (collections will be created)'}")
        print(f"{'='*60}\n")
        
        # Convert and create each collection
        for org in organizations:
            print(f"\nProcessing: {org.get('title', org.get('name'))}")
            
            # Convert to STAC collection
            collection = self.ckan_org_to_stac_collection(org)
            stats['collections'].append(collection)
            
            # Show what will be created
            print(f"  Collection ID: {collection['id']}")
            print(f"  Title: {collection['title']}")
            print(f"  Description: {collection['description'][:100]}...")
            
            if not dry_run:
                # Actually create the collection
                if self.create_stac_collection(collection):
                    stats['collections_created'] += 1
                else:
                    stats['collections_failed'] += 1
            else:
                print("  [DRY RUN] Would create this collection")
                stats['collections_created'] += 1
        
        # Print summary
        print(f"\n{'='*60}")
        print("Migration Summary:")
        print(f"  Total organizations: {stats['total_orgs']}")
        print(f"  Collections created: {stats['collections_created']}")
        print(f"  Collections failed: {stats['collections_failed']}")
        print(f"{'='*60}\n")
        
        return stats
    
    def save_collections_to_file(self, filename: str = "stac_collections.json"):
        """
        Generate and save STAC collections to a JSON file
        
        Args:
            filename: Output filename
        """
        print(f"Generating STAC collections and saving to {filename}...")
        
        organizations = self.get_ckan_organizations()
        collections = [self.ckan_org_to_stac_collection(org) for org in organizations]
        
        with open(filename, 'w') as f:
            json.dump(collections, f, indent=2)
        
        print(f"✓ Saved {len(collections)} collections to {filename}")


def _get_extra_value(extras: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    """
    Helper function to extract a value from CKAN extras by key

    Args:
        extras: List of dicts with 'key' and 'value' fields
        key: The key to look for
        default: Default value if key not found

    Returns:
        The value associated with the key, or default
    """
    for extra in extras:
        if extra.get('key') == key:
            return extra.get('value', default)
    return default


def _extras_to_dict(extras: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert CKAN extras list to a dictionary

    Args:
        extras: List of dicts with 'key' and 'value' fields

    Returns:
        Dictionary mapping keys to values
    """
    return {extra.get('key'): extra.get('value') for extra in extras if extra.get('key')}


def _parse_bbox(bbox_value: Any) -> Optional[List[float]]:
    """
    Parse bbox from various CKAN formats to STAC bbox format [minLon, minLat, maxLon, maxLat]

    Args:
        bbox_value: Bbox value from CKAN (could be JSON string, list, WKT, etc.)

    Returns:
        STAC bbox as [minLon, minLat, maxLon, maxLat] or None
    """
    if not bbox_value:
        return None

    # If already a list
    if isinstance(bbox_value, list):
        if len(bbox_value) == 4:
            return bbox_value
        # Handle nested bbox [[minLon, minLat, maxLon, maxLat]]
        if len(bbox_value) == 1 and isinstance(bbox_value[0], list):
            return bbox_value[0]

    # If JSON string
    if isinstance(bbox_value, str):
        try:
            import json
            parsed = json.loads(bbox_value)
            if isinstance(parsed, list):
                if len(parsed) == 4:
                    return parsed
                if len(parsed) == 1 and isinstance(parsed[0], list):
                    return parsed[0]
        except json.JSONDecodeError:
            pass

    return None


def _parse_geojson_to_geometry(geojson_value: Any) -> Optional[Dict[str, Any]]:
    """
    Parse GeoJSON from CKAN extras to STAC geometry

    Args:
        geojson_value: GeoJSON value (string or dict)

    Returns:
        GeoJSON geometry dict or None
    """
    if not geojson_value:
        return None

    # If already a dict
    if isinstance(geojson_value, dict):
        return geojson_value.get('geometry', geojson_value)

    # If JSON string
    if isinstance(geojson_value, str):
        try:
            import json
            parsed = json.loads(geojson_value)
            if isinstance(parsed, dict):
                return parsed.get('geometry', parsed)
        except json.JSONDecodeError:
            pass

    return None


def ckan_package_to_stac_item(
    ckan_package: Dict[str, Any],
    stac_url: str,
    collection_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convert a CKAN package to a STAC Item

    Args:
        ckan_package: CKAN package dictionary
        stac_url: Base URL of STAC API (for links)
        collection_id: Optional collection ID to link this item to

    Returns:
        STAC Item dictionary
    """
    # Extract extras as dict for easier access
    extras = _extras_to_dict(ckan_package.get('extras', []))

    # Determine ID (use name as it's URL-friendly)
    item_id = ckan_package.get('name', ckan_package.get('id'))

    # Parse spatial information
    geometry = None
    bbox = None

    # Try spatialCov first (GeoJSON)
    spatial_cov = extras.get('spatialCov')
    if spatial_cov:
        geometry = _parse_geojson_to_geometry(spatial_cov)

    # Try dataBbox for bbox
    data_bbox = extras.get('dataBbox')
    if data_bbox:
        bbox = _parse_bbox(data_bbox)

    # If no bbox but we have geometry, calculate bbox from geometry
    if not bbox and geometry:
        # Simple bbox extraction from geometry
        # For a proper implementation, use a library like shapely
        coords = geometry.get('coordinates', [])
        if coords:
            # This is simplified - real implementation should handle all geometry types
            try:
                lons = []
                lats = []
                def extract_coords(coords, lons, lats):
                    for c in coords:
                        if isinstance(c, list):
                            if len(c) == 2 and isinstance(c[0], (int, float)):
                                lons.append(c[0])
                                lats.append(c[1])
                            else:
                                extract_coords(c, lons, lats)
                extract_coords(coords, lons, lats)
                if lons and lats:
                    bbox = [min(lons), min(lats), max(lons), max(lats)]
            except Exception:
                pass

    # Parse temporal information
    start_datetime = extras.get('startDateTime')
    end_datetime = extras.get('endDateTime')
    datetime_value = start_datetime or end_datetime

    # Build properties - start with core STAC and CKAN top-level fields
    properties = {
        # Core STAC properties
        'title': ckan_package.get('title'),
        'description': ckan_package.get('notes'),

        # Organization - owner_org is the CKAN organization ID
        'owner_org': ckan_package.get('owner_org'),

        # Organization name from extras (CKAN is source of truth)
        'organization': extras.get('organization'),

        # Keywords from tags
        'keywords': [tag.get('name') for tag in ckan_package.get('tags', [])]
            if ckan_package.get('tags') else [],

        # Groups from CKAN groups (for categorization)
        'groups': [group.get('name') for group in ckan_package.get('groups', [])]
            if ckan_package.get('groups') else [],

        # Original CKAN references
        'x-ckan-id': ckan_package.get('id'),
        'x-ckan-name': ckan_package.get('name'),
    }

    # Add ALL extras fields to properties (future-proof)
    # This ensures any new NDP fields are automatically included
    for key, value in extras.items():
        # Skip fields that are used for geometry/bbox/datetime (already handled)
        if key in ['spatialCov', 'dataBbox', 'startDateTime', 'endDateTime']:
            continue

        # Add the extra field to properties
        # If key already exists, the extras value takes precedence
        properties[key] = value

    # Build assets from CKAN resources
    assets = {}
    resources = ckan_package.get('resources', [])
    for i, resource in enumerate(resources):
        asset_key = resource.get('name', f'resource_{i}')

        # Determine asset roles
        roles = ['data']
        if resource.get('mimetype', '').startswith('image/'):
            roles.append('thumbnail')

        # Convert resource extras to dict
        resource_extras = _extras_to_dict(resource.get('extras', []))

        # Build asset with standard STAC fields
        asset = {
            'href': resource.get('url'),
            'title': resource.get('name') or resource.get('description'),
            'description': resource.get('description'),
            'type': resource.get('mimetype'),
            'roles': roles
        }

        # Map common CKAN resource fields to STAC asset fields
        if resource.get('resSize'):
            asset['file:size'] = resource.get('resSize')

        if resource.get('license') or resource.get('otherLicense'):
            asset['license'] = resource.get('license') or resource.get('otherLicense')

        if resource.get('issueDate'):
            asset['created'] = resource.get('issueDate')

        if resource.get('lastUpdateDate'):
            asset['modified'] = resource.get('lastUpdateDate')

        # Add ALL resource extras to asset properties (future-proof)
        for key, value in resource_extras.items():
            # Skip if already mapped to a standard STAC field
            if key in ['resSize', 'license', 'otherLicense', 'issueDate', 'lastUpdateDate']:
                continue
            # Use x- prefix for extras to avoid conflicts with standard STAC fields
            asset[f'x-{key}'] = value

        # Remove None values from asset
        asset = {k: v for k, v in asset.items() if v is not None}
        assets[asset_key] = asset

    # Build links
    links = [
        {
            'rel': 'root',
            'href': f"{stac_url.rstrip('/')}/",
            'type': 'application/json'
        },
        {
            'rel': 'self',
            'href': f"{stac_url.rstrip('/')}/collections/{collection_id or 'unknown'}/items/{item_id}",
            'type': 'application/json'
        }
    ]

    if collection_id:
        links.append({
            'rel': 'parent',
            'href': f"{stac_url.rstrip('/')}/collections/{collection_id}",
            'type': 'application/json'
        })
        links.append({
            'rel': 'collection',
            'href': f"{stac_url.rstrip('/')}/collections/{collection_id}",
            'type': 'application/json'
        })

    # Build STAC Item
    stac_item = {
        'stac_version': '1.0.0',
        'type': 'Feature',
        'id': item_id,
        'geometry': geometry,
        'bbox': bbox,
        'properties': properties,
        'links': links,
        'assets': assets
    }

    # Add datetime if available (required by STAC spec)
    # Use "null" if no datetime available (open-ended temporal extent)
    if datetime_value:
        stac_item['datetime'] = datetime_value
    else:
        stac_item['datetime'] = None

    # Add collection reference if provided
    if collection_id:
        stac_item['collection'] = collection_id

    return stac_item


def save_to_stac(
    ckan_package: Dict[str, Any],
    ckan_org: Any,
    logger: Any = None
) -> bool:
    """
    Register a CKAN package to STAC.
    This is a wrapper function that handles the entire STAC registration flow.

    Args:
        ckan_package: CKAN package dictionary (from Prekan)
        ckan_org: CKAN organization model object (with .name, .title, .description)
        logger: Optional logger for logging

    Returns:
        True if successful (or if STAC is disabled), False if failed
    """
    # Check if STAC is enabled
    if not STAC_ENABLED:
        if logger:
            logger.info("STAC registration is disabled")
        return True  # Not an error, just skipped

    try:
        # Get extras as dict
        extras = {e['key']: e['value'] for e in ckan_package.get('extras', [])}

        # Check if dataset has spatial AND temporal coverage
        has_spatial = bool(extras.get('spatialCov') or extras.get('dataBbox'))
        has_temporal = bool(extras.get('startDateTime') or extras.get('endDateTime'))

        if not (has_spatial and has_temporal):
            if logger:
                logger.info(
                    f"Dataset '{ckan_package.get('name')}' missing spatial or "
                    f"temporal coverage, skipping STAC registration"
                )
            return True  # Not an error, just skipped

        # Use organization NAME (not UUID) as collection ID
        collection_id = ckan_org.name

        # Initialize STAC migrator
        migrator = CKANToSTACMigration(
            ckan_url="",  # Not needed for STAC operations
            stac_url=STAC_URL,
            stac_auth_type='apikey',
            stac_api_key=STAC_API_KEY
        )

        # Ensure collection exists (create from organization if not)
        if logger:
            logger.info(f"Ensuring STAC collection exists: {collection_id}")

        try:
            # Build CKAN org dict from model object
            ckan_org_dict = {
                'name': ckan_org.name,
                'title': ckan_org.title,
                'description': ckan_org.description,
                'image_url': getattr(ckan_org, 'image_url', None)
            }
            stac_collection = migrator.ckan_org_to_stac_collection(ckan_org_dict)
            migrator.create_stac_collection(stac_collection)
        except Exception as e:
            # Collection might already exist, log and continue
            if logger:
                logger.warning(f"Collection creation note: {e}")

        # Convert CKAN package to STAC item
        stac_item = ckan_package_to_stac_item(
            ckan_package=ckan_package,
            stac_url=STAC_URL,
            collection_id=collection_id
        )

        # Save to STAC
        if logger:
            logger.info(f"Registering dataset '{ckan_package.get('name')}' to STAC collection '{collection_id}'")

        success = migrator.create_stac_item(stac_item, collection_id)

        if success and logger:
            logger.info(f"Successfully registered dataset to STAC")
        elif not success and logger:
            logger.warning(f"Failed to register dataset to STAC")

        return success

    except Exception as e:
        if logger:
            logger.error(f"STAC registration error: {e}")
        return False  # Return False to indicate failure, but don't raise


def main():
    """
    Main function - example usage with different authentication methods
    """
    # Configuration - UPDATE THESE VALUES
    CKAN_URL = "https://your-ckan-instance.org"  # Your CKAN URL
    STAC_URL = "https://ndp-test.sdsc.edu/stac"  # Your STAC API URL
    CKAN_API_KEY = None  # Optional: Your CKAN API key if needed

    # ===== STAC AUTHENTICATION OPTIONS =====
    # For NDP STAC, use API Key authentication:

    # NDP API Key authentication (configured)
    migrator = CKANToSTACMigration(
        ckan_url=CKAN_URL,
        stac_url=STAC_URL,
        ckan_api_key=CKAN_API_KEY,
        stac_auth_type='apikey',
        stac_api_key='replaceDIMMsoon'  # NDP STAC API key
    )

    # Option 1: No authentication (public STAC API)
    # migrator = CKANToSTACMigration(
    #     ckan_url=CKAN_URL,
    #     stac_url=STAC_URL,
    #     ckan_api_key=CKAN_API_KEY
    # )

    # Option 2: Bearer Token (OAuth2) - e.g., Microsoft Planetary Computer, Azure
    # migrator = CKANToSTACMigration(
    #     ckan_url=CKAN_URL,
    #     stac_url=STAC_URL,
    #     ckan_api_key=CKAN_API_KEY,
    #     stac_auth_type='bearer',
    #     stac_bearer_token='your-bearer-token-here'
    # )

    # Option 4: Basic Authentication - e.g., Planet STAC API
    # migrator = CKANToSTACMigration(
    #     ckan_url=CKAN_URL,
    #     stac_url=STAC_URL,
    #     ckan_api_key=CKAN_API_KEY,
    #     stac_auth_type='basic',
    #     stac_username='your-username',
    #     stac_password='your-password'  # Optional
    # )

    # ===== USAGE EXAMPLES =====

    # Example 1: Convert a CKAN package to STAC item and save it
    # ckan_package = {
    #     "name": "my-dataset",
    #     "title": "My Dataset",
    #     "notes": "Dataset description",
    #     "owner_org": "data-insight",
    #     "tags": [{"name": "climate"}],
    #     "extras": [
    #         {"key": "startDateTime", "value": "2024-01-01T00:00:00Z"}
    #     ],
    #     "resources": []
    # }
    #
    # # Convert to STAC item
    # stac_item = ckan_package_to_stac_item(
    #     ckan_package=ckan_package,
    #     stac_url=STAC_URL,
    #     collection_id="my-collection"  # Optional: link to a collection
    # )
    #
    # # Save to STAC
    # if migrator.create_stac_item(stac_item, collection_id="my-collection"):
    #     print("Item created successfully!")

    # Option 1: Dry run (preview what will be created)
    # print("Running dry run to preview collections...")
    # stats = migrator.migrate_organizations(dry_run=True)
    
    # Option 1: Dry run (preview what will be created)
    print("Running dry run to preview collections...")
    stats = migrator.migrate_organizations(dry_run=True)
    
    # Option 2: Save to file for review
    print("\nSaving collections to file for review...")
    migrator.save_collections_to_file("stac_collections.json")
    
    # Option 3: Uncomment to actually create collections (after reviewing and setting auth)
    # print("\nProceeding with actual migration...")
    # stats = migrator.migrate_organizations(dry_run=False)
    
    print("\nMigration complete!")
    print("\nNext steps:")
    print("1. Review the generated stac_collections.json file")
    print("2. Verify the STAC collections structure")
    print("3. Configure STAC authentication if required")
    print("4. Update the license field if needed")
    print("5. Uncomment the live migration code to create collections")
    print("6. Later: Migrate only spatial-temporal datasets to STAC items")


if __name__ == "__main__":
    main()

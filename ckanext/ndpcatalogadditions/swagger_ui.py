import os
import json
from flask import make_response, jsonify
import ckan.plugins.toolkit as toolkit

server_url = os.getenv('CKANEXT__KEYCLOAK__REDIRECT_URI')
ckan_url = server_url.replace('/user/sso_login', '').replace('/catalog', '/catalog2')

def swagger_ui():
    """Return Swagger UI HTML page"""
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NDP Catalog API Documentation</title>
    <link rel="stylesheet" type="text/css" href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.10.5/swagger-ui.css">
    <style>
        body { margin: 0; padding: 0; }
        .swagger-ui .topbar { display: none; }
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.10.5/swagger-ui-bundle.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.10.5/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {
            SwaggerUIBundle({
                url: "openapi.json",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [SwaggerUIBundle.plugins.DownloadUrl],
                layout: "StandaloneLayout",
                persistAuthorization: true
            });
        };
    </script>
</body>
</html>'''
    response = make_response(html_content)
    response.headers['Content-Type'] = 'text/html'
    return response


def openapi_spec():
    """Return OpenAPI specification as JSON"""
    # Get the base URL dynamically
    base_url = f"{ckan_url}/ndp"
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "NDP Catalog API",
            "description": "API for managing datasets and groups in the NDP Catalog system. This API follows CKAN endpoint conventions.",
            "version": "1.0.0",
            "contact": {"name": "SDSC NDP Support"}
        },
        "servers": [
            {
                "url": base_url,
                "description": "NDP API Environment"
            }
        ],
        "security": [{"bearerAuth": []}],
        "tags": [
            {"name": "Prekan Packages", "description": "Prekan package operations for end users"},
            {"name": "CKAN Packages", "description": "CKAN package operations for end users"},
            {"name": "Prekan Package Adminstration", "description": "Package review and approval operations for admins"},
            {"name": "Groups", "description": "CKAN Group management operations for catalogs"},
        ],
        "paths": {
            "/package_create": {
                "post": {
                    "summary": "Create a new dataset package in Prekan",
                    "tags": ["Prekan Packages"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageCreate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package created successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/package_update": {
                "post": {
                    "summary": "Update a dataset package in Prekan",
                    "tags": ["Prekan Packages"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageUpdate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package updated successfully"},
                        "401": {"description": "Unauthorized"},
                        "404": {"description": "Package not found"}
                    }
                }
            },
            "/package_validate": {
                "post": {
                    "summary": "Validate a dataset package without saving it",
                    "tags": ["Prekan Packages"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageCreate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package is valid"},
                        "401": {"description": "Unauthorized"},
                        "422": {"description": "Validation errors"}
                    }
                }
            },
            "/package_delete": {
                "post": {
                    "summary": "Delete a dataset package in Prekan",
                    "tags": ["Prekan Packages"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package deleted successfully"},
                        "401": {"description": "Unauthorized"},
                        "404": {"description": "Package not found"}
                    }
                }
            },
            "/package_purge": {
                "post": {
                    "summary": "Permanently purge a dataset package in Prekan",
                    "tags": ["Prekan Package Adminstration"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package purged successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/my_prekan_package": {
                "get": {
                    "summary": "Get a specific Prekan package",
                    "tags": ["Prekan Packages"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                },
                # "post": {
                #    "summary": "Get user's pre-CKAN packages",
                #    "tags": ["Package Workflow"],
                #    "responses": {
                #        "200": {"description": "List retrieved successfully"},
                #        "401": {"description": "Unauthorized"}
                #    }
                #}
            },
            "/my_package_list": {
                "get": {
                    "summary": "List all packages in Prekan for the invoker",
                    "tags": ["Prekan Packages"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                },
                # "post": {
                #    "summary": "List user's packages",
                #    "tags": ["Prekan Packages"],
                #    "responses": {
                #        "200": {"description": "List retrieved successfully"},
                #        "401": {"description": "Unauthorized"}
                #    }
                #}
            },
            "/my_reviewed_package_list": {
                "get": {
                    "summary": "List reviewed packages belonging to the  user",
                    "tags": ["Prekan Packages"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                },
                # "post": {
                #    "summary": "List packages reviewed by user",
                #    "tags": ["Package Workflow"],
                #    "responses": {
                #        "200": {"description": "List retrieved successfully"},
                #        "401": {"description": "Unauthorized"}
                #    }
                #}
            },
            "/package_approve": {
                "post": {
                    "summary": "Approve a package",
                    "description": "Approve a package and invoke Solr reindex.",
                    "tags": ["Prekan Package Adminstration"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageApproval"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package approved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/package_reject": {
                "post": {
                    "summary": "Reject a package",
                    "tags": ["Prekan Package Adminstration"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageApproval"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package rejected successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/package_list": {
                "get": {
                    "summary": "List all packages in Prekan by admin",
                    "tags": ["Prekan Package Adminstration"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"}
                    }
                },
                # "post": {
                #    "summary": "List all packages",
                #    "tags": ["Prekan Packages"],
                #    "responses": {
                #        "200": {"description": "List retrieved successfully"}
                #    }
                #}
            },
            "/get_approved_package": {
                "get": {
                    "summary": "Get a specific approved package for the invoker",
                    "tags": ["CKAN Packages"],
                    "responses": {
                        "200": {"description": "Package details retrieved"},
                        "404": {"description": "Package not found"}
                    }
                },
                # "post": {
                #    "summary": "Get approved package details",
                #    "tags": ["Package Workflow"],
                #    "requestBody": {
                #        "required": True,
                #        "content": {
                #            "application/json": {
                #                "schema": {"$ref": "#/components/schemas/PackageGet"}
                #            }
                #        }
                #    },
                #    "responses": {
                #        "200": {"description": "Package details retrieved"},
                #        "404": {"description": "Package not found"}
                #    }
                #}
            },
            "/my_approved_packages": {
                "get": {
                    "summary": "List all approved packages for the invoker",
                    "tags": ["CKAN Packages"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                },
                # "post": {
                #    "summary": "List user's approved packages",
                #    "tags": ["Package Workflow"],
                #    "responses": {
                #        "200": {"description": "List retrieved successfully"},
                #        "401": {"description": "Unauthorized"}
                #    }
                #}
            },
            "/update_my_approved_package": {
                "post": {
                    "summary": "Update user's approved package",
                    "tags": ["CKAN Packages"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PackageUpdate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package updated successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/search_package": {
                "get": {
                    "summary": "Allows users to search CKAN packages, including those that are private or restricted to authorized users.",
                    "tags": ["CKAN Packages"],
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Search terms"
                        },
                        {
                            "name": "fq",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "fieldname:value"
                        },
                    ],
                    "responses": {
                        "200": {"description": "Search results retrieved"}
                    }
                },
                #"post": {
                #    "summary": "Search packages",
                #    "tags": ["Prekan Packages"],
                #    "requestBody": {
                #        "required": True,
                #        "content": {
                #            "application/json": {
                #                "schema": {"$ref": "#/components/schemas/PackageSearch"}
                #            }
                #        }
                #    },
                #    "responses": {
                #        "200": {"description": "Search results retrieved"}
                #    }
                #}
            },
            "/group_create": {
                "post": {
                    "summary": "Create a new group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupCreate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Group created successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/group_show": {
                "post": {
                    "summary": "Show group details",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupGet"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Group details retrieved"},
                        "404": {"description": "Group not found"}
                    }
                }
            },
            "/group_update": {
                "post": {
                    "summary": "Update a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupUpdate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Group updated successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/group_delete": {
                "post": {
                    "summary": "Delete a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Group deleted successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/group_purge": {
                "post": {
                    "summary": "Permanently purge a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Group purged successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/member_create": {
                "post": {
                    "summary": "Add a member to a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/MemberCreate"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Member added successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/member_delete": {
                "post": {
                    "summary": "Remove a member from a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/MemberDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Member removed successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/group_package_add": {
                "post": {
                    "summary": "Add a package to a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupPackageAdd"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package added to group successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/group_package_delete": {
                "post": {
                    "summary": "Remove a package from a group",
                    "tags": ["Groups"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GroupPackageDelete"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Package removed from group successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/my_groups": {
                "get": {
                    "summary": "List user's groups",
                    "tags": ["Groups"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"},
                        "401": {"description": "Unauthorized"}
                    }
                }
            },
            "/list_groups": {
                "get": {
                    "summary": "List all groups",
                    "tags": ["Groups"],
                    "responses": {
                        "200": {"description": "List retrieved successfully"}
                    }
                }
            },
            #"/subgroup_create": {
            #    "post": {
            #        "summary": "Create a subgroup",
            #        "tags": ["Groups"],
            #        "requestBody": {
            #            "required": True,
            #            "content": {
            #                "application/json": {
            #                    "schema": {"$ref": "#/components/schemas/SubgroupCreate"}
            #                }
            #            }
            #        },
            #        "responses": {
            #            "200": {"description": "Subgroup created successfully"},
            #            "401": {"description": "Unauthorized"}
            #        }
            #    }
            #}
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "JWT token for authentication"
                }
            },
            "schemas": {
                "PackageCreate": {
                    "type": "object",
                    "required": ["name", "title", "notes", "extras", "resources", "tags"],
                    "properties": {
                        "title": {"type": "string", "example": "My Dataset"},
                        "name": {"type": "string", "example": "my-dataset"},
                        "notes": {"type": "string"},
                        "owner_org": {"type": "string"},
                        "tags": {"type": "array"},
                        "extras": {"type": "array", "items": {"$ref": "#/components/schemas/Extra"}},
                        "resources": {"type": "array", "items": {"$ref": "#/components/schemas/Resource"}}
                    }
                },
                "PackageUpdate": {
                    "type": "object",
                    "required": ["name", "title", "notes", "extras", "resources", "tags"],
                    "properties": {
                        "title": {"type": "string", "example": "My Dataset"},
                        "name": {"type": "string", "example": "my-dataset"},
                        "notes": {"type": "string"},
                        "owner_org": {"type": "string"},
                        "tags": {"type": "array"},
                        "extras": {"type": "array", "items": {"$ref": "#/components/schemas/Extra"}},
                        "resources": {"type": "array", "items": {"$ref": "#/components/schemas/Resource"}}
                    }
                },
                "PackageDelete": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string", "example": "my-dataset"}
                    }
                },
                #"PackageGet": {
                #    "type": "object",
                #    "required": ["id"],
                #    "properties": {
                #        "id": {"type": "string"}
                #    }
                #},
                "PackageApproval": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"}
                    }
                },
                #"PackageSearch": {
                #    "type": "object",
                #    "properties": {
                #        "q": {"type": "string", "description": "Search query"},
                #        "rows": {"type": "integer", "description": "Number of results"},
                #        "start": {"type": "integer", "description": "Start position"}
                #    }
                #},
                "GroupCreate": {
                    "type": "object",
                    "required": ["name", "title"],
                    "properties": {
                        "name": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"}
                    }
                },
                "GroupUpdate": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"}
                    }
                },
                "GroupDelete": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"}
                    }
                },
                "GroupGet": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"}
                    }
                },
                "MemberCreate": {
                    "type": "object",
                    "required": ["id", "username", "role"],
                    "properties": {
                        "id": {"type": "string", "description": "Group ID"},
                        "username": {"type": "string"},
                        "role": {"type": "string", "enum": ["member", "editor", "admin"]}
                    }
                },
                "MemberDelete": {
                    "type": "object",
                    "required": ["id", "username"],
                    "properties": {
                        "id": {"type": "string", "description": "Group ID"},
                        "username": {"type": "string"}
                    }
                },
                "GroupPackageAdd": {
                    "type": "object",
                    "required": ["id", "package_id"],
                    "properties": {
                        "id": {"type": "string", "description": "Group ID"},
                        "package_id": {"type": "string", "description": "Package ID"}
                    }
                },
                "GroupPackageDelete": {
                    "type": "object",
                    "required": ["id", "package_id"],
                    "properties": {
                        "id": {"type": "string", "description": "Group ID"},
                        "package_id": {"type": "string", "description": "Package ID"}
                    }
                },
                #"SubgroupCreate": {
                #    "type": "object",
                #    "required": ["name", "title", "parent_id"],
                #    "properties": {
                #        "name": {"type": "string"},
                #        "title": {"type": "string"},
                #        "parent_id": {"type": "string", "description": "Parent group ID"},
                #        "description": {"type": "string"}
                #    }
                #},
                "Extra": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"}
                    }
                },
                "Resource": {
                    "type": "object",
                    "required": ["name", "description", "format", "status", "mimetype"],
                    "properties": {
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                        "description": {"type": "string"},
                        "format": {"type": "string"},
                        "mimetype": {"type": "string"},
                        "status": {"type": "string"},
                    }
                }
            }
        }
    }
    
    return make_response(json.dumps(spec, sort_keys=False), 200, {'Content-Type': 'application/json'})

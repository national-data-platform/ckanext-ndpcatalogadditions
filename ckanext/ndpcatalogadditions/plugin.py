import json
from datetime import datetime
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from flask import Blueprint
from ckanext.ndp.controller import (
    create_package,
    update_package,
    delete_package,
    purge_package,
    my_prekan_package,
    list_my_packages,
    list_my_reviewed_packages,
    approve_package,
    reject_package,
    list_all_packages,
    get_approved_package,
    my_approved_packages,
    update_my_approved_package,
    get_api_token,
    get_ckan_id,
    search_package
)
from ckanext.ndp.group import (
    create_group,
    show_group,
    update_group,
    delete_group,
    purge_group,
    create_member,
    delete_member,
    group_package_add,
    group_package_delete,
    my_groups,
    list_groups,
    create_subgroup
)


def jupyterhub_endpoint():
    value = toolkit.config.get('ckanext.ndp.jupyterhub_endpoint')
    return value

class NdpPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IPackageController, inherit=True)
    
    # IConfigurer
    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "ndp")

    # ITemplateHelpers
    def get_helpers(self):
        return {'ndp_jupyterhub_endpoint': jupyterhub_endpoint}


    # IPackageController
    def before_dataset_search(self, search_params):
        # Extract 'extras' from search_params
        extras = search_params.get('extras', {})
    
        if isinstance(extras, str):
            try:
                extras = json.loads(extras)
            except json.JSONDecodeError:
                extras = {}
    
        # Ensure extras is a dictionary
        if not isinstance(extras, dict):
            extras = {}
        
        # Extract temporal fields from extras
        start_date = extras.get('temporal_start') or search_params.get('temporal_start')
        end_date = extras.get('temporal_end') or search_params.get('temporal_end')

        # Build the filter query
        fq = search_params.get('fq', '')
        if start_date:
            fq += f' +temporal_start:[* TO {end_date}]'
        if end_date:
            fq += f' +temporal_end:[{start_date} TO *]'

        # Remove temporal fields from search_params after processing
        search_params.pop('temporal_start', None)
        search_params.pop('temporal_end', None)
        search_params['fq'] = fq.strip()

        return search_params


    def _parse_date(self, date_str):
        formats = ['%Y%m%d', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%SZ']
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).isoformat() + 'Z'
            except ValueError:
                continue
        return None

    
    def _calculate_bbox(self, coords):
        lons, lats = zip(*coords)
        return min(lons), min(lats), max(lons), max(lats)
    
        
    def before_dataset_index(self, pkg_dict):
        temporal = pkg_dict.get('extras_temporal')
        if temporal:
            try:
                temporal_json = json.loads(temporal)
                if 'startTime' in temporal_json.keys():
                    pkg_dict['temporal_start'] = self._parse_date(temporal_json.get('startTime'))
                if 'endTime' in temporal_json.keys():
                    pkg_dict['temporal_end'] = self._parse_date(temporal_json.get('endTime'))
            except json.JSONDecodeError:
                # Log the error or handle it as appropriate
                pass

        """
        spatial_input = pkg_dict.get('extras_spatial')
        if spatial_input:
            try:
                spatial = json.loads(spatial_input)
                if spatial['type'] == 'Polygon':
                    coords = spatial['coordinates'][0]
                    minx, miny, maxx, maxy = self._calculate_bbox(coords)
                    pkg_dict['spatial_minx'] = minx
                    pkg_dict['spatial_miny'] = miny
                    pkg_dict['spatial_maxx'] = maxx
                    pkg_dict['spatial_maxy'] = maxy
            except (ValueError, KeyError, IndexError):
		# Log error or handle invalid JSON
                import traceback
                traceback.print_exc()
                pass
        """    
        return pkg_dict

    # IBlueprint
    def get_blueprint(self):
        blueprint = Blueprint(self.name, self.__module__)

        blueprint.add_url_rule(
            u'/ndp/package_create',
            u'create_package',
            create_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/package_update',
            u'update_package',
            update_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/package_delete',
            u'delete_package',
            delete_package,
            methods=['POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/package_purge',
            u'purge_package',
            purge_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/my_prekan_package',  
            u'my_prekan_package',
            my_prekan_package,
            methods=['GET', 'POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/my_package_list',
            u'my_package_list',
            list_my_packages,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/my_reviewed_package_list',
            u'my_reviewed_package_list',
            list_my_reviewed_packages,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/package_approve',
            u'approve_package',
            approve_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/package_reject',
            u'reject_package',
            reject_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/package_list',
            u'list_package',
            list_all_packages,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/get_approved_package',  
            u'get_approved_package',
            get_approved_package,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/my_approved_packages',  
            u'my_approved_packages',
            my_approved_packages,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/update_my_approved_package',  
            u'update_my_approved_package',
            update_my_approved_package,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/search_package',  
            u'search_package',
            search_package,
            methods=['GET', 'POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/get_api_token',  
            u'get_api_token',
            get_api_token,
            methods=['GET', 'POST']
        )

        blueprint.add_url_rule(
            u'/ndp/get_ckan_id',  
            u'get_ckan_id',
            get_ckan_id,
            methods=['GET']
        )

        
        #########################
        
        blueprint.add_url_rule(
            u'/ndp/group_create',
            u'create_group',
            create_group,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/group_show',
            u'show_group',
            show_group,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/group_update',
            u'update_group',
            update_group,
            methods=['POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/group_delete',
            u'delete_group',
            delete_group,
            methods=['POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/group_purge',
            u'purge_group',
            purge_group,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/member_create',
            u'create_member',
            create_member,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/member_delete',
            u'delete_member',
            delete_member,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/group_package_add',
            u'group_package_add',
            group_package_add,
            methods=['POST']
        )

        blueprint.add_url_rule(
            u'/ndp/group_package_delete',
            u'group_package_delete',
            group_package_delete,
            methods=['POST']
        )
        
        blueprint.add_url_rule(
            u'/ndp/my_groups',
            u'my_groups',
            my_groups,
            methods=['GET']
        )

        blueprint.add_url_rule(
            u'/ndp/list_groups',
            u'list_groups',
            list_groups,
            methods=['GET']
        )
        
        blueprint.add_url_rule(
            u'/ndp/subgroup_create',
            u'create_subgroup',
            create_subgroup,
            methods=['POST']
        )

        return blueprint

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from flask import Blueprint
from ckanext.ndpcatalogadditions.controller import (
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
from ckanext.ndpcatalogadditions.group import (
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

import json
from datetime import datetime


class NdpcatalogadditionsPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)
    
    # IConfigurer
    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "ndp")
        
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


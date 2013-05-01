# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2013 PLUMgrid, Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Edgar Magana, emagana@plumgrid.com, PLUMgrid, Inc.

"""
Quantum PLUMgrid Plug-in for PLUMgrid Virtual Technology
This plugin will forward authenticated REST API calls
to the Network Operating System by PLUMgrid called NOS
"""

import sys

from oslo.config import cfg
import json
import re
import sys

from quantum.common import exceptions as q_exc
from quantum.db import api as db
from quantum.db import db_base_plugin_v2
from quantum.db import l3_db
from quantum.extensions import l3
from quantum.extensions import portbindings
from quantum.openstack.common import log as logging
from quantum.plugins.plumgrid.common import exceptions as plum_excep
from quantum.plugins.plumgrid.plumgrid_nos_plugin.plugin_ver import VERSION
from quantum.plugins.plumgrid.plumgrid_nos_plugin import plumgrid_nos_snippets
from quantum.plugins.plumgrid.plumgrid_nos_plugin import rest_connection
from quantum import policy


LOG = logging.getLogger(__name__)


nos_server_opts = [
    cfg.StrOpt('nos_server', default='localhost',
               help=_("PLUMgrid NOS server to connect to")),
    cfg.StrOpt('nos_server_port', default='8080',
               help=_("PLUMgrid NOS server port to connect to")),
    cfg.StrOpt('username', default='username',
               help=_("PLUMgrid NOS admin username")),
    cfg.StrOpt('password', default='password', secret=True,
               help=_("PLUMgrid NOS admin password")),
    cfg.IntOpt('servertimeout', default=5,
               help=_("PLUMgrid NOS server timeout")),]


cfg.CONF.register_opts(nos_server_opts, "PLUMgridNOS")


class QuantumPluginPLUMgridV2(db_base_plugin_v2.QuantumDbPluginV2,
                              l3_db.L3_NAT_db_mixin):

    supported_extension_aliases = ["router", "binding"]

    binding_view = "extension:port_binding:view"
    binding_set = "extension:port_binding:set"

    def __init__(self):
        LOG.info(_('QuantumPluginPLUMgrid Status: Starting Plugin'))

        # PLUMgrid NOS configuration
        nos_plumgrid = cfg.CONF.PLUMgridNOS.nos_server
        nos_port = cfg.CONF.PLUMgridNOS.nos_server_port
        timeout = cfg.CONF.PLUMgridNOS.servertimeout
        self.snippets = plumgrid_nos_snippets.DataNOSPLUMgrid()

        # TODO: (Edgar) These are placeholders for next PLUMgrid release
        nos_username = cfg.CONF.PLUMgridNOS.username
        nos_password = cfg.CONF.PLUMgridNOS.password
        self.rest_conn = rest_connection.RestConnection(nos_plumgrid,
                                                        nos_port, timeout)
        if self.rest_conn is None:
            raise SystemExit(_('QuantumPluginPLUMgrid Status: '
                               'Aborting Plugin'))

        else:
            # Plugin DB initialization
            db.configure_db()

            # PLUMgrid NOS info validation
            LOG.info(_('QuantumPluginPLUMgrid NOS: %s'), nos_plumgrid)
            if not nos_plumgrid:
                raise SystemExit(_('QuantumPluginPLUMgrid Status: '
                                   'NOS value is missing in config file'))

            LOG.debug(_('QuantumPluginPLUMgrid Status: Quantum server with '
                        'PLUMgrid Plugin has started'))

    def create_network(self, context, network):
        """
        Create network core Quantum API
        """

        LOG.debug(_('QuantumPluginPLUMgrid Status: create_network() called'))

        # Plugin DB - Network Create and validation
        tenant_id = self._get_tenant_id_for_create(context,
                                                   network["network"])
        self._network_admin_state(network)

        with context.session.begin(subtransactions=True):
            net = super(QuantumPluginPLUMgridV2, self).create_network(context,
                                                                      network)
            net_id = net["id"]
            try:
                LOG.debug(_('QuantumPluginPLUMgrid Status: %s, %s, %s'),
                          tenant_id, network["network"], net["id"])
                # Create VND
                self._create_vnd(tenant_id)

                # Add bridge to VND
                nos_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                bridge_name = "bridge_" + net_id[:6]
                body_data = self.snippets.create_bridge_body_data(
                    tenant_id, bridge_name)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data)

                # Add classification rule
                # Add bridge to VND
                nos_url = self.snippets.BASE_NOS_URL + tenant_id + "/properties/rule_group/" + net_id[:6]
                body_data = self.snippets.network_level_rule_body_data(
                    tenant_id, net_id, bridge_name)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data)

                # Saving Tenant - Domains in CDB
                # Get the current domain
                #TODO:(Edgar) Complete this code
                """
                LOG.debug(_('Getting domains from CDB'))
                nos_url = self.snippets.CDB_BASE_URL + '__47__0__47__tenant_manager'
                body_data = {}
                tenants_cdb = self.rest_conn.nos_rest_conn(nos_url,
                                             'GET', body_data)
                print tenants_cdb
                print tenant_data
                """

            except:
                err_message = _("PLUMgrid NOS communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        # return created network
        return net

    def update_network(self, context, net_id, network):
        """
        Update network core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgridV2.update_network() called"))
        self._network_admin_state(network)
        tenant_id = self._get_tenant_id_for_create(context, network["network"])

        # Get initial network details
        original_net = super(QuantumPluginPLUMgridV2, self).get_network(
            context, net_id)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Network Update
            new_network = super(
                QuantumPluginPLUMgridV2, self).update_network(context,
                                                              net_id, network)

            # EVO Cube Director does not use net_name
            # updating calls are not required

        # return updated network
        return new_network

    def delete_network(self, context, net_id):
        """
        Delete network core Quantum API
        """
        LOG.debug(_("QuantumPluginPLUMgrid Status: delete_network() called"))
        super(QuantumPluginPLUMgridV2, self).get_network(context, net_id)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Network Delete
            super(QuantumPluginPLUMgridV2,
                                self).delete_network(context, net_id)

            tenant_id = self._get_tenant_id_for_create(context, net_id)
            try:
                bridge_name = "bridge_" + net_id[:6]
                body_data = {}
                nos_url = self.snippets.BASE_NOS_URL + tenant_id + "/ne/" + bridge_name
                self.rest_conn.nos_rest_conn(nos_url,
                                             'DELETE', body_data)
                nos_url = self.snippets.BASE_NOS_URL + tenant_id + "/properties/rule_group/" + net_id[:6]
                self.rest_conn.nos_rest_conn(nos_url,
                                             'DELETE', body_data)

            except:
                err_message = _("PLUMgrid NOS communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

    def create_port(self, context, port):
        """
        Create port core Quantum API
        """
        LOG.debug(_("QuantumPluginPLUMgrid Status: create_port() called"))

        # Port operations on PLUMgrid NOS is an automatic operation from the
        # VIF driver operations in Nova. It requires admin_state_up to be True
        port["port"]["admin_state_up"] = True

        # Plugin DB - Port Create and Return port
        q_port = super(QuantumPluginPLUMgridV2, self).create_port(context,
                                                                  port)
        return self._port_viftype_binding(context, q_port)

    def get_port(self, context, id, fields=None):
        with context.session.begin(subtransactions=True):
            q_port = super(QuantumPluginPLUMgridV2, self).get_port(context, id,
                                                            fields)
            self._port_viftype_binding(context, q_port)
        return self._fields(q_port, fields)

    def get_ports(self, context, filters=None, fields=None):
        with context.session.begin(subtransactions=True):
            q_ports = super(QuantumPluginPLUMgridV2, self).get_ports(context, filters,
                                                              fields)
            for q_port in q_ports:
                self._port_viftype_binding(context, q_port)
        return [self._fields(port, fields) for port in q_ports]

    def update_port(self, context, port_id, port):
        """
        Update port core Quantum API

        """
        LOG.debug(_("QuantumPluginPLUMgrid Status: update_port() called"))

        # Port operations on PLUMgrid NOS is an automatic operation from the
        # VIF driver operations in Nova.

        # Plugin DB - Port Update
        q_port = super(QuantumPluginPLUMgridV2, self).update_port(
            context, port_id, port)
        return self._port_viftype_binding(context, q_port)

    def delete_port(self, context, port_id, l3_port_check=True):
        """
        Delete port core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgrid Status: delete_port() called"))

        # Port operations on PLUMgrid NOS is an automatic operation from the
        # VIF driver operations in Nova.

        # Plugin DB - Port Delete
        super(QuantumPluginPLUMgridV2, self).delete_port(context, port_id)

    def create_subnet(self, context, subnet):
        """
        Create subnet core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgrid Status: create_subnet() called"))
        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Create
            subnet = super(QuantumPluginPLUMgridV2, self).create_subnet(
                context, subnet)

            subnet_details = self._get_subnet(context, subnet["id"])
            net_id = subnet_details["network_id"]
            tenant_id = subnet_details["tenant_id"]

            try:
                if subnet['ip_version'] == 6:
                    raise q_exc.NotImplementedError(
                        _("PLUMgrid doesn't support IPv6."))

                if subnet['enable_dhcp'] == True:
                    # Add dhcp to VND
                    nos_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
                    dhcp_name = "dhcp_" + net_id[:6]
                    bridge_name = "bridge_" + net_id[:6]
                    dhcp_server_ip = "1.0.0.1"
                    dhcp_server_mask = "255.255.255.0"
                    ip_range_start = "1.0.0.10"
                    ip_range_end = "1.0.0.20"
                    dns_ip = "1.0.0.1"
                    default_gateway = "1.0.0.1"

                    body_data = self.snippets.create_dhcp_body_data(
                        dhcp_name, dhcp_server_ip, dhcp_server_mask,
                        ip_range_start, ip_range_end, dns_ip, default_gateway)
                    self.rest_conn.nos_rest_conn(nos_url,
                                                 'PUT', body_data)

                    # Create link between bridge - dhcp
                    nos_url = self.snippets.create_link_url(tenant_id, net_id)
                    body_data = self.snippets.create_link_body_data(
                        bridge_name, dhcp_name)
                    self.rest_conn.nos_rest_conn(nos_url,
                                                 'PUT', body_data)

                    # Add dhcp with values to VND
                    nos_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
                    dhcp_server_ip = self._get_dhcp_ip(subnet['cidr'])
                    mask = subnet['cidr'].split("/")
                    dhcp_server_mask = self._get_mask_from_subnet(mask[1])

                    ip_range_dict = subnet['allocation_pools']
                    ip_range_start = ip_range_dict[0].get('start')
                    ip_range_end = ip_range_dict[0].get('end')
                    if subnet['dns_nameservers']:
                        # PLUMgrid EVO Director DHCP only supports one DNS IP
                        dns_ip = subnet['dns_nameservers'][0]
                    else:
                        dns_ip = dhcp_server_ip

                    default_gateway = subnet['gateway_ip']
                    body_data = self.snippets.create_dhcp_body_data(
                    dhcp_name, dhcp_server_ip, dhcp_server_mask,
                    ip_range_start, ip_range_end, dns_ip, default_gateway)
                    self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data)

                elif subnet['enable_dhcp'] == False:
                    LOG.debug(_("DHCP has NOT been deployed"))

            except:
                err_message = _("PLUMgrid NOS communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return subnet

    def delete_subnet(self, context, subnet_id):
        """
        Delete subnet core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgrid Status: delete_subnet() called"))
        #Collecting subnet info
        subnet_details = self._get_subnet(context, subnet_id)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Delete
            del_subnet = super(QuantumPluginPLUMgridV2, self).delete_subnet(
                context, subnet_id)
            tenant_id = self._get_tenant_id_for_create(context, subnet_id)
            net_id = subnet_details["network_id"]

            try:
                dhcp_name = "dhcp_" + net_id[:6]
                body_data = {}
                nos_url = self.snippets.BASE_NOS_URL + tenant_id + "/ne/" + dhcp_name
                self.rest_conn.nos_rest_conn(nos_url,
                                             'DELETE', body_data)

            except:
                err_message = _("PLUMgrid NOS communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return del_subnet

    def update_subnet(self, context, subnet_id, subnet):
        """
        Update subnet core Quantum API
        """

        LOG.debug(_("update_subnet() called"))
        #Collecting subnet info
        initial_subnet = self._get_subnet(context, subnet_id)
        net_id = initial_subnet["network_id"]
        tenant_id = initial_subnet["tenant_id"]

        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Update
            new_subnet = super(QuantumPluginPLUMgridV2, self).update_subnet(
                context, subnet_id, subnet)

            try:
                # PLUMgrid Server does not support updating resources yet
                dhcp_name = "dhcp_" + net_id[:6]
                nos_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
                dhcp_server_ip = self._get_dhcp_ip(new_subnet['cidr'])
                mask = new_subnet['cidr'].split("/")
                dhcp_server_mask = self._get_mask_from_subnet(mask[1])
                ip_range_dict = new_subnet['allocation_pools']
                ip_range_start = ip_range_dict[0].get('start')
                ip_range_end = ip_range_dict[0].get('end')
                if new_subnet['dns_nameservers']:
                    # PLUMgrid EVO Director DHCP only supports one DNS IP
                    dns_ip = new_subnet['dns_nameservers'][0]
                else:
                    dns_ip = dhcp_server_ip
                default_gateway = new_subnet['gateway_ip']
                body_data = self.snippets.create_dhcp_body_data(
                dhcp_name, dhcp_server_ip, dhcp_server_mask,
                ip_range_start, ip_range_end, dns_ip, default_gateway)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid NOS communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return new_subnet


    def create_router(self, context, router):
        """
        Create router extension Quantum API
        """
        LOG.debug(_("QuantumPluginPLUMgrid Status: create_router() called"))

        tenant_id = self._get_tenant_id_for_create(context, router["router"])

        with context.session.begin(subtransactions=True):

            # create router in DB
            router = super(QuantumPluginPLUMgridV2, self).create_router(context,
                                                                       router)
            router_id = router["id"]
            self._create_vnd(tenant_id)
            # create router on the network controller
            try:
                # Add bridge to VND
                nos_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                router_name = "router_" + router_id[:6]
                body_data = self.snippets.create_router_body_data(
                    tenant_id, router_name)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid NOS communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        # return created router
        return router

    def update_router(self, context, router_id, router):

        LOG.debug(_("QuantumPluginPLUMgrid: update_router() called"))

        with context.session.begin(subtransactions=True):
            new_router = super(QuantumPluginPLUMgridV2,
                               self).update_router(context, router_id, router)
            # Update router in EVO Cube Director
            # No operation needed in EVO Cue director for updating router

        # return updated router
        return new_router

    def delete_router(self, context, router_id):
        LOG.debug(_("QuantumPluginPLUMgrid: delete_router() called"))

        with context.session.begin(subtransactions=True):
            orig_router = self._get_router(context, router_id)
            tenant_id = orig_router["tenant_id"]

            super(QuantumPluginPLUMgridV2, self).delete_router(context, router_id)

        # delete from network ctrl. Remote error on delete is ignored
        try:
            router_name = "router_" + router_id[:6]
            nos_url = self.snippets.BASE_NOS_URL + tenant_id + "/ne/" + router_name
            body_data = {}
            self.rest_conn.nos_rest_conn(nos_url,
                                         'DELETE', body_data)

        except:
            err_message = _("PLUMgrid NOS communication failed: ")
            LOG.Exception(err_message)
            raise plum_excep.PLUMgridException(err_message)

    def add_router_interface(self, context, router_id, interface_info):

        LOG.debug(_("QuantumPluginPLUMgrid: add_router_interface() called"))

        # Validate args
        router = self._get_router(context, router_id)
        tenant_id = router['tenant_id']

        # create interface in DB
        int_router = super(QuantumPluginPLUMgridV2,
                                   self).add_router_interface(context,
                                                              router_id,
                                                              interface_info)
        port = self._get_port(context, int_router['port_id'])
        net_id = port['network_id']
        interface_ip = port['fixed_ips'][0]['ip_address']


        # create interface on the network controller
        try:
            bridge_name = "bridge_" + net_id[:6]
            router_name = "router_" + router_id[:6]

            # Create interface at router
            nos_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
            nos_url = nos_url + "/ifc/" + net_id[:6]
            body_data = { "ifc_type": "static", "ip_address": interface_ip,
                          "ip_address_mask": "255.255.255.0"}
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

            #Create interface at Bridge
            nos_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
            nos_url = nos_url + "/ifc/" + router_id[:6]
            body_data = { "ifc_type": "static"}
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

            # Create link between bridge - router
            nos_url = self.snippets.create_link_url(tenant_id, net_id, router_id)
            body_data = self.snippets.create_link_body_data(
                bridge_name, router_name, router_id, net_id)
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

        except:
            err_message = _("PLUMgrid NOS communication failed: ")
            LOG.Exception(err_message)
            raise plum_excep.PLUMgridException(err_message)

        return int_router

    def remove_router_interface(self, context, router_id, interface_info):

        LOG.debug(_("QuantumPluginPLUMgrid: remove_router_interface() called"))

        router = self._get_router(context, router_id)
        tenant_id = router['tenant_id']
        port = self._get_port(context, interface_info['port_id'])
        net_id = port['network_id']

        # remove router in DB
        del_int_router = super(QuantumPluginPLUMgridV2,
                              self).remove_router_interface(context,
                                                            router_id,
                                                            interface_info)
        try:
            # Delete Link
            body_data = {}
            nos_url = self.snippets.create_link_url(tenant_id, net_id, router_id)
            self.rest_conn.nos_rest_conn(nos_url,
                                         'DELETE', body_data)

            # Delete Interface Bridge
            nos_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
            nos_url = nos_url + "/ifc/" + router_id[:6]
            self.rest_conn.nos_rest_conn(nos_url,
                                         'DELETE', body_data)

            # Delete Interface Router
            nos_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
            nos_url = nos_url + "/ifc/" + net_id[:6]
            self.rest_conn.nos_rest_conn(nos_url,
                                         'DELETE', body_data)

        except:
            err_message = _("PLUMgrid NOS communication failed: ")
            LOG.Exception(err_message)
            raise plum_excep.PLUMgridException(err_message)

        return del_int_router
    
    """
    Extension API implementation
    """
    # TODO: (Edgar) Complete extensions for PLUMgrid

    """
    Internal PLUMgrid fuctions
    """

    def _get_plugin_version(self):
        return VERSION

    def _create_vnd(self, tenant_id):

        # Verify VND (Tenant_ID) does not exist in Director
        nos_url = self.snippets.BASE_NOS_URL + tenant_id
        body_data = {}
        resp = self.rest_conn.nos_rest_conn(nos_url,
                                            'GET', body_data)
        resp_dict = json.loads(resp[2])
        if not tenant_id in resp_dict.values():
            LOG.debug(_('Creating VND for Tenant: %s'), tenant_id)
            nos_url = self.snippets.TENANT_NOS_URL + tenant_id
            body_data = self.snippets.create_tenant_domain_body_data(tenant_id)
            tenant_data = body_data
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

            nos_url = self.snippets.BASE_NOS_URL + tenant_id
            body_data = self.snippets.create_domain_body_data(tenant_id)
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

            # PLUMgrid creates Domain Rules
            LOG.debug(_('Creating Rule for Tenant: %s'), tenant_id)
            nos_url = self.snippets.create_rule_cm_url(tenant_id)
            body_data = self.snippets.create_rule_cm_body_data(tenant_id)
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

            # PLUMgrid creates Domain Rules
            nos_url = self.snippets.create_rule_url(tenant_id)
            body_data = self.snippets.create_rule_body_data(tenant_id)
            self.rest_conn.nos_rest_conn(nos_url,
                                         'PUT', body_data)

    def _get_json_data(self, tenant_id, json_path):
        nos_url = self.snippets.BASE_NOS_URL + tenant_id + json_path
        body_data = {}
        json_data = self.rest_conn.nos_rest_conn(nos_url,
                                                    'GET', body_data)
        return json.loads(json_data[2])

    def _cleaning_nos_subnet_structure(self, body_data, net_id):
        domain_structure = ['/properties', '/link', '/ne']
        for structure in domain_structure:
            nos_url = self.snippets.BASE_NOS_URL + net_id + structure
            self.rest_conn.nos_rest_conn(nos_url, 'DELETE', body_data)

    def _port_viftype_binding(self, context, port):
        if self._check_view_auth(context, port, self.binding_view):
            port[portbindings.VIF_TYPE] = portbindings.VIF_TYPE_OTHER
        return port

    def _check_view_auth(self, context, resource, action):
        return policy.check(context, action, resource)

    def _network_admin_state(self, network):
        try:
            if network["network"].get("admin_state_up"):
                network_name = network["network"]["name"]
                if network["network"]["admin_state_up"] is False:
                    LOG.warning(_("Network with admin_state_up=False are not "
                                  "supported yet by this plugin. Ignoring "
                                  "setting for network %s"), network_name)
        except:
            err_message = _("Network Admin State Validation Falied: ")
            LOG.Exception(err_message)
            raise plum_excep.PLUMgridException(err_message)
        return network

    def _get_dhcp_ip (self, cidr):
        dhcp_ip = re.split('(.*)\.(.*)\.(.*)\.(.*)/(.*)', cidr)
        dhcp_ip[4] = "1"
        dhcp_ip = dhcp_ip[1:-2]
        return '.'.join(dhcp_ip)

    def _get_mask_from_subnet(self, mask):
        bits = 0
        mask_int = int(mask)
        for i in xrange(32-mask_int,32):
            bits |= (1 << i)
        return "%d.%d.%d.%d" % ((bits & 0xff000000) >> 24, (bits & 0xff0000) >> 16, (bits & 0xff00) >> 8 , (bits & 0xff))

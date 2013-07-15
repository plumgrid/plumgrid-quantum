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
Quantum PLUMgrid Platform Plugin for Virtual Networking Infrastructure
PLUMgrid plugin will forward authenticated REST API calls from Quantum to
PLUMgrid Director.
"""


from oslo.config import cfg
import json
import netaddr
import re

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


director_server_opts = [
    cfg.StrOpt('director_server', default='localhost',
               help=_("PLUMgrid Director server to connect to")),
    cfg.StrOpt('director_server_port', default='8080',
               help=_("PLUMgrid Director server port to connect to")),
    cfg.StrOpt('username', default='username',
               help=_("PLUMgrid Director admin username")),
    cfg.StrOpt('password', default='password', secret=True,
               help=_("PLUMgrid Director admin password")),
    cfg.IntOpt('servertimeout', default=5,
               help=_("PLUMgrid Director server timeout")),]


cfg.CONF.register_opts(director_server_opts, "PLUMgridDirector")


class QuantumPluginPLUMgridV2(db_base_plugin_v2.QuantumDbPluginV2,
                              l3_db.L3_NAT_db_mixin):

    supported_extension_aliases = ["router", "binding"]

    binding_view = "extension:port_binding:view"
    binding_set = "extension:port_binding:set"

    def __init__(self):
        LOG.info(_('QuantumPluginPLUMgrid Status: Starting Plugin'))

        # PLUMgrid Director configuration
        director_plumgrid = cfg.CONF.PLUMgridDirector.director_server
        director_port = cfg.CONF.PLUMgridDirector.director_server_port
        timeout = cfg.CONF.PLUMgridDirector.servertimeout
        self.snippets = plumgrid_nos_snippets.DataDirectorPLUMgrid()

        # TODO: (Edgar) These are placeholders for next PLUMgrid release
        director_username = cfg.CONF.PLUMgridDirector.username
        director_password = cfg.CONF.PLUMgridDirector.password
        self.rest_conn = rest_connection.RestConnection(director_plumgrid,
                                                        director_port, timeout)
        if self.rest_conn is None:
            raise SystemExit(_('QuantumPluginPLUMgrid Status: '
                               'Aborting Plugin'))

        else:
            # Plugin DB initialization
            db.configure_db()

            # PLUMgrid Director info validation
            LOG.info(_('QuantumPluginPLUMgrid Director: %s'), director_plumgrid)
            if not director_plumgrid:
                raise SystemExit(_('QuantumPluginPLUMgrid Status: '
                                   'Director value is missing in config file'))

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
            # Propagate all L3 data into DB
            self._process_l3_create(context, network['network'], net['id'])
            self._extend_network_dict_l3(context, net)
            net_id = net["id"]

            try:
                LOG.debug(_('QuantumPluginPLUMgrid Status: %s, %s, %s'),
                          tenant_id, network["network"], net["id"])

                # Set rules for external connectivity
                if net['router:external']:
                    phy_mac_address = self._set_rules(tenant_id)
                    self._create_vnd(tenant_id, True)

                    # Insert Gateway Connector
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "gateway")
                    gateway_name = "gateway_" + net_id[:6]
                    body_data = self.snippets.create_gateway_body_data(
                        tenant_id, gateway_name)
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Add Rule Port Connector
                    # Add physical classification rule
                    director_url = self.snippets.BASE_Director_URL + tenant_id + "/properties/rule_group/PortConnectorRule"
                    body_data = self.snippets.network_level_rule_body_data(
                            tenant_id, gateway_name, "fab_ifc_mac", phy_mac_address)
                    self.rest_conn.director_rest_conn(director_url,
                                                     'PUT', body_data)

                else:
                    # Create VND
                    self._create_vnd(tenant_id, False)

                # Add bridge to VND
                director_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                bridge_name = "bridge_" + net_id[:6]
                body_data = self.snippets.create_bridge_body_data(
                    tenant_id, bridge_name)
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

                # Add classification rule
                # Add bridge to VND
                director_url = self.snippets.BASE_Director_URL + tenant_id + "/properties/rule_group/" + net_id[:6]
                body_data = self.snippets.network_level_rule_body_data(
                    tenant_id, bridge_name, "pgtag2", net_id)
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed")
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
        net = super(QuantumPluginPLUMgridV2, self).get_network(context, net_id)
        net_extended = self._extend_network_dict_l3(context, net)
        tenant_id = net["tenant_id"]


        with context.session.begin(subtransactions=True):
            # Plugin DB - Network Delete
            super(QuantumPluginPLUMgridV2,
                                self).delete_network(context, net_id)
            try:
                if net['router:external']:
                    # Delete Gateway Connector
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "gateway")
                    body_data = {}
                    self.rest_conn.director_rest_conn(director_url,
                                                 'DELETE', body_data)

                    director_url = self.snippets.BASE_Director_URL + tenant_id + "/properties/rule_group/PortConnectorRule"
                    self.rest_conn.director_rest_conn(director_url,
                                                     'DELETE', body_data)


                bridge_name = "bridge_" + net_id[:6]
                body_data = {}
                director_url = self.snippets.BASE_Director_URL + tenant_id + "/ne/" + bridge_name
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)
                director_url = self.snippets.BASE_Director_URL + tenant_id + "/properties/rule_group/" + net_id[:6]
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)


    def get_network(self, context, id, fields=None):
        session = context.session
        with session.begin(subtransactions=True):
            net = super(QuantumPluginPLUMgridV2, self).get_network(context,
                                                              id, None)
            self._extend_network_dict_l3(context, net)
        return self._fields(net, fields)

    def get_networks(self, context, filters=None, fields=None,
                     sorts=None,
                     limit=None, marker=None, page_reverse=False):
        session = context.session
        with session.begin(subtransactions=True):
            nets = super(QuantumPluginPLUMgridV2,
                         self).get_networks(context, filters, None, sorts,
                                            limit, marker, page_reverse)

            for net in nets:
                self._extend_network_dict_l3(context, net)

        return [self._fields(net, fields) for net in nets]


    def create_port(self, context, port):
        """
        Create port core Quantum API
        """
        LOG.debug(_("QuantumPluginPLUMgrid Status: create_port() called"))
        port["port"]["admin_state_up"] = True

        with context.session.begin(subtransactions=True):
            # Plugin DB - Port Create and Return port
            q_port = super(QuantumPluginPLUMgridV2, self).create_port(context,
                                                                  port)

            try:
                if q_port["device_owner"] == "network:router_gateway":
                    LOG.debug(_("QuantumPluginPLUMgrid Status: Configuring Interface in Router"))
                    # Create interface at Router
                    router_id = q_port["device_id"]
                    router_db =  self._get_router(context, router_id)
                    tenant_id = router_db["tenant_id"]
                    interface_ip = q_port["fixed_ips"][0]["ip_address"]
                    director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                    director_url = director_url + "/ifc/GatewayExt"
                    subnet_id = q_port["fixed_ips"][0]["subnet_id"]
                    subnet = super(QuantumPluginPLUMgridV2, self)._get_subnet(context, subnet_id)
                    netmask = self.snippets.EXTERNALGW
                    body_data = { "ifc_type": "static", "ip_address": interface_ip,
                                          "ip_address_mask": netmask}
                    self.rest_conn.director_rest_conn(director_url,
                                                         'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)


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

        with context.session.begin(subtransactions=True):
            # Plugin DB - Port Update
            q_port = super(QuantumPluginPLUMgridV2, self).update_port(context, port_id, port)

            try:
                if q_port["device_owner"] == "network:router_gateway":
                    LOG.debug(_("QuantumPluginPLUMgrid Status: Updating Interface in Router"))
                    # Update interface at Router
                    router_id = q_port["device_id"]
                    router_db =  self._get_router(context, router_id)
                    tenant_id = router_db["tenant_id"]
                    interface_ip = q_port["fixed_ips"][0]["ip_address"]
                    director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                    director_url = director_url + "/ifc/GatewayExt"
                    subnet_id = q_port["fixed_ips"][0]["subnet_id"]
                    subnet = super(QuantumPluginPLUMgridV2, self)._get_subnet(context, subnet_id)
                    netmask = self.snippets.EXTERNALGW
                    body_data = { "ifc_type": "static", "ip_address": interface_ip,
                                          "ip_address_mask": netmask}
                    self.rest_conn.director_rest_conn(director_url,
                                                         'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return self._port_viftype_binding(context, q_port)

    def delete_port(self, context, port_id, l3_port_check=True):
        """
        Delete port core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgrid Status: delete_port() called"))

        with context.session.begin(subtransactions=True):
            q_port = super(QuantumPluginPLUMgridV2, self).get_port(context, port_id)

            # Plugin DB - Port Delete
            super(QuantumPluginPLUMgridV2, self).delete_port(context, port_id)

            try:
                if q_port["device_owner"] == "network:router_gateway":
                    LOG.debug(_("QuantumPluginPLUMgrid Status: Deleting Interface in Router"))
                    # Delete interface at Router
                    router_id = q_port["device_id"]
                    router_db =  self._get_router(context, router_id)
                    tenant_id = router_db["tenant_id"]
                    director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                    director_url = director_url + "/ifc/GatewayExt"
                    # TODO: (Edgar) Need to get the right Mask for this Subnet
                    body_data = {}
                    self.rest_conn.director_rest_conn(director_url,
                                                         'DELETE', body_data)

                    # Insert Wire Connector
                    director_url = self.snippets.create_ne_url(tenant_id, "EXT", "Wire")
                    self.rest_conn.director_rest_conn(director_url, 'DELETE', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)


    def create_subnet(self, context, subnet):
        """
        Create subnet core Quantum API
        """

        LOG.debug(_("QuantumPluginPLUMgrid Status: create_subnet() called"))
        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Create
            net = super(QuantumPluginPLUMgridV2, self).get_network(
                context, subnet['subnet']['network_id'], fields=None)

            subnet = super(QuantumPluginPLUMgridV2, self).create_subnet(
                context, subnet)

            subnet_details = self._get_subnet(context, subnet["id"])
            net_id = subnet_details["network_id"]
            tenant_id = subnet_details["tenant_id"]
            subnet_id = subnet["id"]

            self._extend_network_dict_l3(context, net)

            try:
                if subnet['ip_version'] == 6:
                    raise q_exc.NotImplementedError(
                        _("PLUMgrid doesn't support IPv6."))

                if net['router:external']:
                    ip_range_dict = subnet['allocation_pools']
                    ip_range_start = ip_range_dict[0].get('start')
                    ip_range_end = ip_range_dict[0].get('end')
                    self._update_nat_ip_pool(tenant_id, ip_range_start, ip_range_end)

                    # Insert NAT element
                    director_url = self.snippets.BASE_Director_URL + tenant_id + "/ne/GW_NAT_1-" + net_id[:6]
                    nat_name = "GW_NAT_1-" + net_id[:6]
                    bridge_name = "bridge_" + net_id[:6]
                    body_data = self.snippets.create_nat_body_data(
                    tenant_id, nat_name)
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    #Create interface at Bridge
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                    director_url = director_url + "/ifc/" + subnet_id[:6]
                    body_data = { "ifc_type": "static"}
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Create link between Bridge - NAT
                    director_url = self.snippets.create_link_url(tenant_id, subnet_id, nat_name)
                    body_data = self.snippets.create_gen_link_body_data(bridge_name, nat_name,
                                                                        subnet_id[:6], "outside")
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Insert Wire Connector
                    director_url = self.snippets.create_ne_url(tenant_id, "EXT", "Wire")
                    wire_name = "Wire_EXT"
                    body_data = self.snippets.create_wire_body_data(
                        tenant_id, wire_name)
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Link between Wire and NAT
                    director_url = self.snippets.create_link_url(tenant_id, subnet_id, wire_name)
                    body_data = self.snippets.create_gen_link_body_data(wire_name, nat_name,
                                                                        "ingress", "inside")
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Create interface at Bridge
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                    director_url = director_url + "/ifc/GatewayConnector"
                    body_data = { "ifc_type": "static"}
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Link Gateway Connector with External Bridge
                    director_url = self.snippets.create_link_url(tenant_id, subnet_id, bridge_name)
                    gateway_name = "gateway_" + net_id[:6]
                    body_data = self.snippets.create_gen_link_body_data(bridge_name, gateway_name,
                                                                        "GatewayConnector", "ExtPort")
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)


                if subnet['enable_dhcp'] == True:
                    # Add DHCP to VND
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
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
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Create link between bridge - dhcp
                    director_url = self.snippets.create_link_url(tenant_id, net_id)
                    body_data = self.snippets.create_link_body_data(
                        bridge_name, dhcp_name)
                    self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

                    # Add dhcp with values to VND
                    director_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
                    ipnet = netaddr.IPNetwork(subnet['cidr'])
                    dhcp_server_ip = str(ipnet.ip)
                    dhcp_server_mask = str(ipnet.netmask)

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
                    self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

                elif subnet['enable_dhcp'] == False:
                    LOG.debug(_("DHCP has NOT been deployed"))

            except:
                err_message = _("PLUMgrid Director communication failed: ")
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
            net_extended = self.get_network(context, net_id)




            try:

                if net_extended['router:external']:

                    # Insert NAT element
                    director_url = self.snippets.BASE_Director_URL + tenant_id + "/ne/GW_NAT_1-" + net_id[:6]
                    body_data = {}
                    self.rest_conn.director_rest_conn(director_url,
                                                 'DELETE', body_data)


                    # Delete Wire Connector
                    director_url = self.snippets.create_ne_url(tenant_id, "EXT", "Wire")
                    self.rest_conn.director_rest_conn(director_url,
                                                 'DELETE', body_data)

                dhcp_name = "dhcp_" + net_id[:6]
                body_data = {}
                director_url = self.snippets.BASE_Director_URL + tenant_id + "/ne/" + dhcp_name
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed: ")
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
                director_url = self.snippets.create_ne_url(tenant_id, net_id, "dhcp")
                ipnet = netaddr.IPNetwork(subnet['cidr'])
                dhcp_server_ip = str(ipnet.ip)
                dhcp_server_mask = str(ipnet.netmask)
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
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed: ")
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
                # Add Router to VND
                director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                router_name = "router_" + router_id[:6]
                body_data = self.snippets.create_router_body_data(
                    tenant_id, router_name)
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed: ")
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
            # Introduce the code to create the wire connector, create the router interface
            # and add teh default route to the user router!!!

            if new_router["external_gateway_info"]:
                tenant_id = new_router["tenant_id"]
                net_id = new_router["external_gateway_info"]["network_id"]

                # Insert Wire Connector
                director_url = self.snippets.create_ne_url(tenant_id, "EXT", "Wire")
                wire_name = "Wire_EXT"
                body_data = self.snippets.create_wire_body_data(tenant_id, wire_name)
                self.rest_conn.director_rest_conn(director_url, 'PUT', body_data)


                # Link between Wire and Router
                router_name = "router_" + router_id[:6]
                director_url = self.snippets.create_link_url(tenant_id, net_id, wire_name)
                body_data = self.snippets.create_gen_link_body_data(wire_name, router_name,
                                                                        "ingress", "GatewayExt")
                self.rest_conn.director_rest_conn(director_url,
                                                 'PUT', body_data)

        # return updated router
        return new_router

    def delete_router(self, context, router_id):
        LOG.debug(_("QuantumPluginPLUMgrid: delete_router() called"))

        with context.session.begin(subtransactions=True):
            orig_router = self._get_router(context, router_id)
            tenant_id = orig_router["tenant_id"]

            super(QuantumPluginPLUMgridV2, self).delete_router(context, router_id)

        try:
            router_name = "router_" + router_id[:6]
            director_url = self.snippets.BASE_Director_URL + tenant_id + "/ne/" + router_name
            body_data = {}
            self.rest_conn.director_rest_conn(director_url,
                                         'DELETE', body_data)

        except:
            err_message = _("PLUMgrid Director communication failed: ")
            LOG.Exception(err_message)
            raise plum_excep.PLUMgridException(err_message)

    def add_router_interface(self, context, router_id, interface_info):

        LOG.debug(_("QuantumPluginPLUMgrid: add_router_interface() called"))
        with context.session.begin(subtransactions=True):

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
                subnet_id = port["fixed_ips"][0]["subnet_id"]
                subnet = super(QuantumPluginPLUMgridV2, self)._get_subnet(context, subnet_id)
                ipnet = netaddr.IPNetwork(subnet['cidr'])

                # Create interface at router
                director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                director_url = director_url + "/ifc/" + net_id[:6]
                body_data = { "ifc_type": "static", "ip_address": interface_ip,
                              "ip_address_mask": str(ipnet.netmask)}
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

                #Create interface at Bridge
                director_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                director_url = director_url + "/ifc/" + router_id[:6]
                body_data = { "ifc_type": "static"}
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

                # Create link between bridge - router
                director_url = self.snippets.create_link_url(tenant_id, net_id, router_id)
                body_data = self.snippets.create_link_body_data(
                    bridge_name, router_name, router_id, net_id)
                self.rest_conn.director_rest_conn(director_url,
                                             'PUT', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return int_router

    def remove_router_interface(self, context, router_id, interface_info):

        LOG.debug(_("QuantumPluginPLUMgrid: remove_router_interface() called"))
        with context.session.begin(subtransactions=True):

            router = self._get_router(context, router_id)
            tenant_id = router['tenant_id']
            if 'port_id' in interface_info:
                port = self._get_port(context, interface_info['port_id'])
                net_id = port['network_id']

            elif 'subnet_id' in interface_info:
                subnet_id = interface_info['subnet_id']
                subnet = self._get_subnet(context, subnet_id)
                net_id = subnet['network_id']

            # remove router in DB
            del_int_router = super(QuantumPluginPLUMgridV2,
                                  self).remove_router_interface(context,
                                                                router_id,
                                                                interface_info)
            try:
                # Delete Link
                body_data = {}
                director_url = self.snippets.create_link_url(tenant_id, net_id, router_id)
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)

                # Delete Interface Bridge
                director_url = self.snippets.create_ne_url(tenant_id, net_id, "bridge")
                director_url = director_url + "/ifc/" + router_id[:6]
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)

                # Delete Interface Router
                director_url = self.snippets.create_ne_url(tenant_id, router_id, "router")
                director_url = director_url + "/ifc/" + net_id[:6]
                self.rest_conn.director_rest_conn(director_url,
                                             'DELETE', body_data)

                # Insert Wire Connector
                director_url = self.snippets.create_ne_url(tenant_id, "EXT", "Wire")
                self.rest_conn.director_rest_conn(director_url, 'DELETE', body_data)

            except:
                err_message = _("PLUMgrid Director communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return del_int_router

    """
    Internal PLUMgrid fuctions
    """

    def _get_plugin_version(self):
        return VERSION

    def _update_nat_ip_pool(self, tenant_id, nat_ip_start, nat_ip_end):
        director_url = self.snippets.TENANT_Director_URL + tenant_id + "/containers/" + tenant_id + "/ip_pools/0"
        #self.rest_conn.director_rest_conn(director_url, 'DELETE', {})
        body_data = self.snippets.update_tenant_domain_body_data(nat_ip_start, nat_ip_end)
        self.rest_conn.director_rest_conn(director_url, 'PUT', body_data)

    def _set_rules(self, tenant_id):
        # Set up Gateway Node(s)
        # Create Logical and Physical Rules for Gateway
        LOG.debug(_('QuantumPluginPLUMgrid Creating PLUMgrid External '
                    'Routing Rules'))
        #self._get_list_engine_nodes()
        #self._get_list_gateway_nodes(self._get_list_engine_nodes())
        phy_mac_address = self._set_phys_interfaces(self._get_list_gateway_nodes(self._get_list_engine_nodes()), tenant_id)
        return phy_mac_address


    def _create_vnd(self, tenant_id, port_connector=False):
        # Verify VND (Tenant_ID) does not exist in Director
        director_url = self.snippets.BASE_Director_URL + tenant_id
        body_data = {}
        resp = self.rest_conn.director_rest_conn(director_url,
                                            'GET', body_data)
        resp_dict = json.loads(resp[2])
        if not tenant_id in resp_dict.values():
            LOG.debug(_('Creating VND for Tenant: %s'), tenant_id)
            director_url = self.snippets.TENANT_Director_URL + tenant_id
            body_data = self.snippets.create_tenant_domain_body_data(tenant_id)
            tenant_data = body_data
            self.rest_conn.director_rest_conn(director_url,
                                         'PUT', body_data)

            if port_connector:
                director_url = self.snippets.TENANT_Director_URL + tenant_id + "/containers/" + tenant_id
                body_data = {"services_enabled": {
                    "DHCP": {"service_type": "DHCP"},
                    "GW_NAT_1": {"service_type": "NAT"},
                    "Gateway": {"service_type": "gateway"}}}
                self.rest_conn.director_rest_conn(director_url,
                                         'PUT', body_data)


            director_url = self.snippets.BASE_Director_URL + tenant_id
            body_data = self.snippets.create_domain_body_data(tenant_id)
            self.rest_conn.director_rest_conn(director_url,
                                         'PUT', body_data)

            # PLUMgrid creates Domain Rules
            LOG.debug(_('Creating Rule for Tenant: %s'), tenant_id)
            director_url = self.snippets.create_rule_cm_url(tenant_id)
            body_data = self.snippets.create_rule_cm_body_data(tenant_id)
            self.rest_conn.director_rest_conn(director_url,
                                         'PUT', body_data)

            # PLUMgrid creates Domain Rules
            director_url = self.snippets.create_rule_url(tenant_id)
            body_data = self.snippets.create_rule_body_data(tenant_id)
            self.rest_conn.director_rest_conn(director_url,
                                         'PUT', body_data)

    def _get_json_data(self, tenant_id, json_path):
        director_url = self.snippets.BASE_Director_URL + tenant_id + json_path
        body_data = {}
        json_data = self.rest_conn.director_rest_conn(director_url,
                                                    'GET', body_data)
        return json.loads(json_data[2])

    def _cleaning_director_subnet_structure(self, body_data, net_id):
        domain_structure = ['/properties', '/link', '/ne']
        for structure in domain_structure:
            director_url = self.snippets.BASE_Director_URL + net_id + structure
            self.rest_conn.director_rest_conn(director_url, 'DELETE', body_data)

    def _port_viftype_binding(self, context, port):
        if self._check_view_auth(context, port, self.binding_view):
            port[portbindings.VIF_TYPE] = portbindings.VIF_TYPE_OTHER
        return port

    def _check_view_auth(self, context, resource, action):
        return policy.check(context, action, resource)


    def _get_list_engine_nodes(self):
        director_url = self.snippets.PEM_MASTER + "/pe"
        body_data = {}
        json_data = self.rest_conn.director_rest_conn(director_url,
                                         'GET', body_data)
        return json.loads(json_data[2])


    def _get_list_gateway_nodes(self, list_engine_nodes):
        list_gateways = []
        for engine_node in list_engine_nodes.keys():
            director_url = self.snippets.BASE + engine_node + "/stats/stats"
            body_data = {}
            json_data = self.rest_conn.director_rest_conn(director_url,
                                         'GET', body_data)
            pems_dicc = json.loads(json_data[2])

            if pems_dicc['role'] == 'gateway':
                list_gateways.append(engine_node)
        return list_gateways


    def _set_phys_interfaces(self, list_gateways, tenant_id):
        for gateway in list_gateways:
            director_url = self.snippets.BASE + gateway + "/ifc"
            body_data = {}
            json_data = self.rest_conn.director_rest_conn(director_url,
                                         'GET', body_data)
            pems_dict = json.loads(json_data[2])
            physical_ints = pems_dict.keys()
        for interface in physical_ints:
            if pems_dict[interface]['ifc_type'] == 'access_phys':
                mac_address_full = pems_dict[interface]['status']
                mac_address = mac_address_full.split()[1][:17]
                #mac_addresses.append(dict_physical)
                self._set_mac_addresses_gateway(interface, mac_address)
                self._create_log_rules(mac_address, tenant_id)
                self._create_phys_rules(mac_address, interface, tenant_id)
        return mac_address


    def _set_mac_addresses_gateway(self, interface, mac_address):
            director_url = self.snippets.PEM_MASTER + "/device/" + mac_address
            body_data = {"device_name": interface,
                         "label": "gateway"}
            self.rest_conn.director_rest_conn(director_url, 'PUT', body_data)

    def _create_log_rules(self, mac_address, tenant_id):
        director_url = self.snippets.PEM_MASTER + "/ifc_rule_logical/" + mac_address
        vm_state = True
        body_data = {"domain_dest": tenant_id,
                     "log_ifc_type": "ACCESS_PHYS",
                     "ignore_vm_state": vm_state,
                     "phy_mac_addr": mac_address}
        self.rest_conn.director_rest_conn(director_url, 'PUT', body_data)

    def _create_phys_rules(self, mac_address, interface, tenant_id):
        director_url = self.snippets.PEM_MASTER + "/ifc_rule_physical/" + mac_address
        body_data = {"domain_dest": tenant_id,
                         "ifc_name": interface,
                         "ifc_type": "ACCESS_PHYS",
                         "mac_addr": mac_address,
                         "pem_owned": 1}
        self.rest_conn.director_rest_conn(director_url, 'PUT', body_data)


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

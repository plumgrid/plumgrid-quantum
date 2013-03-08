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

from quantum.db import api as db
from quantum.db import db_base_plugin_v2
from quantum.db import l3_db
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
               help=_("PLUMgrid NOS server timeout")),
    cfg.IntOpt('topologyname', default='t1',
               help=_("PLUMgrid NOS topology name")), ]


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
        self.topology_name = cfg.CONF.PLUMgridNOS.topologyname
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

            try:
                LOG.debug(_('QuantumPluginPLUMgrid Status: %s, %s, %s'),
                          tenant_id, network["network"], net["id"])
                headers = {}

                # PLUMgrid creates Domain Rules
                nos_url = self.snippets.create_rule_url(net["id"])
                body_data = self.snippets.create_rule_body_data(net["id"])
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)

                # PLUMgrid creates Tenant Domain
                nos_url = self.snippets.TENANT_NOS_URL + net["id"]
                body_data = self.snippets.create_tenant_domain_body_data(net["id"])
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)

                #PLUMgrid creates Network Domain
                nos_url = self.snippets.BASE_NOS_URL + net["id"]
                body_data = self.snippets.create_domain_body_data(net["id"])
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)

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

            try:
                # PLUMgrid Server does not support updating resources yet
                nos_url = self.snippets.BASE_NOS_URL + net_id
                headers = {}
                body_data = {}
                self.rest_conn.nos_rest_conn(nos_url,
                                             'DELETE', body_data, headers)
                nos_url = self.snippets.BASE_NOS_URL + new_network["id"]
                body_data = self.snippets.create_domain_body_data(net_id)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)
            except:
                err_message = _("PLUMgrid NOS communication failed")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

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
            net_deleted = super(QuantumPluginPLUMgridV2,
                                self).delete_network(context, net_id)

            try:
                nos_url = self.snippets.BASE_NOS_URL + net_id
                headers = {}
                body_data = {}
                self.rest_conn.nos_rest_conn(nos_url,
                                             'DELETE', body_data, headers)
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

    def delete_port(self, context, port_id):
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
                nos_url = self.snippets.BASE_NOS_URL + net_id
                headers = {}
                body_data = self.snippets.create_network_body_data(
                    net_id, self.topology_name)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)
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
            try:
                headers = {}
                body_data = {}
                net_id = subnet_details["network_id"]
                self._cleaning_nos_subnet_structure(body_data, headers, net_id)
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
                headers = {}
                body_data = {}
                self._cleaning_nos_subnet_structure(body_data, headers, net_id)
                nos_url = self.snippets.BASE_NOS_URL + net_id
                body_data = self.snippets.create_network_body_data(
                    net_id, self.topology_name)
                self.rest_conn.nos_rest_conn(nos_url,
                                             'PUT', body_data, headers)

            except:
                err_message = _("PLUMgrid NOS communication failed: ")
                LOG.Exception(err_message)
                raise plum_excep.PLUMgridException(err_message)

        return new_subnet

    """
    Extension API implementation
    """
    # TODO: (Edgar) Complete extensions for PLUMgrid

    """
    Internal PLUMgrid fuctions
    """

    def _get_plugin_version(self):
        return VERSION

    def _cleaning_nos_subnet_structure(self, body_data, headers, net_id):
        domain_structure = ['/properties', '/link', '/ne']
        for structure in domain_structure:
            nos_url = self.snippets.BASE_NOS_URL + net_id + structure
            self.rest_conn.nos_rest_conn(nos_url, 'DELETE', body_data, headers)

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

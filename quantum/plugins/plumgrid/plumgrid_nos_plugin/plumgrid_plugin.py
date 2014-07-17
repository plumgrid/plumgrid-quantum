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
# @author: Brenden Blanco, bblanco@plumgrid.com, PLUMgrid, Inc.

"""
Quantum Plug-in for PLUMgrid Virtual Networking Infrastructure (VNI)
This plugin will forward authenticated REST API calls
to the PLUMgrid Network Management System called Director
"""

import netaddr
from oslo.config import cfg
from sqlalchemy.orm import exc as sa_exc

from quantum.api.v2 import attributes
from quantum.db import api as db
from quantum.db import db_base_plugin_v2
from quantum.db import l3_db
from quantum.db import quota_db  # noqa
from quantum.db import securitygroups_db
from quantum.extensions import portbindings
from quantum.extensions import providernet
from quantum.extensions import securitygroup as sec_grp
from quantum.openstack.common import importutils
from quantum.openstack.common import lockutils
from quantum.openstack.common import log as logging
from quantum.plugins.plumgrid.common import exceptions as plum_excep
from quantum.plugins.plumgrid.plumgrid_nos_plugin.plugin_ver import VERSION

LOG = logging.getLogger(__name__)
PLUM_DRIVER = 'quantum.plugins.plumgrid.drivers.plumlib.Plumlib'

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
               help=_("PLUMgrid Director server timeout")), ]

cfg.CONF.register_opts(director_server_opts, "PLUMgridDirector")


class QuantumPluginPLUMgridV2(db_base_plugin_v2.QuantumDbPluginV2,
                              l3_db.L3_NAT_db_mixin,
                              securitygroups_db.SecurityGroupDbMixin):

    supported_extension_aliases = ["binding", "external-net", "provider",
                                   "quotas", "router", "security-group"]

    binding_view = "extension:port_binding:view"
    binding_set = "extension:port_binding:set"

    def __init__(self):
        LOG.info(_('Quantum PLUMgrid Director: Starting Plugin'))

        # Plugin DB initialization
        db.configure_db()

        self.plumgrid_init()

        LOG.debug(_('Quantum PLUMgrid Director: Quantum server with '
                    'PLUMgrid Plugin has started'))

    def plumgrid_init(self):
        """PLUMgrid initialization."""
        director_plumgrid = cfg.CONF.PLUMgridDirector.director_server
        director_port = cfg.CONF.PLUMgridDirector.director_server_port
        director_admin = cfg.CONF.PLUMgridDirector.username
        director_password = cfg.CONF.PLUMgridDirector.password
        timeout = cfg.CONF.PLUMgridDirector.servertimeout

        # PLUMgrid Director info validation
        LOG.info(_('Quantum PLUMgrid Director: %s'), director_plumgrid)
        self._plumlib = importutils.import_object(PLUM_DRIVER)
        self._plumlib.director_conn(director_plumgrid, director_port, timeout,
                                    director_admin, director_password)

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def create_network(self, context, network):
        """Create Quantum network.

        Creates a PLUMgrid-based bridge.
        """

        LOG.debug(_('Quantum PLUMgrid Director: create_network() called'))

        # Plugin DB - Network Create and validation
        tenant_id = self._get_tenant_id_for_create(context,
                                                   network["network"])
        self._network_admin_state(network)

        with context.session.begin(subtransactions=True):
            net_db = super(QuantumPluginPLUMgridV2,
                           self).create_network(context, network)
            # Propagate all L3 data into DB
            self._process_l3_create(context, network['network'], net_db['id'])
            self._extend_network_dict_l3(context, net_db)
            self._ensure_default_security_group(context, tenant_id)

            try:
                LOG.debug(_('PLUMgrid Library: create_network() called'))
                self._plumlib.create_network(tenant_id, net_db, network)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Return created network
        return net_db

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def update_network(self, context, net_id, network):
        """Update Quantum network.

        Updates a PLUMgrid-based bridge.
        """

        LOG.debug(_("Quantum PLUMgrid Director: update_network() called"))
        self._network_admin_state(network)
        tenant_id = self._get_tenant_id_for_create(context, network["network"])

        with context.session.begin(subtransactions=True):
            # Plugin DB - Network Update
            net_db = super(
                QuantumPluginPLUMgridV2, self).update_network(context,
                                                              net_id, network)
            self._extend_network_dict_l3(context, net_db)

            try:
                LOG.debug(_("PLUMgrid Library: update_network() called"))
                self._plumlib.update_network(tenant_id, net_id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Return updated network
        return net_db

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def delete_network(self, context, net_id):
        """Delete Quantum network.

        Deletes a PLUMgrid-based bridge.
        """

        LOG.debug(_("Quantum PLUMgrid Director: delete_network() called"))
        net_db = super(QuantumPluginPLUMgridV2,
                       self).get_network(context, net_id)
        self._extend_network_dict_l3(context, net_db)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Network Delete
            super(QuantumPluginPLUMgridV2, self).delete_network(context,
                                                                net_id)

            try:
                LOG.debug(_("PLUMgrid Library: update_network() called"))
                self._plumlib.delete_network(net_db, net_id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

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

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def create_port(self, context, port):
        """Create Quantum port.

        Creates a PLUMgrid-based port on the specific Virtual Network
        Function (VNF).
        """
        LOG.debug(_("Quantum PLUMgrid Director: create_port() called"))

        # Port operations on PLUMgrid Director is an automatic operation
        # from the VIF driver operations in Nova.
        # It requires admin_state_up to be True

        port["port"]["admin_state_up"] = True
        port_data = port["port"]

        with context.session.begin(subtransactions=True):
            # Plugin DB - Port Create and Return port
            port_db = super(QuantumPluginPLUMgridV2, self).create_port(context,
                                                                       port)

            # Update port security
            port_data.update(port_db)

            self._ensure_default_security_group_on_port(context, port)

            port_data[sec_grp.SECURITYGROUPS] = (
                self._get_security_groups_on_port(context, port))

            self._process_port_create_security_group(
                context, port_db, port_data[sec_grp.SECURITYGROUPS])

            device_id = port_db["device_id"]
            if port_db["device_owner"] == "network:router_gateway":
                router_db = self._get_router(context, device_id)
            else:
                router_db = None

            try:
                LOG.debug(_("PLUMgrid Library: create_port() called"))
                self._plumlib.create_port(port_db, router_db)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Plugin DB - Port Create and Return port
        return self._port_viftype_binding(context, port_db)

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def update_port(self, context, port_id, port):
        """Update Quantum port.

        Updates a PLUMgrid-based port on the specific Virtual Network
        Function (VNF).
        """
        LOG.debug(_("Quantum PLUMgrid Director: update_port() called"))

        with context.session.begin(subtransactions=True):
            # Plugin DB - Port Create and Return port
            port_db = super(QuantumPluginPLUMgridV2, self).update_port(
                context, port_id, port)
            device_id = port_db["device_id"]
            if port_db["device_owner"] == "network:router_gateway":
                router_db = self._get_router(context, device_id)
            else:
                router_db = None
            if (self._check_update_deletes_security_groups(port) or
                    self._check_update_has_security_groups(port)):
                self._delete_port_security_group_bindings(context,
                                                          port_db["id"])
                sg_ids = self._get_security_groups_on_port(context, port)
                self._process_port_create_security_group(context,
                                                         port_db,
                                                         sg_ids)
            try:
                LOG.debug(_("PLUMgrid Library: create_port() called"))
                self._plumlib.update_port(port_db, router_db)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Plugin DB - Port Update
        return self._port_viftype_binding(context, port_db)

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def delete_port(self, context, port_id, l3_port_check=True):
        """Delete Quantum port.

        Deletes a PLUMgrid-based port on the specific Virtual Network
        Function (VNF).
        """

        LOG.debug(_("Quantum PLUMgrid Director: delete_port() called"))

        with context.session.begin(subtransactions=True):
            # Plugin DB - Port Create and Return port
            port_db = super(QuantumPluginPLUMgridV2,
                            self).get_port(context, port_id)
            self.disassociate_floatingips(context, port_id)
            super(QuantumPluginPLUMgridV2, self).delete_port(context, port_id)

            if port_db["device_owner"] == "network:router_gateway":
                device_id = port_db["device_id"]
                router_db = self._get_router(context, device_id)
            else:
                router_db = None
            try:
                LOG.debug(_("PLUMgrid Library: delete_port() called"))
                self._plumlib.delete_port(port_db, router_db)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

    def get_port(self, context, id, fields=None):
        with context.session.begin(subtransactions=True):
            port_db = super(QuantumPluginPLUMgridV2,
                            self).get_port(context, id, fields)

            self._port_viftype_binding(context, port_db)
        return self._fields(port_db, fields)

    def get_ports(self, context, filters=None, fields=None):
        with context.session.begin(subtransactions=True):
            ports_db = super(QuantumPluginPLUMgridV2,
                             self).get_ports(context, filters, fields)
            for port_db in ports_db:
                self._port_viftype_binding(context, port_db)
        return [self._fields(port, fields) for port in ports_db]

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def create_subnet(self, context, subnet):
        """Create Quantum subnet.

        Creates a PLUMgrid-based DHCP and NAT Virtual Network
        Functions (VNFs).
        """

        LOG.debug(_("Quantum PLUMgrid Director: create_subnet() called"))

        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Create
            net_db = super(QuantumPluginPLUMgridV2, self).get_network(
                context, subnet['subnet']['network_id'], fields=None)
            s = subnet['subnet']
            ipnet = netaddr.IPNetwork(s['cidr'])

            # PLUMgrid Director reserves the last IP address for GW
            # when is not defined
            if s['gateway_ip'] is attributes.ATTR_NOT_SPECIFIED:
                gw_ip = str(netaddr.IPAddress(ipnet.last - 1))
                subnet['subnet']['gateway_ip'] = gw_ip

            # PLUMgrid reserves the first IP
            if s['allocation_pools'] == attributes.ATTR_NOT_SPECIFIED:
                allocation_pool = self._allocate_pools_for_subnet(context, s)
                subnet['subnet']['allocation_pools'] = allocation_pool

            sub_db = super(QuantumPluginPLUMgridV2, self).create_subnet(
                context, subnet)
            self._extend_network_dict_l3(context, net_db)

            try:
                LOG.debug(_("PLUMgrid Library: create_subnet() called"))
                self._plumlib.create_subnet(sub_db, net_db, ipnet)
            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return sub_db

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def delete_subnet(self, context, subnet_id):
        """Delete subnet core Quantum API."""

        LOG.debug(_("Quantum PLUMgrid Director: delete_subnet() called"))
        # Collecting subnet info
        sub_db = self._get_subnet(context, subnet_id)
        tenant_id = self._get_tenant_id_for_create(context, subnet_id)
        net_id = sub_db["network_id"]
        net_db = self.get_network(context, net_id)
        self._extend_network_dict_l3(context, net_db)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Delete
            super(QuantumPluginPLUMgridV2, self).delete_subnet(
                context, subnet_id)
            try:
                LOG.debug(_("PLUMgrid Library: delete_subnet() called"))
                self._plumlib.delete_subnet(tenant_id, net_db, net_id)
            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def update_subnet(self, context, subnet_id, subnet):
        """Update subnet core Quantum API."""

        LOG.debug(_("update_subnet() called"))
        # Collecting subnet info
        org_sub_db = self._get_subnet(context, subnet_id)

        with context.session.begin(subtransactions=True):
            # Plugin DB - Subnet Update
            new_sub_db = super(QuantumPluginPLUMgridV2,
                               self).update_subnet(context, subnet_id, subnet)
            ipnet = netaddr.IPNetwork(new_sub_db['cidr'])

            try:
                # PLUMgrid Server does not support updating resources yet
                LOG.debug(_("PLUMgrid Library: update_network() called"))
                self._plumlib.update_subnet(org_sub_db, new_sub_db, ipnet)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return new_sub_db

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def create_router(self, context, router):
        """
        Create router extension Quantum API
        """
        LOG.debug(_("Quantum PLUMgrid Director: create_router() called"))

        tenant_id = self._get_tenant_id_for_create(context, router["router"])

        with context.session.begin(subtransactions=True):

            # Create router in DB
            router_db = super(QuantumPluginPLUMgridV2,
                              self).create_router(context, router)
            # Create router on the network controller
            try:
                # Add Router to VND
                LOG.debug(_("PLUMgrid Library: create_router() called"))
                self._plumlib.create_router(tenant_id, router_db)
            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Return created router
        return router_db

    def update_router(self, context, router_id, router):

        LOG.debug(_("Quantum PLUMgrid Director: update_router() called"))

        with context.session.begin(subtransactions=True):
            router_db = super(QuantumPluginPLUMgridV2,
                              self).update_router(context, router_id, router)
            try:
                LOG.debug(_("PLUMgrid Library: update_router() called"))
                self._plumlib.update_router(router_db, router_id)
            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        # Return updated router
        return router_db

    @lockutils.synchronized('pg_rest', 'plumlib-', external=True)
    def delete_router(self, context, router_id):
        LOG.debug(_("Quantum PLUMgrid Director: delete_router() called"))

        with context.session.begin(subtransactions=True):
            orig_router = self._get_router(context, router_id)
            tenant_id = orig_router["tenant_id"]

            super(QuantumPluginPLUMgridV2, self).delete_router(context,
                                                               router_id)

            try:
                LOG.debug(_("PLUMgrid Library: delete_router() called"))
                self._plumlib.delete_router(tenant_id, router_id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

    def add_router_interface(self, context, router_id, interface_info):

        LOG.debug(_("Quantum PLUMgrid Director: "
                    "add_router_interface() called"))
        with context.session.begin(subtransactions=True):
            # Validate args
            router_db = self._get_router(context, router_id)
            tenant_id = router_db['tenant_id']

            # Create interface in DB
            int_router = super(QuantumPluginPLUMgridV2,
                               self).add_router_interface(context,
                                                          router_id,
                                                          interface_info)
            port_db = self._get_port(context, int_router['port_id'])
            subnet_id = port_db["fixed_ips"][0]["subnet_id"]
            subnet_db = super(QuantumPluginPLUMgridV2,
                              self)._get_subnet(context, subnet_id)
            ipnet = netaddr.IPNetwork(subnet_db['cidr'])

            # Create interface on the network controller
            try:
                LOG.debug(_("PLUMgrid Library: add_router_interface() called"))
                self._plumlib.add_router_interface(tenant_id, router_id,
                                                   port_db, ipnet)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return int_router

    def remove_router_interface(self, context, router_id, int_info):

        LOG.debug(_("Quantum PLUMgrid Director: "
                    "remove_router_interface() called"))
        with context.session.begin(subtransactions=True):
            # Validate args
            router_db = self._get_router(context, router_id)
            tenant_id = router_db['tenant_id']
            if 'port_id' in int_info:
                port = self._get_port(context, int_info['port_id'])
                net_id = port['network_id']

            elif 'subnet_id' in int_info:
                subnet_id = int_info['subnet_id']
                subnet = self._get_subnet(context, subnet_id)
                net_id = subnet['network_id']

            # Remove router in DB
            del_int_router = super(QuantumPluginPLUMgridV2,
                                   self).remove_router_interface(context,
                                                                 router_id,
                                                                 int_info)

            try:
                LOG.debug(_("PLUMgrid Library: "
                            "remove_router_interface() called"))
                self._plumlib.remove_router_interface(tenant_id,
                                                      net_id, router_id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return del_int_router

    def create_floatingip(self, context, floatingip):
        LOG.debug(_("Quantum PLUMgrid Director: create_floatingip() called"))

        with context.session.begin(subtransactions=True):

            floating_ip = super(QuantumPluginPLUMgridV2,
                                self).create_floatingip(context, floatingip)

            try:
                LOG.debug(_("PLUMgrid Library: create_floatingip() called"))
                self._plumlib.create_floatingip(floating_ip)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return floating_ip

    def update_floatingip(self, context, id, floatingip):
        LOG.debug(_("Quantum PLUMgrid Director: update_floatingip() called"))

        with context.session.begin(subtransactions=True):

            floating_ip_orig = super(QuantumPluginPLUMgridV2,
                                    self).get_floatingip(context, id)

            floating_ip = super(QuantumPluginPLUMgridV2,
                                self).update_floatingip(context, id,
                                                        floatingip)

            try:
                LOG.debug(_("PLUMgrid Library: update_floatingip() called"))
                self._plumlib.update_floatingip(floating_ip_orig, floating_ip, id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

        return floating_ip

    def delete_floatingip(self, context, id):
        LOG.debug(_("Quantum PLUMgrid Director: delete_floatingip() called"))

        with context.session.begin(subtransactions=True):

            floating_ip_org = super(QuantumPluginPLUMgridV2,
                                    self).get_floatingip(context, id)

            super(QuantumPluginPLUMgridV2, self).delete_floatingip(context, id)

            try:
                LOG.debug(_("PLUMgrid Library: delete_floatingip() called"))
                self._plumlib.delete_floatingip(floating_ip_org, id)

            except Exception as e:
                LOG.error(e)
                raise plum_excep.PLUMgridException(err_msg=e)

    def disassociate_floatingips(self, context, port_id):
        LOG.debug(_("Quantum PLUMgrid Director: disassociate_floatingips() "
                    "called"))

        try:
            fip_qry = context.session.query(l3_db.FloatingIP)
            floating_ip = fip_qry.filter_by(fixed_port_id=port_id).one()

            LOG.debug(_("PLUMgrid Library: disassociate_floatingips()"
                        " called"))
            self._plumlib.disassociate_floatingips(floating_ip, port_id)

        except sa_exc.NoResultFound:
            pass

        except Exception as err_message:
            raise plum_excep.PLUMgridException(err_msg=err_message)

        super(QuantumPluginPLUMgridV2,
              self).disassociate_floatingips(context, port_id)

    def create_security_group(self, context, security_group, default_sg=False):
        """Create a security group

        Create a new security group, including the default security group
        """
        LOG.debug(_("Quantum PLUMgrid Director: create_security_group()"
                    " called"))

        with context.session.begin(subtransactions=True):

            sg = security_group.get('security_group')

            tenant_id = self._get_tenant_id_for_create(context, sg)
            if not default_sg:
                self._ensure_default_security_group(context, tenant_id)

            sg_db =  super(QuantumPluginPLUMgridV2,
                            self).create_security_group(context, security_group,
                                                        default_sg)
            try:
                LOG.debug(_("PLUMgrid Library: create_security_group()"
                            " called"))
                self._plumlib.create_security_group(sg_db)

            except Exception as err_message:
                raise plum_excep.PLUMgridException(err_msg=err_message)

        return sg_db

    def delete_security_group(self, context, sg_id):
        """Delete a security group

        Delete security group from Quantum and PLUMgrid Platform
        """
        with context.session.begin(subtransactions=True):

            sg = super(QuantumPluginPLUMgridV2, self).get_security_group(
                context, sg_id)
            if not sg:
                raise sec_grp.SecurityGroupNotFound(id=sg_id)

            if sg['name'] == 'default' and not context.is_admin:
                raise sec_grp.SecurityGroupCannotRemoveDefault()

            sec_grp_ip = sg['id']
            filters = {'security_group_id': [sec_grp_ip]}
            if super(QuantumPluginPLUMgridV2, self)._get_port_security_group_bindings(
                context, filters):
                raise sec_grp.SecurityGroupInUse(id=sec_grp_ip)

            sec_db = super(QuantumPluginPLUMgridV2, self).delete_security_group(
                context, sg_id)
            try:
                LOG.debug(_("PLUMgrid Library: delete_security_group()"
                            " called"))
                self._plumlib.delete_security_group(sg)

            except Exception as err_message:
                raise plum_excep.PLUMgridException(err_msg=err_message)

            return sec_db

    def create_security_group_rule(self, context, security_group_rule):
        """Create a security group rule

        Create a security group rule in Quantum and PLUMgrid Platform
        """
        LOG.debug(_("Quantum PLUMgrid Director: create_security_group_rule()"
                    " called"))
        bulk_rule = {'security_group_rules': [security_group_rule]}
        return self.create_security_group_rule_bulk(context, bulk_rule)[0]

    def create_security_group_rule_bulk(self, context, security_group_rule):
        """Create security group rules

        Create security group rules in Quantum and PLUMgrid Platform
        """
        sg_rules = security_group_rule.get('security_group_rules')

        with context.session.begin(subtransactions=True):
            sg_id = self._validate_security_group_rules(
                context, security_group_rule)

            # Check to make sure security group exists
            security_group = super(QuantumPluginPLUMgridV2,
                                   self).get_security_group(context,
                                                            sg_id)

            if not security_group:
                raise sec_grp.SecurityGroupNotFound(id=sg_id)

            # Check for duplicate rules
            self._check_for_duplicate_rules(context, sg_rules)

            sec_db = (super(QuantumPluginPLUMgridV2, self).
                            create_security_group_rule_bulk_native(
                            context, security_group_rule))
            try:
                LOG.debug(_("PLUMgrid Library: create_security_"
                            "group_rule_bulk() called"))
                self._plumlib.create_security_group_rule_bulk(sec_db)

            except Exception as err_message:
                raise plum_excep.PLUMgridException(err_msg=err_message)

            return sec_db

    def delete_security_group_rule(self, context, sgr_id):
        """Delete a security group rule

        Delete a security group rule in Quantum and PLUMgrid Platform
        """

        LOG.debug(_("Quantum PLUMgrid Director: delete_security_group_rule()"
                    " called"))

        with context.session.begin(subtransactions=True):
            sgr = (
                super(QuantumPluginPLUMgridV2, self).get_security_group_rule(
                    context, sgr_id))

            if not sgr:
                raise sec_grp.SecurityGroupRuleNotFound(id=sgr_id)

            sg_id = sgr['security_group_id']
            super(QuantumPluginPLUMgridV2, self).delete_security_group_rule(context,
                                                                       sgr_id)
            try:
                LOG.debug(_("PLUMgrid Library: delete_security_"
                            "group_rule() called"))
                self._plumlib.delete_security_group_rule(sgr)

            except Exception as err_message:
                raise plum_excep.PLUMgridException(err_msg=err_message)

    """
    Internal PLUMgrid Fuctions
    """

    def _get_plugin_version(self):
        return VERSION

    def _port_viftype_binding(self, context, port):
        port[portbindings.VIF_TYPE] = portbindings.VIF_TYPE_OTHER
        port[portbindings.CAPABILITIES] = {
            portbindings.CAP_PORT_FILTER:
            'security-group' in self.supported_extension_aliases}
        return port

    def _network_admin_state(self, network):
        try:
            if network["network"].get("admin_state_up"):
                network_name = network["network"]["name"]
                if network["network"]["admin_state_up"] is False:
                    LOG.warning(_("Network with admin_state_up=False are not "
                                  "supported yet by this plugin. Ignoring "
                                  "setting for network %s"), network_name)
        except Exception:
            err_message = _("Network Admin State Validation Falied: ")
            LOG.error(err_message)
            raise plum_excep.PLUMgridException(err_msg=err_message)
        return network

    def _allocate_pools_for_subnet(self, context, subnet):
        """Create IP allocation pools for a given subnet

        Pools are defined by the 'allocation_pools' attribute,
        a list of dict objects with 'start' and 'end' keys for
        defining the pool range.
        Modified from Quantum DB based class

        """

        pools = []
        # Auto allocate the pool around gateway_ip
        net = netaddr.IPNetwork(subnet['cidr'])
        boundary = int(netaddr.IPAddress(subnet['gateway_ip'] or net.last))
        potential_dhcp_ip = int(net.first + 1)
        if boundary == potential_dhcp_ip:
            first_ip = net.first + 3
            boundary = net.first + 2
        else:
            first_ip = net.first + 2
        last_ip = net.last - 1
        # Use the gw_ip to find a point for splitting allocation pools
        # for this subnet
        split_ip = min(max(boundary, net.first), net.last)
        if split_ip > first_ip:
            pools.append({'start': str(netaddr.IPAddress(first_ip)),
                          'end': str(netaddr.IPAddress(split_ip - 1))})
        if split_ip < last_ip:
            pools.append({'start': str(netaddr.IPAddress(split_ip + 1)),
                          'end': str(netaddr.IPAddress(last_ip))})
            # return auto-generated pools
        # no need to check for their validity
        return pools

    def _validate_security_group_rules(self, context, rules):
        return super(QuantumPluginPLUMgridV2,
                     self)._validate_security_group_rules(context, rules)

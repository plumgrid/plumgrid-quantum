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
# @author: Brenden Blanco, bblanco@plumgrid.com, PLUMgrid, Inc.

"""
Snippets needed by the PLUMgrid Plugin
"""

import random

from quantum.openstack.common import log as logging


LOG = logging.getLogger(__name__)


class DataNOSPLUMgrid():
    BASE_NOS_URL = '/0/connectivity/domain/'
    RULE_NOS_URL = '/0/pem_master/ifc_rule_logical/'
    RULE_NOS_CM_URL = '/0/connectivity/rule/'
    TENANT_NOS_URL = '/0/tenant_manager/tenants/'
    CDB_BASE_URL = '/0/cdb/folder/'

    def __init__(self):
        LOG.info(_('QuantumPluginPLUMgrid Status: NOS Body Data Creation'))

    def create_cdb_domain(self, tenant_id):
        return self.CDB_BASE_URL + tenant_id

    def create_cdb_topology(self, tenant_id):
        return self.CDB_BASE_URL + tenant_id + "/domain/quantum-based"

    def create_rule_url(self, tenant_id):
        return self.RULE_NOS_URL + tenant_id + "_" + tenant_id[:6]

    def create_rule_cm_url(self, tenant_id):
        return self.RULE_NOS_CM_URL + tenant_id[:6]

    def create_rule_cm_body_data(self, tenant_id):
        body_data = {"criteria": "pgtag1",
                     "add_context": "first-level-rule",
                     "match": tenant_id,
                     "domain_dest": "/connectivity/domain/" + tenant_id}
        return body_data

    def create_rule_body_data(self, tenant_id): #USED
        body_data = {"domain_dest": "/connectivity/domain/"
                                    + tenant_id, "pgtag1": tenant_id}
        return body_data

    def network_level_rule_body_data(self, tenant_id, net_id, bridge_name):
        body_data = {"ne_dest": "/connectivity/domain/" + tenant_id + "/ne/"
                                + bridge_name + "/action/action1", "rule": {
            "1": {"add_context": "second-level-rule",
                  "criteria": "pgtag2", "match": net_id}}}

        return body_data


    def create_domain_body_data(self, net_id): #USED
        body_data = {"container_group": net_id,
                     "topology_name": "quantum-based"}
        return body_data


    def create_tenant_domain_body_data(self, tenant_id):
        body_data = {"containers": {
            tenant_id: {"enable": "true",
                        "qos_marking": "9",
                        "type": "Gold",
                        "property": "Container %s Property" % tenant_id,
                        "services_enabled": {
                            "DHCP": {"service_type": "DHCP"}, "GW_NAT_1":
                            {"service_type": "NAT"}}, "domains": {}, "rules": {}}}}
        return body_data

    def create_ne_url(self, tenant_id, ne_id, ne):
        return self.BASE_NOS_URL + tenant_id + "/ne/" + ne + "_" + ne_id[:6]

    def create_link_url(self, tenant_id, prefix_link_id, sufix_link_id=""):
        return self.BASE_NOS_URL + tenant_id + "/link/" + prefix_link_id[:6] + sufix_link_id[:6]

    #TODO: (Edgar) Improve this call to link N number of elements
    def create_link_body_data(self, ne_start, ne_end, ifc_name_ne_start="1", ifc_name_ne_end="1"):
        body_data = {"link_type": "static", "attachment1":
            "/ne/" + ne_start + "/ifc/" + ifc_name_ne_start[:6], "attachment2":
            "/ne/" + ne_end + "/ifc/" + ifc_name_ne_end[:6]}
        return body_data

    def create_bridge_body_data(self, tenant_id, bridge_name):
        body_data = {"ne_type": "bridge", "mobility": "true", "ne_dname": bridge_name, "ifc":
            {"1": {"ifc_type": "static"}}, "action": {"action1": {"action_text": "create_and_link_ifc(DYN_)"}},
                     "container_group": tenant_id, "topology_name": "quantum-based"}
        return body_data

    def create_dhcp_body_data(self, dhcp_name, dhcp_server_ip,
                              dhcp_server_mask, ip_range_start, ip_range_end,
                              dns_ip, default_gateway):

        body_data = {"ne_type": "dhcp", "mobility": "true", "ne_dname": dhcp_name, "ifc":
            {"1": {"ifc_type": "static", "dhcp_server_ip": dhcp_server_ip, "dhcp_server_mask":
                dhcp_server_mask, "ip_range_start": ip_range_start, "ip_range_end":
                       ip_range_end, "dns_ip": dns_ip, "default_gateway": default_gateway}}}
        return body_data

    def create_router_body_data(self, tenant_id, router_name):
        body_data = {"ne_type": "router", "mobility": "true", "ne_dname": router_name,
                     "action": {"action1": {"action_text": "create_and_link_ifc(DYN_)"}},
                     "config": {"0": {"user_mac": self._create_mac()}},
                     "container_group": tenant_id, "topology_name": "quantum-based"}
        return body_data

    def _create_mac(self):
        """
        This function generate unique random mac addresses
        :param base:
        :param cntr:
        :return: Mac address
        """
        mac = [0x00, 0x24, 0x81, random.randint(0x00, 0x7f),
               random.randint(0x00, 0xff),
               random.randint(0x00, 0xff)]
        mac_address = ':'.join(map(lambda x: "%02x" % x, mac))
        return mac_address
    

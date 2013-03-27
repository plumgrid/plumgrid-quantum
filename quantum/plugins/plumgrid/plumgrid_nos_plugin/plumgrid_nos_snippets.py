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

from quantum.openstack.common import log as logging


LOG = logging.getLogger(__name__)


class DataNOSPLUMgrid():
    BASE_NOS_URL = '/0/connectivity/domain/'
    RULE_NOS_URL = '/0/pem_master/ifc_rule_logical/'
    TENANT_NOS_URL = '/0/tenant_manager/tenants/'

    def __init__(self):
        LOG.info(_('QuantumPluginPLUMgrid Status: NOS Body Data Creation'))
        #self.rule_counter_id = 0

    def create_rule_url(self, tenant_id): #USED
        return self.RULE_NOS_URL + "tenant_rule"

    def create_rule_body_data(self, tenant_id): #USED
        #self.rule_counter_id = +1
        body_data = {"domain_dest": "/connectivity/domain/"
                                    + tenant_id, "pgtag1": tenant_id}
        return body_data

    def create_domain_body_data(self, net_id): #USED
        body_data = {"container_group": net_id,
                     "topology_name": "quantum-based"}
        return body_data

    #def create_network_body_data(self, net_id, topology_name):
    #    body_data = {"config_template": "bridge_dhcp",
    #                 "container_group": net_id,
    #                 "topology_name": topology_name}
    #    return body_data

    def create_tenant_domain_body_data(self, tenant_id): #USED
        body_data = {"containers": {
            tenant_id: {"enable": "true",
                     "qos_marking": "9",
                     "type": "Gold",
                     "property": "Test Container %s Property1 Text" % tenant_id,
                     "services_enabled": {
                         "DHCP": {}}, "domains": {}, "rules": {}}}}
        return body_data

    def create_ne_url(self, tenant_id, net_id, ne): #USED
        return self.BASE_NOS_URL + tenant_id + "/ne/" + ne + "_" + net_id[:6]

    def create_bridge_body_data(selfself, tenant_id, bridge_name):
        body_data = {"ne_type": "bridge", "mobility": "true", "ne_dname": bridge_name, "ifc":
            { "1": { "ifc_type": "static" }},"action":{"Action1":
            {"action_text":"create_and_link_ifc(DYN_)"}},
            "container_group": tenant_id,"topology_name":"quantum-based"}
        return body_data

    def create_dhcp_body_data(selfself, tenant_id, dhcp_name, dhcp_server_ip,
                              dhcp_server_mask, ip_range_start, ip_range_end,
                              dns_ip, default_gateway):
        body_data = {"ne_type": "dhcp", "mobility":"true", "ne_dname": dhcp_name, "ifc":
            {"1": {"ifc_type": "static","dhcp_server_ip": dhcp_server_ip,"dhcp_server_mask":
                dhcp_server_mask,"ip_range_start": ip_range_start,"ip_range_end":
                ip_range_end,"dns_ip": dns_ip,"default_gateway": default_gateway}}}
        return body_data

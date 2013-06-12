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
Neutron PLUMgrid Plug-in for PLUMgrid Virtual Technology
This plugin will forward authenticated REST API calls
to the Network Operating System by PLUMgrid called Director
"""

import httplib
import urllib2

from neutron.openstack.common import jsonutils as json
from neutron.openstack.common import log as logging
from neutron.plugins.plumgrid.common import exceptions as plum_excep


LOG = logging.getLogger(__name__)


class RestConnection(object):
    """REST Connection to PLUMgrid Director Server."""

    def __init__(self, server, port, timeout):
        LOG.debug(_('PLUMgrid Director Status: REST Connection Started'))
        self.server = server
        self.port = port
        self.timeout = timeout

    def director_rest_conn(self, director_url, action, data):
        self.director_url = director_url
        body_data = json.dumps(data)
        headers = {}
        headers['Content-type'] = 'application/json'
        headers['Accept'] = 'application/json'

        LOG.debug(_("PLUMgrid_Director_Server: %(server)s %(port)s %(action)s"),
                  dict(server=self.server, port=self.port, action=action))

        conn = httplib.HTTPConnection(self.server, self.port,
                                      timeout=self.timeout)
        if conn is None:
            LOG.error(_('PLUMgrid_Director_Server: Could not establish HTTP '
                        'connection'))
            return

        try:
            LOG.debug(_("PLUMgrid_Director_Server Sending Data: %(director_url)s "
                        "%(body_data)s %(headers)s"),
                      dict(
                          director_url=director_url,
                          body_data=body_data,
                          headers=headers,
                      ))
            conn.request(action, director_url, body_data, headers)
            resp = conn.getresponse()
            resp_str = resp.read()

            LOG.debug(_("PLUMgrid_Director_Server Connection Data: %(resp)s, "
                        "%(resp_str)s"), dict(resp=resp, resp_str=resp_str))

            if resp.status is httplib.OK:
                try:
                    pass
                except ValueError:
                    err_message = _("PLUMgrid HTTP Connection Failed: ")
                    LOG.Exception(err_message)
                    raise plum_excep.PLUMgridException(err_message)

            ret = (resp.status, resp.reason, resp_str)
        except urllib2.HTTPError:
            LOG.error(_('PLUMgrid_Director_Server: %(action)s failure, %(e)r'))
            ret = 0, None, None, None
        conn.close()
        LOG.debug(_("PLUMgrid_Director_Server: status=%(status)d, "
                  "reason=%(reason)r, ret=%(ret)s"),
                  {'status': ret[0], 'reason': ret[1], 'ret': ret[2]})
        return ret

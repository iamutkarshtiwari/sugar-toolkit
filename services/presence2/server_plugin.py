# Copyright (C) 2007, Red Hat, Inc.
# Copyright (C) 2007, Collabora Ltd.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import gobject
import dbus
from sugar import profile
from sugar import util
from sugar import env
import gtk
from buddyiconcache import BuddyIconCache
import logging
import os

from telepathy.client import ConnectionManager, ManagerRegistry, Connection, Channel
from telepathy.interfaces import (
    CONN_MGR_INTERFACE, CONN_INTERFACE, CHANNEL_TYPE_CONTACT_LIST, CHANNEL_INTERFACE_GROUP, CONN_INTERFACE_ALIASING,
    CONN_INTERFACE_AVATARS, CONN_INTERFACE_PRESENCE)
from telepathy.constants import (
    CONNECTION_HANDLE_TYPE_NONE, CONNECTION_HANDLE_TYPE_CONTACT,
    CONNECTION_STATUS_CONNECTED, CONNECTION_STATUS_DISCONNECTED, CONNECTION_STATUS_CONNECTING,
    CONNECTION_HANDLE_TYPE_LIST, CONNECTION_HANDLE_TYPE_CONTACT,
    CONNECTION_STATUS_REASON_AUTHENTICATION_FAILED)

CONN_INTERFACE_BUDDY_INFO = 'org.laptop.Telepathy.BuddyInfo'

_PROTOCOL = "jabber"

class InvalidBuddyError(Exception):
    pass

def _get_buddy_icon_at_size(maxw, maxh, maxsize):
    icon = os.path.join(env.get_profile_path(), "buddy-icon.jpg")
    pixbuf = gtk.gdk.pixbuf_new_from_file_at_size(icon, maxw, maxh)

    obj = {}
    obj['data'] = ""
    quality = 90
    img_size = maxsize + 1
    while img_size > maxsize:
        del obj['data']
        obj['data'] = ""
        pixbuf.save_to_callback(_buddy_icon_save_cb, "jpeg", {"quality":"%d" % quality}, obj)
        quality -= 10
        img_size = len(obj['data'])
    del pixbuf

    if img_size > maxsize:
        del obj['data']
        raise RuntimeError("could not size image less than %d bytes" % maxsize)

    return obj['data']
        
def _buddy_icon_save_cb(buf, obj):
    obj['data'] += buf
    return True


class ServerPlugin(gobject.GObject):
    __gsignals__ = {
        'contact-online':  (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT])),
        'contact-offline': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_PYOBJECT])),
        'status':          (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_INT, gobject.TYPE_INT])),
        'avatar-updated':  (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT])),
        'properties-changed':  (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT])),
        'activities-changed':  (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                             ([gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT]))
    }
    
    def __init__(self, registry):
        gobject.GObject.__init__(self)

        self._icon_cache = BuddyIconCache()

        self._gabble_mgr = registry.GetManager('gabble')
        self._online_contacts = {}  # handle -> jid
        self._account = self._get_account_info()

        self._conn = self._init_connection()

    def _get_account_info(self):
        account_info = {}
                        
        pubkey = profile.get_pubkey()

        server = profile.get_server()
        if not server:
            account_info['server'] = 'olpc.collabora.co.uk'
        else:
            account_info['server'] = server

        registered = profile.get_server_registered()
        account_info['register'] = not registered

        khash = util.printable_hash(util._sha_data(pubkey))
        account_info['account'] = "%s@%s" % (khash, account_info['server'])

        account_info['password'] = profile.get_private_key_hash()
        return account_info

    def _find_existing_connection(self):
        our_name = self._account['account']

        # Search existing connections, if any, that we might be able to use
        connections = Connection.get_connections()
        conn = None
        for item in connections:
            if not item.object_path.startswith("/org/freedesktop/Telepathy/Connection/gabble/jabber/"):
                continue
            if item[CONN_INTERFACE].GetProtocol() != _PROTOCOL:
                continue
            if item[CONN_INTERFACE].GetStatus() == CONNECTION_STATUS_CONNECTED:
                test_handle = item[CONN_INTERFACE].RequestHandles(CONNECTION_HANDLE_TYPE_CONTACT, [our_name])[0]
                if item[CONN_INTERFACE].GetSelfHandle() != test_handle:
                    continue
            return item
        return None

    def _init_connection(self):
        conn = self._find_existing_connection()
        if not conn:
            acct = self._account.copy()

            # Create a new connection
            name, path = self._gabble_mgr[CONN_MGR_INTERFACE].RequestConnection(_PROTOCOL, acct)
            conn = Connection(name, path)
            del acct

        conn[CONN_INTERFACE].connect_to_signal('StatusChanged', self._status_changed_cb)

        # hack
        conn._valid_interfaces.add(CONN_INTERFACE_PRESENCE)
        conn[CONN_INTERFACE_PRESENCE].connect_to_signal('PresenceUpdate',
            self._presence_update_cb)

        return conn

    def _request_list_channel(self, name):
        handle = self._conn[CONN_INTERFACE].RequestHandles(
            CONNECTION_HANDLE_TYPE_LIST, [name])[0]
        chan_path = self._conn[CONN_INTERFACE].RequestChannel(
            CHANNEL_TYPE_CONTACT_LIST, CONNECTION_HANDLE_TYPE_LIST,
            handle, True)
        channel = Channel(self._conn._dbus_object._named_service, chan_path)
        # hack
        channel._valid_interfaces.add(CHANNEL_INTERFACE_GROUP)
        return channel

    def _connected_cb(self):
        if self._account['register']:
            # we successfully register this account
            profile.set_server_registered()

        # the group of contacts who may receive your presence
        publish = self._request_list_channel('publish')
        publish_handles, local_pending, remote_pending = publish[CHANNEL_INTERFACE_GROUP].GetAllMembers()

        # the group of contacts for whom you wish to receive presence
        subscribe = self._request_list_channel('subscribe')
        subscribe_handles = subscribe[CHANNEL_INTERFACE_GROUP].GetMembers()

        if local_pending:
            # accept pending subscriptions
            #print 'pending: %r' % local_pending
            publish[CHANNEL_INTERFACE_GROUP].AddMembers(local_pending, '')

        not_subscribed = list(set(publish_handles) - set(subscribe_handles))
        self_handle = self._conn[CONN_INTERFACE].GetSelfHandle()
        self._online_contacts[self_handle] = self._account['account']

        for handle in not_subscribed:
            # request subscriptions from people subscribed to us if we're not subscribed to them
            subscribe[CHANNEL_INTERFACE_GROUP].AddMembers([self_handle], '')

        # hack
        self._conn._valid_interfaces.add(CONN_INTERFACE_BUDDY_INFO)
        if CONN_INTERFACE_BUDDY_INFO not in self._conn.get_valid_interfaces():
            print 'OLPC information not available'
            self.disconnect()
            return

        self._conn[CONN_INTERFACE_BUDDY_INFO].connect_to_signal('PropertiesChanged', self._properties_changed_cb)
        self._conn[CONN_INTERFACE_BUDDY_INFO].connect_to_signal('ActivitiesChanged', self._activities_changed_cb)

        # hack
        self._conn._valid_interfaces.add(CONN_INTERFACE_AVATARS)
        self._conn[CONN_INTERFACE_AVATARS].connect_to_signal('AvatarUpdated', self._avatar_updated_cb)

        # hack
        self._conn._valid_interfaces.add(CONN_INTERFACE_ALIASING)
        # FIXME: we need to use PEP to store the nick. We aren't notified when
        # vcards are changed
        #self._conn[CONN_INTERFACE_ALIASING].connect_to_signal('AliasesChanged', self._alias_changed_cb)

        self._set_self_buddy_info()

        # Request presence for everyone on the channel
        self._conn[CONN_INTERFACE_PRESENCE].GetPresence(subscribe_handles)

    def _set_self_buddy_info(self):
        # Set our OLPC buddy properties
        props = {}
        props['color'] = profile.get_color().to_string()
        props['key'] = profile.get_pubkey()
        self._conn[CONN_INTERFACE_BUDDY_INFO].SetProperties(props)

        name = profile.get_nick_name()
        self_handle = self._conn[CONN_INTERFACE].GetSelfHandle()
        self._conn[CONN_INTERFACE_ALIASING].SetAliases( {self_handle : name} )

        types, minw, minh, maxw, maxh, maxsize = self._conn[CONN_INTERFACE_AVATARS].GetAvatarRequirements()
        if not "image/jpeg" in types:
            print "server does not accept JPEG format avatars."
            return

        # FIXME: check server avatar token and only upload if we know our
        # buddy icon has changed
        try:
            img_data = _get_buddy_icon_at_size(min(maxw, 96), min(maxh, 96), maxsize)
            self._conn[CONN_INTERFACE_AVATARS].SetAvatar(img_data, "image/jpeg")
        except RuntimeError, e:
            pass

    def _status_changed_cb(self, state, reason):
        if state == CONNECTION_STATUS_CONNECTING:
            print 'connecting: %r' % reason
        elif state == CONNECTION_STATUS_CONNECTED:
            print 'connected: %r' % reason
            self.emit('status', state, int(reason))
            self._connected_cb()
        elif state == CONNECTION_STATUS_DISCONNECTED:
            print 'disconnected: %r' % reason
            self.emit('status', state, int(reason))
            if reason == CONNECTION_STATUS_REASON_AUTHENTICATION_FAILED:
                # FIXME: handle connection failure; retry later?
                pass
        return False

    def start(self):
        # If the connection is already connected query initial contacts
        conn_status = self._conn[CONN_INTERFACE].GetStatus()
        if conn_status == CONNECTION_STATUS_CONNECTED:
            self._connected_cb()
            subscribe = self._request_list_channel('subscribe')
            subscribe_handles = subscribe[CHANNEL_INTERFACE_GROUP].GetMembers()
            self._conn[CONN_INTERFACE_PRESENCE].RequestPresence(subscribe_handles)
        elif conn_status == CONNECTION_STATUS_CONNECTING:
            pass
        else:
            self._conn[CONN_INTERFACE].Connect()

    def disconnect(self):
        self._conn[CONN_INTERFACE].Disconnect()

    def _contact_offline(self, handle):
        self.emit("contact-offline", handle)
        del self._online_contacts[handle]

    def _contact_online(self, handle):
        try:
            props = self._conn[CONN_INTERFACE_BUDDY_INFO].GetProperties(handle)
        except dbus.DBusException, e:
            if str(e).startswith("org.freedesktop.DBus.Error.NoReply"):
                raise InvalidBuddyError("couldn't get properties")
        if not props.has_key('color'):
            raise InvalidBuddyError("no color")
        if not props.has_key('key'):
            raise InvalidBuddyError("no key")

        jid = self._conn[CONN_INTERFACE].InspectHandles(CONNECTION_HANDLE_TYPE_CONTACT, [handle])[0]
        nick = self._conn[CONN_INTERFACE_ALIASING].RequestAliases([handle])[0]
        if not nick:
            raise InvalidBuddyError("no name")
        props['nick'] = nick

        self._online_contacts[handle] = jid
        self.emit("contact-online", handle, props)

    def _presence_update_cb(self, presence):
        for handle in presence:
            timestamp, statuses = presence[handle]
            online = handle in self._online_contacts
            for status, params in statuses.items():
                print "Handle %s now online=%s with status %s" % (handle, online, status)
                if not online and status in ["available", "away", "brb", "busy", "dnd", "xa"]:
                    try:
                        self._contact_online(handle)
                    except InvalidBuddyError, e:
                        print "Not adding %s because %s" % (handle, e)
                elif online and status in ["offline", "invisible"]:
                    self._contact_offline(handle)

    def _avatar_updated_cb(self, handle, new_avatar_token):
        jid = self._online_contacts[handle]
        icon = self._icon_cache.get_icon(jid, new_avatar_token)
        if not icon:
            # cache miss
            avatar, mime_type = self._conn[CONN_INTERFACE_AVATARS].RequestAvatar(handle)
            icon = ''.join(map(chr, avatar))
            self._icon_cache.store_icon(jid, new_avatar_token, icon)

        self.emit("avatar-updated", handle, icon)

    def _alias_changed_cb(self, aliases):
        for handle, alias in aliases:
            nick = self._conn[CONN_INTERFACE_ALIASING].RequestAliases([handle])[0]
            prop = {'nick': nick}
            print "Buddy %s alias changed to %s" % (handle, nick)
            self._properties_changed_cb(handle, prop)

    def _properties_changed_cb(self, contact, properties):
        self.emit("properties-changed", contact, properties)

    def _activities_changed_cb(self, contact, activities):
        self.emit("activities-changed", contact, activities)
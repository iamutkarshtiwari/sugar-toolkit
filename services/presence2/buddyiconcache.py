# Copyright (C) 2007, Red Hat, Inc.
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

import os, time, md5
from sugar import env
from sugar import util

class BuddyIconCache(object):
    """Caches icons on disk and finds them based on md5 hash."""
    def __init__(self):
        ppath = env.get_profile_path()
        self._cachepath = os.path.join(ppath, "cache", "buddy-icons")
        if not os.path.exists(self._cachepath):
            os.makedirs(self._cachepath)

        self._cache = {}

        # Read all cached icons and their sums
        for fname in os.listdir(self._cachepath):
            m = md5.new()
            data = self._get_icon_data(fname)
            if len(data) == 0:
                continue
            m.update(data)
            printable_hash = util.printable_hash(m.digest())
            self._cache[printable_hash] = fname
            del m

    def _get_icon_data(self, fname):
        fd = open(os.path.join(self._cachepath, fname), "r")
        data = fd.read()
        fd.close()
        del fd
        return data

    def get_icon(self, printable_hash):
        if not isinstance(printable_hash, unicode):
            raise RuntimeError("printable_hash must be a unicode string.")
        try:
            fname = self._cache[printable_hash]
            return self._get_icon_data(fname)
        except KeyError:
            pass
        return None

    def add_icon(self, icon_data):
        if len(icon_data) == 0:
            return

        m = md5.new()
        m.update(icon_data)
        printable_hash = util.printable_hash(m.digest())
        if self._cache.has_key(printable_hash):
            del m
            return

        # Write the icon to disk and add an entry to our cache for it
        m.update(time.asctime())
        fname = util.printable_hash(m.digest())
        fd = open(os.path.join(self._cachepath, fname), "w")
        fd.write(icon_data)
        fd.close()
        self._cache[printable_hash] = fname
        del m
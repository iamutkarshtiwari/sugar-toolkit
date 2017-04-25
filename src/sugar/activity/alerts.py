import gtk

from sugar.graphics.alert import Alert
from sugar.graphics import style


class SaveAlert(Alert):
    """
    Creates a alert popup to prompt the user to specify
    a name for the project to be saved.
    """
    __gtype_name__ = 'SaveAsAlert'

    def __init__(self, **kwargs):
        Alert.__init__(self, **kwargs)
        # Name entry box
        self._name_view = gtk.EventBox()
        self._name_view.show()

        # Entry box
        self._name_entry = gtk.Entry()
        halign = gtk.Alignment(0, 0, 1, 0)
        self._hbox.pack_start(halign, True, True, 0)
        halign.add(self._name_view)
        halign.show()

        self._name_view.add(self._name_entry)
        self._name_entry.show()

        halign = gtk.Alignment(0, 0, 0, 0)
        self._buttons_box = gtk.HButtonBox()
        self._buttons_box.set_layout(gtk.BUTTONBOX_END)
        self._buttons_box.set_spacing(style.DEFAULT_SPACING)
        halign.add(self._buttons_box)
        self._hbox.pack_start(halign, False, False, 0)
        halign.show()
        self.show_all()

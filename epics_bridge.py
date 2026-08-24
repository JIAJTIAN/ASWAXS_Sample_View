# epics_bridge.py — Thread-safe EPICS → Qt signal bridge for PV callbacks.
# Debug entry point: check that pvname is being passed correctly in __call__ and conn_cb.
# External deps: none (bridge is generic; callers supply the PV name).

from PyQt6.QtCore import QObject, pyqtSignal


class _PVBridge(QObject):
    changed    = pyqtSignal(str, object)
    conn_state = pyqtSignal(str, bool)   # pvname, connected

    def __call__(self, pvname=None, value=None, **_kw):
        if value is not None:
            self.changed.emit(str(pvname or ""), value)

    def conn_cb(self, pvname=None, conn=True, **_kw):
        self.conn_state.emit(str(pvname or ""), bool(conn))

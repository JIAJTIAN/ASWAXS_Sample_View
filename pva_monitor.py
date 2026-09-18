# pva_monitor.py — PVAccess camera monitor thread for ASWAXS Sample Station.
# Subscribes to an NTNDArray PVA channel and emits frames as numpy arrays.

import threading
import time

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


def _extract_ndarray(value) -> tuple:
    """Return (np.ndarray, "") on success or (None, error_str) on failure.

    Strategy 1: np.asarray(value) — works when p4p wraps NTNDArray as ntndarray.
    Strategy 2: raw p4p Value field access for fallback.
    """
    try:
        arr = np.asarray(value)
        if arr.ndim >= 2 and arr.size > 0:
            return arr.copy(), ""
    except Exception:
        pass

    try:
        dims = value['dimension']
        if not dims or len(dims) < 1:
            return None, f"no dimension field (dims={dims!r})"
        nx = int(dims[0]['size'])
        ny = int(dims[1]['size']) if len(dims) > 1 else 1
        if nx == 0 or ny == 0:
            return None, f"zero dimension nx={nx} ny={ny}"
        data = np.asarray(value['value']).ravel()
        if data.size < nx * ny:
            return None, f"data size {data.size} < expected {nx*ny}"
        return data[: nx * ny].reshape(ny, nx).copy(), ""
    except Exception as exc:
        return None, str(exc)


class PVAMonitorThread(QThread):
    """Background thread that subscribes to a PVA NTNDArray channel and emits frames."""

    frame_ready        = pyqtSignal(object)   # np.ndarray
    connection_changed = pyqtSignal(bool)
    error_occurred     = pyqtSignal(str)

    def __init__(self, pva_channel: str, pva_host: str = "",
                 fps_limit: float = 15.0, parent=None):
        super().__init__(parent)
        self._pva_channel  = pva_channel.strip()
        self._pva_host     = pva_host.strip()
        self._fps_limit    = fps_limit
        self._min_interval = 1.0 / max(fps_limit, 1.0)
        self._stop_evt     = threading.Event()
        self._last_emit    = 0.0

    def run(self):
        import os
        try:
            from p4p.client.thread import Context
        except ImportError:
            self.error_occurred.emit("p4p not installed — pip install p4p")
            return

        conf = {}
        addr = self._pva_host or os.environ.get('EPICS_PVA_ADDR_LIST', '').strip()
        if addr:
            conf['EPICS_PVA_ADDR_LIST']      = addr
            conf['EPICS_PVA_AUTO_ADDR_LIST'] = 'NO'

        ctx = Context('pva', conf=conf) if conf else Context('pva')
        try:
            sub = ctx.monitor(self._pva_channel, self._on_value, notify_disconnect=True)
            try:
                self._stop_evt.wait()
            finally:
                sub.close()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            ctx.close()

    def stop(self):
        self._stop_evt.set()
        if not self.wait(1500):
            self.terminate()
            self.wait(500)

    def _on_value(self, value):
        if value is None or isinstance(value, Exception):
            self.connection_changed.emit(False)
            return
        now = time.monotonic()
        if now - self._last_emit < self._min_interval:
            return
        self._last_emit = now
        arr, err = _extract_ndarray(value)
        if arr is None:
            self.error_occurred.emit(f"Frame decode: {err}")
            return
        self.connection_changed.emit(True)
        self.frame_ready.emit(arr)

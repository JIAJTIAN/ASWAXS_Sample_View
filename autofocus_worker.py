# autofocus_worker.py — Hill-climbing autofocus worker that runs in a background QThread.
# Debug entry point: check _measure() for epics.caget failures or _decode_frame shape errors.
# External deps: CAMERA_PREFIX+ArraySizeX_RBV/ArraySizeY_RBV, IMAGE_PREFIX+ArrayData, Z_MOTOR_PV.

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from frame_decoder import _decode_frame

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import epics
    EPICS_AVAILABLE = True
except Exception:
    EPICS_AVAILABLE = False
    epics = None   # type: ignore[assignment]


class _AutofocusWorker(QObject):
    """
    Hill-climbing autofocus that runs entirely in a background QThread.

    Algorithm: sweep-halving — step Z in one direction, measuring Laplacian
    sharpness after each move.  Reverse + halve step when sharpness drops.
    Stop when step < 0.005 mm, then drive to the best position found.

    Signals
    -------
    progress(msg)   — human-readable status line, emitted each iteration
    focus_val(fp)   — current Laplacian variance, for live display in focus_lbl
    finished(ok, msg) — True/message on success, False/message on error/cancel
    """
    progress  = pyqtSignal(str)
    focus_val = pyqtSignal(float)
    finished  = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @staticmethod
    def _measure(image_prefix: str, width: int, height: int) -> float:
        """Read one frame via caget and return Laplacian variance."""
        data = epics.caget(image_prefix + "ArrayData")
        _, gray = _decode_frame(data, width, height)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @pyqtSlot(str, str, str, float)
    def run(self, camera_prefix: str, image_prefix: str, motor_pv: str, delta_z: float):
        if not EPICS_AVAILABLE:
            self.finished.emit(False, "EPICS not available")
            return
        if not CV2_AVAILABLE:
            self.finished.emit(False, "opencv-python not installed")
            return
        try:
            width  = int(epics.caget(camera_prefix + "ArraySizeX_RBV") or 0)
            height = int(epics.caget(camera_prefix + "ArraySizeY_RBV") or 0)
            if width <= 0 or height <= 0:
                self.finished.emit(False,
                    f"Cannot read image size from {camera_prefix}ArraySizeX/Y_RBV")
                return

            motor = epics.Motor(motor_pv)
            current_pos = motor.get('VAL')
            best_focus  = self._measure(image_prefix, width, height)
            best_pos    = current_pos
            direction   = 1

            self.focus_val.emit(best_focus)
            self.progress.emit(f"Start  pos={current_pos:.3f}  fp={best_focus:.1f}")

            while delta_z > 0.005:
                if self._cancel:
                    motor.move(best_pos, wait=True)
                    self.finished.emit(False, "Cancelled — returned to best position")
                    return

                new_pos = current_pos + direction * delta_z
                motor.move(new_pos, wait=True)
                current_focus = self._measure(image_prefix, width, height)
                self.focus_val.emit(current_focus)

                if current_focus < best_focus:
                    motor.move(current_pos, wait=True)
                    direction *= -1
                    delta_z   /= 2
                    self.progress.emit(
                        f"Reverse  dz={delta_z:.4f}  best={best_focus:.1f}"
                    )
                else:
                    current_pos = new_pos
                    best_focus  = current_focus
                    best_pos    = current_pos
                    self.progress.emit(
                        f"Better  pos={best_pos:.3f}  fp={best_focus:.1f}"
                    )

            if self._cancel:
                motor.move(best_pos, wait=True)
                self.finished.emit(False, "Cancelled — returned to best position")
                return

            motor.move(best_pos, wait=True)
            self.focus_val.emit(best_focus)
            self.finished.emit(True,
                f"Done  pos={best_pos:.3f}  fp={best_focus:.1f}")

        except Exception as e:
            self.finished.emit(False, str(e))

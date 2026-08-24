"""
autofocus.py — standalone ASWAXS autofocus diagnostic script.

The same algorithm is built into sample_station.py as _AutofocusWorker and
runs in-process without subprocess overhead.  Keep this file as a command-line
tool useful for testing autofocus independently of the GUI.

Usage:
    python autofocus.py <camera_prefix> <image_prefix> <motor_pv> <step_mm>

    camera_prefix   e.g. 15PS1:cam1:
    image_prefix    e.g. 15PS1:image1:
    motor_pv        e.g. 15IDD:m7
    step_mm         initial step size in mm, e.g. 0.2

Supported pixel formats (auto-detected from array size / dtype):
    Mono8   uint8  W*H
    Mono16  uint16 W*H   (top-8-bits used for focus metric)
    BGR8    uint8  W*H*3 (NDPluginStdArrays byte order)
    BGRA8   uint8  W*H*4 (alpha discarded)
"""

import sys
import epics
import cv2
import numpy as np


def _decode_gray(raw_value, width: int, height: int) -> np.ndarray:
    """Return a uint8 (H, W) grayscale array from a raw EPICS caget value."""
    if raw_value is None:
        raise RuntimeError("caget returned None")
    arr  = np.asarray(raw_value)
    n_px = width * height
    if n_px <= 0 or arr.size == 0:
        raise RuntimeError(f"Invalid frame: size={arr.size} dims={width}×{height}")

    def _hw(n_pixels):
        if width > 0 and n_pixels % width == 0:
            return n_pixels // width, width
        return height, width

    # Mono16: proper uint16/int16 path
    if arr.dtype in (np.uint16, np.int16) and arr.size >= n_px // 2:
        h, w = _hw(arr.size)
        return (arr.reshape((h, w)).astype(np.uint16) >> 8).astype(np.uint8)

    # All other types: raw byte reinterpretation (matches EPICS DBR_CHAR behavior)
    raw = arr.ravel().view(np.uint8)
    n_ch = raw.size // n_px

    if n_ch == 1:
        h, w = _hw(raw.size)
        return raw.reshape((h, w)).copy()

    if n_ch == 3:
        h, w = _hw(raw.size // 3)
        bgr = raw.reshape((h, w, 3))
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    if n_ch == 4:
        h, w = _hw(raw.size // 4)
        bgra = raw.reshape((h, w, 4))
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2GRAY)

    raise RuntimeError(
        f"Unsupported layout: size={arr.size} dtype={arr.dtype} "
        f"n_ch≈{n_ch} for {width}×{height}"
    )


def _focus_measure(image_prefix: str, width: int, height: int) -> float:
    data = epics.caget(image_prefix + "ArrayData")
    gray = _decode_gray(data, width, height)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def autofocus(camera_prefix: str, image_prefix: str, motor_pv: str,
              delta_z: float):
    """Sweep-halving hill-climb autofocus."""
    width  = int(epics.caget(camera_prefix + "ArraySizeX_RBV") or 0)
    height = int(epics.caget(camera_prefix + "ArraySizeY_RBV") or 0)
    if width <= 0 or height <= 0:
        print(f"ERROR: could not read image size from {camera_prefix}ArraySizeX/Y_RBV")
        sys.exit(1)
    print(f"Image size: {width}×{height}")

    motor = epics.Motor(motor_pv)
    current_pos = motor.get('VAL')
    best_focus  = _focus_measure(image_prefix, width, height)
    best_pos    = current_pos
    direction   = 1
    print(f"Start  pos={current_pos:.3f}  fp={best_focus:.3f}")

    while delta_z > 0.005:
        new_pos = current_pos + direction * delta_z
        motor.move(new_pos, wait=True)
        current_focus = _focus_measure(image_prefix, width, height)

        if current_focus < best_focus:
            motor.move(current_pos, wait=True)
            direction *= -1
            delta_z   /= 2
            print(f"Reverse  dz={delta_z:.4f}  best={best_focus:.3f}")
        else:
            current_pos = new_pos
            best_focus  = current_focus
            best_pos    = current_pos
            print(f"Better  pos={best_pos:.3f}  fp={best_focus:.3f}")

    motor.move(best_pos, wait=True)
    print(f"Autofocus complete.  Final pos={best_pos:.3f}  fp={best_focus:.3f}")


if __name__ == '__main__':
    if len(sys.argv) < 5:
        print("Usage: autofocus.py <camera_prefix> <image_prefix> <motor_pv> <step_mm>")
        print("  e.g. autofocus.py 15PS1:cam1: 15PS1:image1: 15IDD:m7 0.2")
        sys.exit(1)

    camera_prefix = sys.argv[1]
    image_prefix  = sys.argv[2]
    motor_pv      = sys.argv[3]
    delta_z       = float(sys.argv[4])
    autofocus(camera_prefix, image_prefix, motor_pv, delta_z)

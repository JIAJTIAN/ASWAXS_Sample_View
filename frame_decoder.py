# frame_decoder.py — Decodes raw EPICS area-detector ArrayData into RGB/gray numpy arrays.
# Debug entry point: check arr.dtype and arr.size vs width to diagnose wrong channel layout.
# External deps: IMAGE_PREFIX + "ArrayData" (read by callers, not here).

import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


def _decode_frame(raw_value, width: int, height: int = 0):
    """
    Decode a raw EPICS area-detector ArrayData value into (rgb_uint8, gray_uint8).

    Channel layout is detected from raw byte count and width alone — height is
    never required (mirrors sample_view.py which only uses image_width).

    Priority: BGR8 → Mono8 → BGRA8 → Mono16.
    Mono16 is recognised when the source dtype is uint16/int16 and size % width == 0.

    Returns
    -------
    rgb  : np.ndarray  uint8 (H, W, 3)  RGB for pyqtgraph display
    gray : np.ndarray  uint8 (H, W)     luminance for focus metric
    """
    if raw_value is None:
        raise ValueError("raw_value is None")

    arr = np.asarray(raw_value)
    if arr.size == 0 or width <= 0:
        raise ValueError(f"Empty frame or width={width}")

    # ── Mono16 (uint16 / int16 from pyepics) ────────────────────────────────
    if arr.dtype in (np.uint16, np.int16) and arr.size % width == 0:
        h = arr.size // width
        gray16 = arr.reshape((h, width)).astype(np.uint16)
        mn, mx = int(gray16.min()), int(gray16.max())
        if mx > mn:
            gray = np.clip((gray16.astype(np.float32) - mn) * (255.0 / (mx - mn)),
                           0, 255).astype(np.uint8)
        else:
            gray = np.zeros((h, width), dtype=np.uint8)
        if CV2_AVAILABLE:
            rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        else:
            rgb = np.stack([gray, gray, gray], axis=-1)
        return rgb, gray

    # ── Byte-level detection (matches sample_view.py dtype=np.uint8 approach)
    raw = arr.ravel().view(np.uint8)

    # BGR8 — try first (most common for colour cameras, matches sample_view.py)
    if raw.size % (width * 3) == 0:
        h = raw.size // (width * 3)
        bgr = raw.reshape((h, width, 3))
        if CV2_AVAILABLE:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            rgb  = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        else:
            gray = bgr.mean(axis=2).astype(np.uint8)
            rgb  = bgr[:, :, ::-1].copy()
        return rgb, gray

    # Mono8
    if raw.size % width == 0:
        h = raw.size // width
        gray = raw.reshape((h, width)).copy()
        if CV2_AVAILABLE:
            rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        else:
            rgb = np.stack([gray, gray, gray], axis=-1)
        return rgb, gray

    # BGRA8
    if raw.size % (width * 4) == 0:
        h = raw.size // (width * 4)
        bgra = raw.reshape((h, width, 4))
        if CV2_AVAILABLE:
            gray = cv2.cvtColor(bgra, cv2.COLOR_BGRA2GRAY)
            rgb  = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGB)
        else:
            gray = bgra[:, :, :3].mean(axis=2).astype(np.uint8)
            rgb  = bgra[:, :, 2::-1].copy()
        return rgb, gray

    raise ValueError(
        f"Unsupported: size={arr.size} dtype={arr.dtype} width={width}"
    )

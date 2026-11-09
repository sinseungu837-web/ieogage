"""Responsive mouse/touch rectangle selector, using Streamlit's component protocol."""
import base64
from io import BytesIO
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component("box_selector", path=str(Path(__file__).parent / "frontend"))


def select_box(image, *, mode, pending_box, reset_token, key):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return _component(
        image="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        mode=mode, pending_box=pending_box, reset_token=reset_token,
        default=None, key=key,
    )


def validated_box(value, width, height):
    """Normalize reverse drags and bound coordinates to the original image."""
    import math
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    x1, x2 = sorted(max(0, min(width, v)) for v in (value[0], value[2]))
    y1, y2 = sorted(max(0, min(height, v)) for v in (value[1], value[3]))
    return [x1, y1, x2, y2] if x2 - x1 >= 1 and y2 - y1 >= 1 else None

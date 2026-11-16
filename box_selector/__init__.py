"""Responsive mouse/touch rectangle selector, using Streamlit's component protocol."""
import base64
from io import BytesIO
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component("box_selector", path=str(Path(__file__).parent / "frontend"))


def select_box(image, *, mode, pending_box, reset_token, key, max_side=1280):
    """화면용으로 줄인 JPEG 를 보내고(원본 PNG 는 수 MB 라 모바일에서 드래그가 버벅임), 좌표는 원본 기준으로 되돌린다."""
    scale = min(1.0, max_side / max(image.size))
    shown = image if scale == 1 else image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    buffer = BytesIO()
    shown.convert("RGB").save(buffer, format="JPEG", quality=85)
    event = _component(
        image="data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        mode=mode, pending_box=[v * scale for v in pending_box] if pending_box else None,
        reset_token=reset_token, default=None, key=key,
    )
    if isinstance(event, dict) and scale != 1:
        event = dict(event)
        if isinstance(event.get("box"), list):
            event["box"] = [v / scale if isinstance(v, (int, float)) else v for v in event["box"]]
        for k in ("x", "y"):
            if isinstance(event.get(k), (int, float)):
                event[k] = event[k] / scale
    return event


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

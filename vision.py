"""Florence-2-base 탐지 + 카운터 주변 자동 줌인(Zoom-in)으로 소형 커피머신·그라인더 정밀 포착."""
from __future__ import annotations

import os
import time
from unittest.mock import patch

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor
from transformers.dynamic_module_utils import get_imports

MODEL_ID = "microsoft/Florence-2-base"
MAX_SIDE = 1024  # 멀리 있는 작은 그라인더도 선명하게 보이도록 1024 화질 유지

# 묶음 1: 카운터 위 소형·핵심 카페 장비만 집중 탐색 (단어 수를 줄여야 AI가 안 놓침!)
CAFE_MACHINES_PROMPT = (
    "espresso machine. coffee machine. coffee grinder. ice maker. "
    "blender. water dispenser. pos terminal. cash register."
)

# 묶음 2: 카운터·쇼케이스·냉장고 등 중대형 설비 탐색
CAFE_FIXTURES_PROMPT = (
    "counter. display case. refrigerator. freezer. sink. oven. microwave. shelf. cabinet."
)

CAFE_PRIORITY_LABELS = {
    "espresso machine", "coffee machine", "coffee grinder", "ice maker",
    "blender", "water dispenser", "display case", "pastry display",
    "counter", "bar counter", "cash register", "pos terminal", "kiosk",
    "sink", "oven", "microwave", "refrigerator", "freezer",
}

BLOCK_WORDS = [
    "person", "man", "woman", "boy", "girl", "people", "human", "face", "hand", "head",
    "wall", "floor", "ceiling", "window", "door", "pillar", "stairs", "building",
    "sky", "tree", "grass", "text", "shadow", "light", "reflection",
]
MIN_AREA_RATIO = 0.003            # 일반 가구 최소 면적 (0.3%)
MIN_PRIORITY_AREA_RATIO = 0.0005  # 멀리 찍힌 그라인더·커피머신은 0.05% 크기까지 허용!
MAX_BOX_AREA_RATIO = 0.85


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = _get_device()


def _no_flash_attn(filename):
    imports = get_imports(filename)
    if str(filename).endswith("modeling_florence2.py") and "flash_attn" in imports:
        imports.remove("flash_attn")
    return imports


def load_model():
    torch.set_num_threads(os.cpu_count() or 4)
    # MPS에서 간혹 float16이 느려지거나 오류가 나는 경우를 방지하기 위해 안정적인 설정 적용
    dtype = torch.float16 if DEVICE == "cuda" else torch.float32
    with patch("transformers.dynamic_module_utils.get_imports", _no_flash_attn):
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, trust_remote_code=True, torch_dtype=dtype, attn_implementation="eager"
        ).to(DEVICE).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    return model, processor


def prepare(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


@torch.inference_mode()
def _run(model, processor, image, task, text=""):
    dtype = next(model.parameters()).dtype
    inputs = processor(text=task + text, images=image, return_tensors="pt")
    input_ids = inputs["input_ids"].to(DEVICE)
    pixel_values = inputs["pixel_values"].to(DEVICE, dtype=dtype)

    ids = model.generate(
        input_ids=input_ids,
        pixel_values=pixel_values,
        max_new_tokens=512,
        num_beams=1,
        do_sample=False,
    )
    raw = processor.batch_decode(ids, skip_special_tokens=False)[0]
    res = processor.post_process_generation(raw, task=task, image_size=image.size)[task]
    return res.get("labels", []), res.get("bboxes", []), int(ids.shape[1]) >= 512


def _find_work_zones(objs: list[dict], image_size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """카운터나 주방 선반이 발견되면, 그 위쪽(커피머신·그라인더가 놓이는 위치) 영역 좌표를 계산한다."""
    w, h = image_size
    zones = []
    for o in objs:
        lbl = o["label"].lower()
        if any(k in lbl for k in ("counter", "bar", "cabinet", "shelf", "desk")):
            x1, y1, x2, y2 = o["box"]
            bw, bh = x2 - x1, y2 - y1
            if bw * bh < 0.02 * w * h:
                continue
            # 카운터 박스 기준 위쪽으로 크게 확장해서 카운터 위 기계들이 다 들어오게 크롭 영역 설정
            zx1 = max(0, int(x1 - bw * 0.1))
            zy1 = max(0, int(y1 - bh * 1.2))
            zx2 = min(w, int(x2 + bw * 0.1))
            zy2 = min(h, int(y2 + bh * 0.1))
            if (zx2 - zx1) > 100 and (zy2 - zy1) > 100:
                zones.append((zx1, zy1, zx2, zy2))
    # 겹치는 영역 정리 후 가장 큰 카운터 영역 최대 2곳만 반환
    zones.sort(key=lambda z: (z[2] - z[0]) * (z[3] - z[1]), reverse=True)
    return zones[:2]


def detect(model, processor, image: Image.Image, use_grounding: bool = True) -> dict:
    """1) 기본 탐지 -> 2) 분리형 전문장비 탐색 -> 3) 카운터 영역 줌인(Zoom-in) 정밀 탐색."""
    t0 = time.time()
    objs, rejected, truncated = [], [], False

    # Step 1: 전체 사진 기본 물체 탐지 (<OD>)
    labels, boxes, tr = _run(model, processor, image, "<OD>")
    truncated |= tr
    objs += [{"label": l, "box": b, "task": "OD"} for l, b in zip(labels, boxes)]

    if use_grounding:
        # Step 2: 전체 사진에서 설비(카운터 등) & 커피 기계 분리 탐색
        for prompt_text in (CAFE_FIXTURES_PROMPT, CAFE_MACHINES_PROMPT):
            labels, boxes, tr = _run(model, processor, image, "<CAPTION_TO_PHRASE_GROUNDING>", prompt_text)
            truncated |= tr
            for l, b in zip(labels, boxes):
                ok, reason = _fast_check(b, image.size, is_crop=False)
                o = {"label": l, "box": b, "task": "GROUNDING", "check": reason}
                (objs if ok else rejected).append(o)

        # Step 3: 카운터/선반 영역이 잡혔다면, 그 부분만 확대(Zoom-in)해서 커피머신·그라인더 집중 재탐색!
        zones = _find_work_zones(objs, image.size)
        for zx1, zy1, zx2, zy2 in zones:
            crop_img = image.crop((zx1, zy1, zx2, zy2))
            c_labels, c_boxes, _ = _run(
                model, processor, crop_img, "<CAPTION_TO_PHRASE_GROUNDING>", CAFE_MACHINES_PROMPT
            )
            for l, cb in zip(c_labels, c_boxes):
                ok, reason = _fast_check(cb, crop_img.size, is_crop=True)
                # 잘라낸 사진의 좌표를 원래 전체 사진의 좌표로 복원
                full_box = [cb[0] + zx1, cb[1] + zy1, cb[2] + zx1, cb[3] + zy1]
                o = {"label": l, "box": full_box, "task": "ZOOM_IN", "check": f"카운터확대({reason})"}
                (objs if ok else rejected).append(o)

    for i, o in enumerate(objs):
        o["id"] = i
        o["box"] = [round(float(v), 1) for v in o["box"]]
    for o in rejected:
        o["box"] = [round(float(v), 1) for v in o["box"]]
    return {"objects": objs, "rejected": rejected, "seconds": round(time.time() - t0, 1), "truncated": truncated}


def _fast_check(box, image_size: tuple[int, int], is_crop: bool = False) -> tuple[bool, str]:
    """AI 재호출 없이 0.001초 만에 끝나는 박스 유효성 검사."""
    w, h = image_size
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    min_px = 10 if is_crop else 12
    if bw < min_px or bh < min_px:
        return False, "너무 작음"
    max_ratio = 0.70 if is_crop else MAX_BOX_AREA_RATIO
    if (bw * bh) > max_ratio * w * h:
        return False, "전체 배경 박스 제외"
    return True, "고속통과"


def _iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = lambda r: max(0, r[2] - r[0]) * max(0, r[3] - r[1])
    u = area(a) + area(b) - inter
    return inter / u if u else 0.0


def code_filter(objects: list[dict], image_size: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    """1차 필터: 제외 단어, 너무 작은 박스, 중복 제거."""
    w, h = image_size
    kept, dropped = [], []
    for o in objects:
        label = o["label"].lower().strip()
        x1, y1, x2, y2 = o["box"]
        min_ratio = MIN_PRIORITY_AREA_RATIO if label in CAFE_PRIORITY_LABELS else MIN_AREA_RATIO

        if any(word in label.split() for word in BLOCK_WORDS):
            dropped.append({**o, "reason": "제외 목록"})
        elif (x2 - x1) * (y2 - y1) < min_ratio * w * h:
            dropped.append({**o, "reason": "너무 작음"})
        elif any(
            (_iou(k["box"], o["box"]) > 0.6 and k["label"].lower() == label)
            or (_iou(k["box"], o["box"]) > 0.85)
            for k in kept
        ):
            dropped.append({**o, "reason": "중복"})
        else:
            kept.append(o)
    return kept, dropped
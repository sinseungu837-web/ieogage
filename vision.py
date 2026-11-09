"""1단계 물체 탐지. 기본은 RF-DETR nano(빠름), DETECTOR=florence 로 Florence-2-base(카운터 줌인 포함)로 되돌릴 수 있다."""
from __future__ import annotations

import os
import time
from unittest.mock import patch

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import torch
from PIL import Image
# transformers 는 Florence 를 쓸 때만 불러온다 (RF-DETR 과 필요한 버전이 달라서)
from pathlib import Path as _P
from dotenv import load_dotenv as _ld
_ld(_P(__file__).parent / ".env")  # 로컬 .env 의 DETECTOR 설정을 먼저 읽음 (서버는 없음 → rfdetr)
DETECTOR = os.environ.get("DETECTOR", "rfdetr")  # "rfdetr" | "florence"
RFDETR_THRESHOLD = 0.35  # 이 점수 미만 박스는 버림
LOW_CONF = 0.5  # 이 점수 미만은 '애매함' → Gemini 사진 재확인 대상

MODEL_ID = "microsoft/Florence-2-base"
MAX_SIDE = 1024  # 멀리 있는 작은 그라인더도 선명하게 보이도록 1024 화질 유지

# 묶음 1: 카운터 위 소형·핵심 카페 장비만 집중 탐색 (단어 수를 줄여야 AI가 안 놓침!)  [소정]
# (승우 병합 시 추가: card terminal)
CAFE_MACHINES_PROMPT = (
    "espresso machine. coffee machine. coffee grinder. ice maker. "
    "blender. water dispenser. pos terminal. cash register. card terminal."
)

# 묶음 2: 카운터·쇼케이스·냉장고 등 중대형 설비 탐색  [소정]
# (승우 병합 시 추가: showcase refrigerator, under-counter refrigerator)
CAFE_FIXTURES_PROMPT = (
    "counter. display case. showcase refrigerator. under-counter refrigerator. "
    "refrigerator. freezer. sink. oven. microwave. shelf. cabinet."
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
    from transformers.dynamic_module_utils import get_imports
    imports = get_imports(filename)
    if str(filename).endswith("modeling_florence2.py") and "flash_attn" in imports:
        imports.remove("flash_attn")
    return imports


def _load_florence():
    from transformers import AutoModelForCausalLM, AutoProcessor
    torch.set_num_threads(os.cpu_count() or 4)
    # MPS에서 간혹 float16이 느려지거나 오류가 나는 경우를 방지하기 위해 안정적인 설정 적용
    dtype = torch.float16 if DEVICE == "cuda" else torch.float32
    with patch("transformers.dynamic_module_utils.get_imports", _no_flash_attn):
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, trust_remote_code=True, torch_dtype=dtype, attn_implementation="eager"
        ).to(DEVICE).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    return model, processor


# ---------------- RF-DETR nano (기본) ----------------
def load_model():
    """(모델, 프로세서). RF-DETR 은 프로세서가 없어 None."""
    if DETECTOR == "florence":
        return _load_florence()
    from rfdetr import RFDETRNano
    torch.set_num_threads(os.cpu_count() or 4)
    return RFDETRNano(), None


def detect(model, processor, image: Image.Image, use_grounding: bool = False) -> dict:
    if DETECTOR == "florence":
        return _detect_florence(model, processor, image, use_grounding)
    from rfdetr.utilities.class_names import COCO_CLASSES
    t0 = time.time()
    d = model.predict(image, threshold=RFDETR_THRESHOLD)
    objs = []
    for i, (box, cid, score) in enumerate(zip(d.xyxy, d.class_id, d.confidence)):
        objs.append({"id": i, "label": COCO_CLASSES[int(cid)], "box": [round(float(v), 1) for v in box],
                     "task": "RFDETR", "score": round(float(score), 2)})
    return {"objects": objs, "rejected": [], "seconds": round(time.time() - t0, 1), "truncated": False}


def prepare(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


@torch.inference_mode()
def _run(model, processor, image, task, text=""):
    dtype = next(model.parameters()).dtype
    # (승우) 일반 탐지는 물건이 많으면 출력이 길어 잘리므로 1024 유지, 단어 탐색은 512
    max_tokens = 1024 if task == "<OD>" else 512
    inputs = processor(text=task + text, images=image, return_tensors="pt")
    input_ids = inputs["input_ids"].to(DEVICE)
    pixel_values = inputs["pixel_values"].to(DEVICE, dtype=dtype)

    ids = model.generate(
        input_ids=input_ids,
        pixel_values=pixel_values,
        max_new_tokens=max_tokens,
        num_beams=1,
        do_sample=False,
    )
    raw = processor.batch_decode(ids, skip_special_tokens=False)[0]
    res = processor.post_process_generation(raw, task=task, image_size=image.size)[task]
    return res.get("labels", []), res.get("bboxes", []), int(ids.shape[1]) >= max_tokens


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


def _detect_florence(model, processor, image: Image.Image, use_grounding: bool = True) -> dict:
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
                if ok:  # (승우) 전체 사진 단어 탐색은 없는 물건도 억지로 찾는 경향 → 잘라서 다시 확인
                    ok, reason = _verify(model, processor, image, l, b, objs)
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


# (승우) 전체 사진 단어 탐색 결과 재확인용. 색·재질 같은 일반 단어는 넣지 않는다 (꽃 사진 오탐 재발 방지)
SYNONYMS = {
    "espresso machine": ["espresso", "coffee", "machine", "maker", "brewer"],
    "coffee machine": ["coffee", "espresso", "machine", "maker", "brewer", "dispenser"],
    "coffee grinder": ["grinder", "coffee", "mill", "machine"],
    "ice maker": ["ice", "machine", "maker", "freezer"],
    "blender": ["blender", "mixer", "pitcher", "jar"],
    "water dispenser": ["water", "dispenser", "purifier", "machine", "tap"],
    "refrigerator": ["fridge", "refrigerator", "cooler", "freezer"],
    "showcase refrigerator": ["fridge", "refrigerator", "cooler", "display", "showcase", "case"],
    "under-counter refrigerator": ["fridge", "refrigerator", "cooler", "freezer", "drawer"],
    "freezer": ["freezer", "fridge", "refrigerator", "cooler"],
    "display case": ["display", "case", "showcase", "glass", "fridge", "cooler", "bakery", "pastry"],
    "counter": ["counter", "bar", "desk", "island", "kitchen"],
    "cash register": ["register", "pos", "cash", "computer", "screen", "monitor", "terminal", "tablet"],
    "pos terminal": ["pos", "terminal", "register", "screen", "monitor", "computer", "tablet"],
    "card terminal": ["card", "terminal", "pos", "reader", "machine"],
    "oven": ["oven", "stove", "toaster"],
    "microwave": ["microwave", "oven"],
    "sink": ["sink", "faucet", "tap", "basin"],
    "shelf": ["shelf", "shelves", "rack", "bookcase"],
    "cabinet": ["cabinet", "cupboard", "drawer", "storage"],
}


def _verify(model, processor, image, label, box, od_objs):
    """일반 탐지와 같은 물체로 겹치면 통과, 아니면 박스를 잘라 캡션에 품목명(동의어)이 나오는지 확인."""
    clean = label.lower().strip()
    if any(_iou(box, o["box"]) > 0.4 and clean.split()[-1] in o["label"].lower() for o in od_objs):
        return True, "OD 일치"
    x1, y1, x2, y2 = [int(v) for v in box]
    crop = image.crop((x1, y1, x2, y2))
    cap = _caption(model, processor, crop).lower()
    words = SYNONYMS.get(clean, clean.split())
    return any(w in cap for w in words), cap


@torch.inference_mode()
def _caption(model, processor, image):
    inputs = processor(text="<CAPTION>", images=image, return_tensors="pt")
    ids = model.generate(input_ids=inputs["input_ids"].to(DEVICE),
                         pixel_values=inputs["pixel_values"].to(DEVICE, dtype=next(model.parameters()).dtype),
                         max_new_tokens=40, num_beams=1, do_sample=False)
    return processor.batch_decode(ids, skip_special_tokens=True)[0]


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
    kept, more = _drop_contained(kept)
    dropped += more
    _mark_fixed(kept, w, h)
    return kept, dropped


def _family(label: str) -> str:
    """같은 물건 계열로 묶기 위한 대표 단어 (예: 'pendant lamp' -> 'lamp')."""
    words = label.lower().split()
    for fam in FAMILIES:
        if fam in words or fam in label.lower():
            return fam
    return words[-1] if words else label


FAMILIES = ["lamp", "light", "chair", "stool", "table", "vase", "flower", "plant", "refrigerator", "fridge",
            "cabinet", "shelf", "machine", "grinder", "monitor", "sofa"]


def _inside(small, big) -> float:
    """small 박스 면적 중 big 박스 안에 들어간 비율."""
    x1, y1, x2, y2 = max(small[0], big[0]), max(small[1], big[1]), min(small[2], big[2]), min(small[3], big[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = max(1e-6, (small[2] - small[0]) * (small[3] - small[1]))
    return inter / area


def _drop_contained(objs: list[dict]) -> tuple[list[dict], list[dict]]:
    """포함 관계 중복 제거 (IoU 로는 안 잡히는 경우).
    - 큰 박스가 같은 계열 작은 박스를 2개 이상 품으면: 큰 박스는 '묶음 박스' → 큰 박스 제거
    - 1개만 품으면: 같은 물건을 두 번 잡은 것 → 작은 박스 제거
    - 같은 계열끼리 IoU 0.7 이상: 중복 → 나중 것 제거"""
    drop = set()
    for i, big in enumerate(objs):
        inner = [j for j, s in enumerate(objs) if j != i and _family(s["label"]) == _family(big["label"])
                 and _inside(s["box"], big["box"]) >= 0.8 and _area(s["box"]) < _area(big["box"])]
        if len(inner) >= 2:
            drop.add(i)
        elif len(inner) == 1:
            drop.add(inner[0])
    for i, a in enumerate(objs):
        for j in range(i + 1, len(objs)):
            if i not in drop and j not in drop and _family(a["label"]) == _family(objs[j]["label"]) \
                    and _iou(a["box"], objs[j]["box"]) >= 0.7:
                drop.add(j)
    kept = [o for k, o in enumerate(objs) if k not in drop]
    dropped = [{**o, "reason": "포함·겹침 중복"} for k, o in enumerate(objs) if k in drop]
    return kept, dropped


def _area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


FIXED_WORDS = ["counter", "cabinet", "cupboard", "lamp", "light", "chandelier", "shelf", "sink", "bar"]


def _mark_fixed(objs: list[dict], w: int, h: int) -> None:
    """고정 설비 의심 표시 (제거하지 않고 hint 만 붙임 → LLM 이 uncertain 처리, 사용자가 최종 확인).
    - 사진 면적 35% 이상
    - 폭 60% 이상인데 높이가 낮은 가로 띠 (카운터·하부장)
    - 조명·선반 계열이 사진 위쪽 5% 에 닿음 (천장·벽에 매달림)"""
    for o in objs:
        x1, y1, x2, y2 = o["box"]
        bw, bh = x2 - x1, y2 - y1
        label = o["label"].lower()
        big = bw * bh >= 0.35 * w * h
        band = bw >= 0.6 * w and bh <= 0.45 * h and any(f in label for f in FIXED_WORDS)
        hanging = y1 <= 0.05 * h and any(f in label for f in ["lamp", "light", "chandelier", "shelf"])
        if big or band or hanging:
            o["hint"] = "fixed_suspect"

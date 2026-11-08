"""Florence-2-base 탐지 + 코드 1차 필터 (카페 전문장비·카운터 인식 강화 버전)."""
from __future__ import annotations

import os
import time
from unittest.mock import patch

os.environ.setdefault("USE_TF", "0")  # transformers 가 쓰지 않는 TensorFlow 를 불러오지 않게 (기동 시간 단축)
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor
from transformers.dynamic_module_utils import get_imports

MODEL_ID = "microsoft/Florence-2-base"
MAX_SIDE = 1024  # 큰 사진은 줄여서 CPU 시간 단축

# 1. 카페 핵심 장비 & 카운터 위주로 단어장 개편 (중요도 순 배치)
VOCAB = [
    # 커피·음료 제조 핵심 장비
    "espresso machine", "coffee machine", "coffee grinder", "ice maker",
    "blender", "water dispenser",
    # 냉장·보관·진열·조리 장비
    "display case", "pastry display", "refrigerator", "freezer",
    "oven", "microwave", "dishwasher", "sink",
    # 카운터·결제·매장 설비
    "counter", "bar counter", "cash register", "pos terminal", "kiosk", "monitor",
    # 홀 가구·냉난방·기타 집기
    "table", "chair", "stool", "sofa", "shelf", "cabinet",
    "air conditioner", "fan", "lamp", "mirror", "television",
    # (승우 추가) 매립형 냉장고·쇼케이스 냉장고·카드 단말기
    "under-counter refrigerator", "showcase refrigerator", "card terminal",
]

# 카페 핵심 장비 목록 (검증 완화 및 소형 크기 보호 대상)
CAFE_PRIORITY_LABELS = {
    "espresso machine", "coffee machine", "coffee grinder", "ice maker",
    "blender", "water dispenser", "display case", "pastry display",
    "counter", "bar counter", "cash register", "pos terminal", "kiosk",
    "sink", "oven", "microwave", "refrigerator", "freezer",
}

# 코드 1차 제외 목록: 사람·건물 구조·배경. 대표 단어만 두고 나머지는 LLM 이 2차로 거른다.
BLOCK_WORDS = [
    "person", "man", "woman", "boy", "girl", "people", "human", "face", "hand", "head",
    "wall", "floor", "ceiling", "window", "door", "pillar", "stairs", "building",
    "sky", "tree", "grass", "text", "shadow", "light", "reflection",
]
MIN_AREA_RATIO = 0.003          # 일반 물체 최소 면적 비율 (0.3%)
MIN_PRIORITY_AREA_RATIO = 0.0012  # 그라인더·블렌더·포스기 등 소형 핵심 장비 최소 면적 비율 (0.12%)


def _no_flash_attn(filename):
    imports = get_imports(filename)
    if str(filename).endswith("modeling_florence2.py") and "flash_attn" in imports:
        imports.remove("flash_attn")
    return imports


def load_model():
    torch.set_num_threads(os.cpu_count() or 4)
    with patch("transformers.dynamic_module_utils.get_imports", _no_flash_attn):
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, trust_remote_code=True, torch_dtype=torch.float32, attn_implementation="eager"
        ).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    return model, processor


def prepare(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


@torch.inference_mode()
def _run(model, processor, image, task, text=""):
    inputs = processor(text=task + text, images=image, return_tensors="pt")
    ids = model.generate(input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                         max_new_tokens=1024, num_beams=3, do_sample=False)
    raw = processor.batch_decode(ids, skip_special_tokens=False)[0]
    res = processor.post_process_generation(raw, task=task, image_size=image.size)[task]
    return res.get("labels", []), res.get("bboxes", []), int(ids.shape[1]) >= 1024


def detect(model, processor, image: Image.Image, use_grounding: bool = True) -> dict:
    """<OD> 와 품목 사전 grounding 결과를 합친다."""
    t0 = time.time()
    objs, truncated = [], False
    labels, boxes, tr = _run(model, processor, image, "<OD>")
    truncated |= tr
    objs += [{"label": l, "box": b, "task": "OD"} for l, b in zip(labels, boxes)]
    rejected = []
    if use_grounding:
        labels, boxes, tr = _run(model, processor, image, "<CAPTION_TO_PHRASE_GROUNDING>", ". ".join(VOCAB) + ".")
        truncated |= tr
        # 카페 핵심 장비(커피머신, 그라인더, 카운터 등)를 일반 가구보다 먼저 검증하도록 우선순위 정렬
        paired = sorted(
            zip(labels, boxes),
            key=lambda x: 0 if x[0].lower().strip() in CAFE_PRIORITY_LABELS else 1
        )
        for l, b in paired[:MAX_VERIFY]:
            ok, cap = _verify(model, processor, image, l, b, objs)
            o = {"label": l, "box": b, "task": "GROUNDING", "check": cap}
            (objs if ok else rejected).append(o)
    for i, o in enumerate(objs):
        o["id"] = i
        o["box"] = [round(float(v), 1) for v in o["box"]]
    for o in rejected:
        o["box"] = [round(float(v), 1) for v in o["box"]]
    return {"objects": objs, "rejected": rejected, "seconds": round(time.time() - t0, 1), "truncated": truncated}


MAX_VERIFY = 18  # 카페 장비가 누락되지 않도록 검증 상한 확대 (기존 8 -> 18)

# Florence-2 캡션에서 자주 나오는 외형·동의어 사전 대폭 확장
SYNONYMS = {
    "espresso machine": ["espresso", "coffee", "machine", "maker", "appliance", "brewer", "stainless", "silver"],
    "coffee machine": ["coffee", "espresso", "machine", "maker", "appliance", "brewer", "dispenser"],
    "coffee grinder": ["grinder", "coffee", "mill", "machine", "appliance", "hopper", "cylinder", "black", "silver"],
    "ice maker": ["ice", "machine", "maker", "freezer", "cooler", "bin", "stainless", "appliance"],
    "blender": ["blender", "mixer", "pitcher", "jar", "appliance", "machine"],
    "water dispenser": ["water", "dispenser", "boiler", "heater", "purifier", "machine", "appliance", "tap"],
    "refrigerator": ["fridge", "refrigerator", "cooler", "freezer", "stainless", "cabinet", "appliance"],
    "freezer": ["freezer", "fridge", "refrigerator", "cooler", "chest", "stainless"],
    "display case": ["display", "case", "showcase", "counter", "cabinet", "glass", "fridge", "cooler", "bakery", "pastry"],
    "pastry display": ["pastry", "bakery", "cake", "bread", "display", "case", "showcase", "glass", "counter"],
    "counter": ["counter", "bar", "desk", "table", "cabinet", "island", "worktop", "stand", "reception", "wood"],
    "bar counter": ["bar", "counter", "desk", "island", "worktop", "table", "cabinet", "stand"],
    "cash register": ["register", "pos", "cash", "computer", "screen", "monitor", "terminal", "tablet", "display"],
    "pos terminal": ["pos", "terminal", "register", "screen", "monitor", "computer", "tablet", "cash"],
    "kiosk": ["kiosk", "screen", "monitor", "display", "terminal", "stand", "machine"],
    "oven": ["oven", "stove", "toaster", "bakery", "appliance", "machine"],
    "microwave": ["microwave", "oven", "appliance"],
    "dishwasher": ["dishwasher", "washer", "machine", "stainless", "appliance"],
    "sink": ["sink", "faucet", "tap", "basin", "wash", "stainless", "counter"],
    "television": ["tv", "television", "screen", "monitor", "display"],
    "cabinet": ["cabinet", "cupboard", "drawer", "shelf", "storage", "counter"],
}


def _verify(model, processor, image, label, box, od_objs):
    """grounding 박스를 잘라 캡션을 만들고 품목명이 나오는지 확인 (카페 핵심 장비는 관대하게 판정)."""
    clean_label = label.lower().strip()
    if any(_iou(box, o["box"]) > 0.4 and clean_label.split()[-1] in o["label"].lower() for o in od_objs):
        return True, "OD 일치"
    x1, y1, x2, y2 = [int(v) for v in box]
    # 그라인더 등 소형 장비는 12px 이상이면 검증 허용
    min_px = 12 if clean_label in CAFE_PRIORITY_LABELS else 16
    if x2 - x1 < min_px or y2 - y1 < min_px:
        return False, "너무 작음"
    crop = image.crop((x1, y1, x2, y2))
    cap = _caption(model, processor, crop).lower()

    # 캡션에서 배경(벽·바닥·천장·사람)만 단독으로 나온 경우가 아니면 카페 핵심 장비는 폭넓게 인정
    words = SYNONYMS.get(clean_label, clean_label.split())
    if any(w in cap for w in words):
        return True, cap
    if clean_label in CAFE_PRIORITY_LABELS:
        generic_equip = ["machine", "appliance", "equipment", "device", "counter", "kitchen", "cafe", "shop", "metal"]
        if any(g in cap for g in generic_equip):
            return True, f"{cap} (카페장비 인정)"
    return False, cap


@torch.inference_mode()
def _caption(model, processor, image):
    inputs = processor(text="<CAPTION>", images=image, return_tensors="pt")
    ids = model.generate(input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                         max_new_tokens=40, num_beams=1, do_sample=False)
    return processor.batch_decode(ids, skip_special_tokens=True)[0]


def _iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = lambda r: max(0, r[2] - r[0]) * max(0, r[3] - r[1])
    u = area(a) + area(b) - inter
    return inter / u if u else 0.0


def code_filter(objects: list[dict], image_size: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    """1차 필터: 제외 단어, 너무 작은 박스, 두 작업이 같은 물체를 중복으로 잡은 경우."""
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
        elif any(k["task"] != o.get("task") and k["label"].lower() == label and _iou(k["box"], o["box"]) > 0.6
                 for k in kept):  # OD 와 grounding 이 같은 물체를 둘 다 잡은 경우만 중복 처리
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

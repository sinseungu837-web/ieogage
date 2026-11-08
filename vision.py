"""Florence-2-base 탐지 + 코드 1차 필터."""
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

# phrase grounding 에 넣는 찾을 품목 (영어). Florence 는 영어 라벨을 낸다.
VOCAB = [
    "espresso machine", "coffee grinder", "ice maker", "refrigerator", "freezer",
    "display case", "blender", "oven", "microwave", "dishwasher", "sink", "table",
    "chair", "stool", "sofa", "shelf", "cabinet", "cash register", "monitor",
    "air conditioner", "fan", "lamp", "mirror", "television",
    "coffee machine", "water dispenser", "under-counter refrigerator", "showcase refrigerator", "card terminal",
]

# 코드 1차 제외 목록: 사람·건물 구조·배경. 대표 단어만 두고 나머지는 LLM 이 2차로 거른다.
BLOCK_WORDS = [
    "person", "man", "woman", "boy", "girl", "people", "human", "face", "hand", "head",
    "wall", "floor", "ceiling", "window", "door", "pillar", "stairs", "building",
    "sky", "tree", "grass", "text", "shadow", "light", "reflection",
]
MIN_AREA_RATIO = 0.003  # 사진 면적의 0.3% 미만 박스는 잡음으로 제외


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
    """<OD> 와 (선택) 품목 사전 grounding 결과를 합친다."""
    t0 = time.time()
    objs, truncated = [], False
    labels, boxes, tr = _run(model, processor, image, "<OD>")
    truncated |= tr
    objs += [{"label": l, "box": b, "task": "OD"} for l, b in zip(labels, boxes)]
    rejected = []
    if use_grounding:
        labels, boxes, tr = _run(model, processor, image, "<CAPTION_TO_PHRASE_GROUNDING>", ". ".join(VOCAB) + ".")
        truncated |= tr
        for l, b in list(zip(labels, boxes))[:MAX_VERIFY]:
            ok, cap = _verify(model, processor, image, l, b, objs)
            o = {"label": l, "box": b, "task": "GROUNDING", "check": cap}
            (objs if ok else rejected).append(o)
    for i, o in enumerate(objs):
        o["id"] = i
        o["box"] = [round(float(v), 1) for v in o["box"]]
    for o in rejected:
        o["box"] = [round(float(v), 1) for v in o["box"]]
    return {"objects": objs, "rejected": rejected, "seconds": round(time.time() - t0, 1), "truncated": truncated}


MAX_VERIFY = 8  # grounding 박스 검증 상한 (CPU 시간 보호)
SYNONYMS = {"refrigerator": ["fridge", "refrigerator", "cooler"], "display case": ["display", "case", "showcase", "counter"],
            "cash register": ["register", "pos", "cash", "computer", "screen"], "ice maker": ["ice", "machine"],
            "espresso machine": ["espresso", "coffee", "machine"], "coffee grinder": ["grinder", "coffee"],
            "television": ["tv", "television", "screen"], "cabinet": ["cabinet", "cupboard", "drawer"]}


def _verify(model, processor, image, label, box, od_objs):
    """grounding 은 사전 단어를 억지로 맞추는 경향이 있어, 박스를 잘라 캡션을 다시 만들고 품목명이 나오는지 확인."""
    if any(_iou(box, o["box"]) > 0.5 and label.split()[-1] in o["label"].lower() for o in od_objs):
        return True, "OD 일치"
    x1, y1, x2, y2 = [int(v) for v in box]
    if x2 - x1 < 16 or y2 - y1 < 16:
        return False, "너무 작음"
    crop = image.crop((x1, y1, x2, y2))
    cap = _caption(model, processor, crop).lower()
    words = SYNONYMS.get(label, label.split())
    return any(w in cap for w in words), cap


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
        if any(word in label.split() for word in BLOCK_WORDS):
            dropped.append({**o, "reason": "제외 목록"})
        elif (x2 - x1) * (y2 - y1) < MIN_AREA_RATIO * w * h:
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

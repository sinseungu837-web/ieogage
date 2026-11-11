"""1단계 물체 탐지: RF-DETR nano (COCO 80종) + 코드 1차 필터."""
from __future__ import annotations

import os
import time
from pathlib import Path

import torch
from dotenv import load_dotenv
from PIL import Image

load_dotenv(Path(__file__).parent / ".env")  # 로컬 .env (GEMINI_API_KEY 등). 서버는 Secrets 사용

RFDETR_THRESHOLD = 0.35  # 이 점수 미만 박스는 버림
LOW_CONF = 0.5  # 이 점수 미만은 '애매함' → Gemini 사진 재확인 대상
MAX_SIDE = 1024  # 멀리 있는 작은 장비도 보이도록 긴 변 1024 유지

# 작은 박스 기준을 완화할 카페 핵심 라벨 (COCO 에 있는 것은 sink·oven·microwave·refrigerator)
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
MIN_AREA_RATIO = 0.003            # 일반 물체 최소 면적 (사진의 0.3%)
MIN_PRIORITY_AREA_RATIO = 0.0005  # 카페 핵심 장비 최소 면적 (0.05%)


def _cpu_limit() -> int:
    """컨테이너(서버)에 실제로 허용된 CPU 수. 서버는 코어를 많이 보여 줘도 2개만 허용될 수 있어
    스레드를 과하게 띄우면 서로 경쟁해 크게 느려진다."""
    try:  # cgroup v2
        q, p = open("/sys/fs/cgroup/cpu.max").read().split()
        if q != "max":
            return max(1, int(int(q) / int(p)))
    except Exception:
        pass
    try:  # cgroup v1
        q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read()); p = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if q > 0:
            return max(1, q // p)
    except Exception:
        pass
    return os.cpu_count() or 2


def load_model():
    """(모델, 프로세서). RF-DETR 은 프로세서가 없어 None."""
    from rfdetr import RFDETRNano
    torch.set_num_threads(_cpu_limit())
    return RFDETRNano(), None


def prepare(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def detect(model, processor, image: Image.Image) -> dict:
    from rfdetr.utilities.class_names import COCO_CLASSES
    t0 = time.time()
    d = model.predict(image, threshold=RFDETR_THRESHOLD)
    objs = []
    for i, (box, cid, score) in enumerate(zip(d.xyxy, d.class_id, d.confidence)):
        objs.append({"id": i, "label": COCO_CLASSES[int(cid)], "box": [round(float(v), 1) for v in box],
                     "task": "RFDETR", "score": round(float(score), 2)})
    return {"objects": objs, "rejected": [], "seconds": round(time.time() - t0, 1), "truncated": False}


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

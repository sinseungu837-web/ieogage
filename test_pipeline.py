"""파이프라인 점검: python test_pipeline.py [사진경로]"""
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

import llm
import vision

load_dotenv(Path(__file__).parent / ".env")

# 1) LLM 단독: 가상의 탐지 라벨 목록
fake = [{"id": i, "label": l} for i, l in enumerate(
    ["man", "espresso machine", "chair", "chair", "chair", "wall", "coffee cup", "display case",
     "refrigerator", "potted plant", "window", "cash register", "cake", "table", "table"])]
kept, dropped = vision.code_filter([{**o, "box": [o["id"]*60, 0, o["id"]*60+50, 400], "task": "OD"} for o in fake], (1000, 1000))
t = time.time()
res = llm.classify_items(kept)
print("[LLM] %.1fs" % (time.time() - t))
print(" code dropped:", [d["label"] for d in dropped])
print(" items:", [(i["name"], i["count"], i["uncertain"]) for i in res["items"]])
print(" llm excluded:", res["excluded"])

# 2) RF-DETR 실제 실행
if len(sys.argv) > 1:
    model, proc = vision.load_model()
    img = vision.prepare(Image.open(sys.argv[1]))
    det = vision.detect(model, proc, img)
    print(f"[RF-DETR] {det['seconds']}s:", [(o["label"], o["score"]) for o in det["objects"]][:30])

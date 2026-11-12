"""파이프라인 점검 (Gemini 단독 탐지): python test_pipeline.py 사진경로 [사진경로 ...]"""
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

import llm
import vision

load_dotenv(Path(__file__).parent / ".env")

for path in sys.argv[1:]:
    img = vision.prepare(Image.open(path))
    t = time.time()
    det, result = llm.detect_all(img)
    print(f"{Path(path).name} {time.time() - t:.1f}s",
          [(i["name"], i["count"], i["category"]) for i in result["items"]])

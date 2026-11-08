"""Gemini 로 2차 판별: 판매 가능한 집기만 남기고 한국어 품목명으로 묶는다.
애매한 품목(헷갈리기 쉬운 장비, 고정 설비 의심)만 잘라낸 이미지로 한 번 더 확인한다."""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

from google import genai
from google.genai import types

PROMPT_FILE = Path(__file__).parent / "prompts" / "item_filter_v2.md"
DEFAULT_MODEL = "gemini-3.1-flash-lite"

# 서로 헷갈리기 쉬운 장비 묶음: 이 이름으로 판별된 품목은 이미지로 다시 확인한다
CONFUSABLE = {
    "커피 장비": ["에스프레소 머신", "커피 그라인더", "커피머신", "블렌더", "정수기", "제빙기"],
    "냉장·보관": ["업소용 냉장고", "테이블 냉장고", "쇼케이스 냉장고", "업소용 냉동고", "수납장", "하부장", "선반"],
    "조명": ["조명 스탠드", "펜던트 조명"],
}
MAX_CROPS = 8  # 이미지 재확인 상한 (속도·무료 사용량 보호)


def _section(text: str, name: str) -> str:
    m = re.search(rf"## \[{re.escape(name)}\][^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1).strip() if m else ""


def load_prompt() -> dict:
    t = PROMPT_FILE.read_text(encoding="utf-8")
    return {
        "rules": _section(t, "규칙"),
        "dictionary": _section(t, "품목 사전"),
        "format": _section(t, "출력 형식"),
        "examples": _section(t, "예시 1") + "\n\n" + _section(t, "예시 2"),
    }


def get_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        try:  # 배포 시 Streamlit secrets
            import streamlit as st
            key = st.secrets.get("GEMINI_API_KEY")
        except Exception:
            key = None
    return key


_CLIENT = None


def _client():
    """클라이언트를 재사용 (임시 객체로 쓰면 요청 중에 닫히는 문제)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=get_api_key())
    return _CLIENT


def _generate(**kw):
    """Gemini 호출. 일시 과부하(503)·사용량 초과(429)는 재시도 후 다른 경량 모델로 대체."""
    import time
    from google.genai import errors
    models = [kw.pop("model")] + [m for m in FALLBACK_MODELS]
    last = None
    for m in dict.fromkeys(models):
        for attempt in range(2):
            try:
                return _client().models.generate_content(model=m, **kw)
            except (errors.ServerError, errors.ClientError) as e:
                if getattr(e, "code", None) not in (429, 500, 503):
                    raise
                last = e
                time.sleep(2 * (attempt + 1))
    raise last


FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]

def _model(model):
    return model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def classify_items(objects: list[dict], model: str | None = None) -> dict:
    p = load_prompt()
    system = (p["rules"] + "\n\n[품목 사전]\n" + p["dictionary"] + "\n\n[출력 형식]\n" + p["format"]
              + "\n\n[예시]\n" + p["examples"])
    payload = [{"id": o["id"], "label": o["label"], **({"hint": o["hint"]} if o.get("hint") else {})}
               for o in objects]
    resp = _generate(
        model=_model(model),
        contents="입력:\n" + json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=system, temperature=0.1, response_mime_type="application/json"),
    )
    data = json.loads(resp.text)
    if isinstance(data, list):  # 모델이 가끔 목록만 돌려주는 경우 형식 보정
        data = {"items": data, "excluded": []}
    return validate(data, {o["id"] for o in objects})


def validate(data: dict, valid_ids: set[int]) -> dict:
    """LLM 출력 검사: 형식, 없는 id 참조 제거(지어낸 물건 차단), 개수 보정."""
    items = []
    for it in data.get("items", []):
        ids = [i for i in it.get("source_ids", []) if i in valid_ids]
        if not ids or not it.get("name"):
            continue  # 근거 박스가 없는 품목은 버린다
        items.append({
            "name": str(it["name"]),
            "count": len(ids),  # 개수는 LLM 이 아닌 근거 박스 수로 확정
            "source_ids": ids,
            "in_dictionary": bool(it.get("in_dictionary", True)),
            "uncertain": bool(it.get("uncertain", False)),
            "category": it.get("category", "집기"),
        })
    return {"items": items, "excluded": data.get("excluded", [])}


def _group_of(name: str) -> str | None:
    for g, names in CONFUSABLE.items():
        if name in names:
            return g
    return None


def verify_ambiguous(image, objects: list[dict], result: dict, model: str | None = None) -> dict:
    """애매한 품목만 잘라서 Gemini 에 이미지로 보여 주고 이름·고정 여부를 다시 판단.
    대상: 헷갈리기 쉬운 장비 묶음에 속한 품목, 또는 uncertain 품목. 한 번의 호출로 처리."""
    by_id = {o["id"]: o for o in objects}
    targets = []  # (item, 대표 box id)
    for it in result["items"]:
        if it["category"] == "기타":
            continue
        if _group_of(it["name"]) or it["uncertain"]:
            targets.append(it)
    targets = targets[:MAX_CROPS]
    if not targets:
        result["verified"] = []
        return result

    parts, meta = [], []
    for n, it in enumerate(targets):
        oid = it["source_ids"][0]
        x1, y1, x2, y2 = [int(v) for v in by_id[oid]["box"]]
        pad = int(0.08 * max(x2 - x1, y2 - y1))  # 주변 맥락을 조금 포함
        crop = image.crop((max(0, x1 - pad), max(0, y1 - pad), min(image.width, x2 + pad), min(image.height, y2 + pad)))
        crop.thumbnail((384, 384))
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, "JPEG", quality=85)
        g = _group_of(it["name"])
        cands = CONFUSABLE[g] if g else [it["name"]]
        parts.append(f"[{n}] 현재 이름: {it['name']} / 후보: {', '.join(cands)}")
        parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
        meta.append((it, cands))

    instruction = (
        "아래 각 이미지는 폐업 매장 사진에서 잘라낸 물건이다. 번호마다 판단하라.\n"
        "1) name: 후보 중 실제 물건에 가장 맞는 이름. 후보 중 맞는 것이 없으면 실제 한국어 일반 명칭.\n"
        "2) fixed: 바닥·벽·천장에 고정되어 떼어 팔기 어려운 설비면 true.\n"
        "3) sure: 이미지로 확실히 판단되면 true.\n"
        '출력은 JSON 만: {"results":[{"n":번호,"name":"...","fixed":false,"sure":true}]}'
    )
    resp = _generate(
        model=_model(model), contents=[instruction, *parts],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    verified = []
    out = json.loads(resp.text)
    if isinstance(out, list):  # 형식 보정
        out = {"results": out}
    for r in out.get("results", []):
        n = r.get("n")
        if not isinstance(n, int) or not 0 <= n < len(meta):
            continue
        it, cands = meta[n]
        new = str(r.get("name") or it["name"])
        verified.append({"before": it["name"], "after": new, "fixed": bool(r.get("fixed")), "sure": bool(r.get("sure"))})
        it["name"] = new
        it["in_dictionary"] = new in cands or it["in_dictionary"]
        it["uncertain"] = bool(r.get("fixed")) or not bool(r.get("sure"))
        if r.get("fixed"):
            it["note"] = "고정 설비 의심"
    # 이름이 바뀌어 같아진 품목은 합친다
    merged = {}
    for it in result["items"]:
        if it["name"] in merged:
            m = merged[it["name"]]
            m["source_ids"] += it["source_ids"]
            m["count"] = len(m["source_ids"])
            m["uncertain"] = m["uncertain"] or it["uncertain"]
        else:
            merged[it["name"]] = it
    result["items"] = list(merged.values())
    result["verified"] = verified
    return result


def find_missing(image, result: dict, objects: list[dict], model: str | None = None) -> tuple[list[dict], list[dict]]:
    """Florence 가 놓친 판매 가능 장비를 Gemini 가 사진 전체를 1회 보고 보완 (박스 포함).
    반환: (새 물체 목록, 새 품목 목록). 이미 찾은 품목 이름은 제외해 중복을 막는다."""
    found = sorted({it["name"] for it in result["items"]})
    p = load_prompt()
    img = image.copy()
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    instruction = (
        "폐업 매장 사진이다. 아래 '이미 찾은 품목' 외에, 중고로 팔 수 있는 업소용 장비·가구·가전 중 "
        "사진에 분명히 보이는데 빠진 것만 찾아라. 사람·건물·고정된 카운터·소모품·음식·장식은 제외. "
        "보이지 않는 물건은 절대 추가하지 말 것.\n"
        f"이미 찾은 품목: {', '.join(found) or '없음'}\n"
        f"품목 이름은 가능하면 다음 사전에서: {p['dictionary']}\n"
        '물건 하나마다 box_2d=[ymin,xmin,ymax,xmax] (0~1000 정규화). JSON 만: '
        '{"items":[{"name":"...","box_2d":[0,0,0,0]}]}'
    )
    resp = _generate(
        model=_model(model), contents=[instruction, types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    out = json.loads(resp.text)
    if isinstance(out, list):
        out = {"items": out}
    W, H = image.size
    next_id = max((o["id"] for o in objects), default=-1) + 1
    new_objs, groups = [], {}
    for it in out.get("items", []):
        name, b = str(it.get("name", "")).strip(), it.get("box_2d")
        if not name or name in found or not (isinstance(b, list) and len(b) == 4):
            continue  # 박스 근거가 없거나 이미 찾은 품목이면 버림
        y1, x1, y2, x2 = [float(v) for v in b]
        box = [x1 * W / 1000, y1 * H / 1000, x2 * W / 1000, y2 * H / 1000]
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        new_objs.append({"id": next_id, "label": name, "box": [round(v, 1) for v in box], "task": "GEMINI"})
        groups.setdefault(name, []).append(next_id)
        next_id += 1
    new_items = [{"name": n, "count": len(ids), "source_ids": ids, "in_dictionary": n in p["dictionary"],
                  "uncertain": True, "category": "집기", "note": "AI 보완 탐지"} for n, ids in groups.items()]
    return new_objs, new_items

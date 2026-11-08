"""Gemini 로 2차 판별: 판매 가능한 집기만 남기고 한국어 품목명으로 묶는다."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from google import genai
from google.genai import types

PROMPT_FILE = Path(__file__).parent / "prompts" / "item_filter_v1.md"


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


def classify_items(objects: list[dict], model: str | None = None) -> dict:
    p = load_prompt()
    system = (p["rules"] + "\n\n[품목 사전]\n" + p["dictionary"] + "\n\n[출력 형식]\n" + p["format"]
              + "\n\n[예시]\n" + p["examples"])
    payload = [{"id": o["id"], "label": o["label"]} for o in objects]
    client = genai.Client(api_key=get_api_key())
    resp = client.models.generate_content(
        model=model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        contents="입력:\n" + json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=system, temperature=0.1, response_mime_type="application/json"),
    )
    data = json.loads(resp.text)
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
        })
    return {"items": items, "excluded": data.get("excluded", [])}

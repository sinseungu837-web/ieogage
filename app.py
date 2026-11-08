"""이어가게 - 매장 사진 한 장으로 집기 목록, 참고 시세, 판매처를 정리하는 웹 (MVP 뼈대)."""
from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

import llm
import vision

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

MAX_RUNS_PER_SESSION = 10      # 링크 공개 시 무료 API 한도 보호
MAX_UPLOAD_MB = 10

st.set_page_config(page_title="이어가게", page_icon="🏪", layout="centered")


MAX_RUNS_PER_DAY = 200         # 접속자 전체 합산 하루 상한 (무료 API 한도·서버 보호)


@st.cache_resource
def daily_counter() -> dict:
    return {"date": None, "count": 0}


def take_daily_quota() -> bool:
    import datetime
    c = daily_counter()
    today = datetime.date.today().isoformat()
    if c["date"] != today:
        c.update(date=today, count=0)
    if c["count"] >= MAX_RUNS_PER_DAY:
        return False
    c["count"] += 1
    return True


@st.cache_resource(show_spinner="AI 모델을 불러오는 중 (처음 1회)...")
def get_vision():
    return vision.load_model()


def load_prices() -> pd.DataFrame:
    f = BASE / "data" / "price_table.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def search_links(name: str) -> dict:
    q = urllib.parse.quote(name)
    return {
        "당근": f"https://www.daangn.com/kr/buy-sell/?search={q}",
        "번개장터": f"https://m.bunjang.co.kr/search/products?q={q}",
        "중고나라": f"https://web.joongna.com/search/{q}",
    }


FONT_CANDIDATES = [  # Windows(노트북) / Linux(배포 서버, packages.txt 의 fonts-nanum)
    "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
PALETTE = [(230, 57, 70), (29, 120, 220), (0, 150, 110), (240, 140, 0), (140, 70, 200),
           (0, 160, 180), (200, 60, 150), (90, 110, 30), (120, 80, 40), (60, 60, 60)]


def korean_font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw_boxes(image: Image.Image, objects: list[dict], names: dict[int, str]) -> Image.Image:
    """객체 탐지 결과처럼 품목별 색상 박스 + 한글 라벨(같은 품목은 번호)."""
    img = image.copy()
    d = ImageDraw.Draw(img)
    W, H = img.size
    size = max(14, W // 45)
    font = korean_font(size)
    line = max(2, W // 300)
    colors, counters = {}, {}
    for o in objects:
        if o["id"] not in names:
            continue
        name = names[o["id"]]
        color = colors.setdefault(name, PALETTE[len(colors) % len(PALETTE)])
        counters[name] = counters.get(name, 0) + 1
        label = f"{name} {counters[name]}"
        x1, y1, x2, y2 = o["box"]
        d.rectangle([x1, y1, x2, y2], outline=color, width=line)
        l, t, r, b = d.textbbox((0, 0), label, font=font)
        tw, th = r - l + 8, b - t + 6
        lx = min(max(0, x1), W - tw)
        ly = y1 - th if y1 - th >= 0 else y1  # 위쪽에 자리가 없으면 박스 안쪽 위에
        d.rectangle([lx, ly, lx + tw, ly + th], fill=color)
        d.text((lx + 4 - l, ly + 3 - t), label, fill="white", font=font)
    return img


# ---------------- 화면 ----------------
st.title("🏪 이어가게")
st.caption("매장 사진 한 장으로 남은 집기를 정리하고, 다음 가게로 이어줍니다.")

if not llm.get_api_key():
    st.error("서버에 AI 키가 설정되지 않았습니다. 관리자에게 문의하세요.")
    st.stop()

runs = st.session_state.setdefault("runs", 0)
src = st.radio("사진 입력", ["사진 올리기", "카메라로 찍기"], horizontal=True)
file = st.file_uploader("매장 사진", type=["jpg", "jpeg", "png"]) if src == "사진 올리기" \
    else st.camera_input("매장을 넓게 찍어 주세요")
use_grounding = st.toggle("품목 사전으로 한 번 더 찾기 (정확도↑, 시간 약 2배)", value=True)

if file is not None:
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"{MAX_UPLOAD_MB}MB 이하 사진만 올릴 수 있습니다.")
        st.stop()
    image = vision.prepare(Image.open(file))
    st.image(image, caption="올린 사진", use_container_width=True)

    if st.button("집기 분석하기", type="primary", disabled=runs >= MAX_RUNS_PER_SESSION):
        if not take_daily_quota():
            st.error("오늘 서비스 전체 분석 한도에 도달했습니다. 내일 다시 이용해 주세요.")
            st.stop()
        st.session_state.runs = runs + 1
        model, processor = get_vision()
        with st.spinner("① 사진에서 물체를 찾는 중 (Florence-2)..."):
            det = vision.detect(model, processor, image, use_grounding)
        kept, dropped = vision.code_filter(det["objects"], image.size)
        with st.spinner("② 판매할 수 있는 집기만 고르는 중 (Gemini)..."):
            try:
                result = llm.classify_items(kept) if kept else {"items": [], "excluded": []}
            except Exception as e:
                st.error("AI 판별 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                st.caption(type(e).__name__)
                st.stop()
        st.session_state.last = {"image": image, "det": det, "kept": kept, "dropped": dropped, "result": result}
    if runs >= MAX_RUNS_PER_SESSION:
        st.warning("이 화면에서 가능한 분석 횟수를 모두 사용했습니다.")

last = st.session_state.get("last")
if last:
    items = last["result"]["items"]
    names = {i: it["name"] for it in items for i in it["source_ids"]}
    st.subheader("인식된 집기")
    st.image(draw_boxes(last["image"], last["det"]["objects"], names), use_container_width=True)
    st.caption(f"탐지 {len(last['det']['objects'])}개 → 코드 필터 후 {len(last['kept'])}개 → "
               f"집기 {len(items)}종 · {last['det']['seconds']}초"
               + (" · ⚠ 물체가 많아 일부 누락 가능 (나눠 찍기 권장)" if last["det"]["truncated"] else ""))

    if items:
        df = pd.DataFrame([{"품목": it["name"], "수량": it["count"],
                            "확인 필요": "예" if it["uncertain"] or not it["in_dictionary"] else ""}
                           for it in items])
        st.write("틀린 품목·수량은 표에서 바로 고칠 수 있습니다.")
        edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="editor")

        st.subheader("참고 시세 · 판매처")
        prices = load_prices()
        for _, row in edited.iterrows():
            name = str(row["품목"]).strip()
            if not name:
                continue
            with st.expander(f"{name} × {row['수량']}"):
                hit = prices[prices["품목"] == name] if not prices.empty and "품목" in prices else pd.DataFrame()
                if hit.empty:
                    st.info("시세 데이터 준비 중입니다. 아래 링크에서 비슷한 매물을 확인해 보세요.")
                else:
                    st.dataframe(hit, use_container_width=True, hide_index=True)
                cols = st.columns(3)
                for col, (site, url) in zip(cols, search_links(name).items()):
                    col.link_button(f"{site}에서 보기", url)
    else:
        st.info("판매할 수 있는 집기를 찾지 못했습니다. 매장을 더 넓게 찍거나 나눠서 찍어 보세요.")

    with st.expander("AI 처리 과정 보기 (개발·발표용)"):
        st.write("**Florence-2 원본 탐지**")
        st.dataframe(pd.DataFrame(last["det"]["objects"]), use_container_width=True, hide_index=True)
        st.write("**품목 사전 탐지 중 검증 실패로 제외 (오탐 방지)**")
        st.dataframe(pd.DataFrame(last["det"].get("rejected", [])), use_container_width=True, hide_index=True)
        st.write("**코드 1차 필터에서 제외**")
        st.dataframe(pd.DataFrame(last["dropped"]), use_container_width=True, hide_index=True)
        st.write("**Gemini 2차 판별에서 제외**")
        st.dataframe(pd.DataFrame(last["result"]["excluded"]), use_container_width=True, hide_index=True)

st.divider()
st.caption("시세는 참고용이며 실제 거래가와 다를 수 있습니다. 올린 사진은 저장하지 않습니다.")

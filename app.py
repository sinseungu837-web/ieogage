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

from box_selector import select_box, validated_box

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


FONT_CANDIDATES = [  # Windows(노트북) / Linux(배포 서버) / macOS(맥북 한글 폰트 추가)
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
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


def normalize_natural_input(raw_text: str) -> str:
    """사용자가 자연어 문장('카운터 위 그라인더 추가해줘', '이건 제빙기야')으로 입력해도 핵심 품목명만 깔끔히 추출."""
    t = raw_text.strip()
    for suffix in ["추가해줘", "추가해주세요", "추가", "으로 변경해줘", "로 변경해줘", "으로 변경", "로 변경", "입니다", "이에요", "예요", "인듯", "같아요", "이야", "야"]:  # 긴 표현부터 (승우: "스툴이야"→"스툴")
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    for prefix in ["이건 ", "이거는 ", "여기는 ", "저기 있는 ", "카운터 위 ", "옆에 있는 "]:
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
    return t.strip() or raw_text.strip()


def draw_boxes(
    image: Image.Image,
    objects: list[dict],
    names: dict[int, str],
    selected_id: int | None = None,
    pending_box: list[float] | None = None,
) -> Image.Image:
    """객체 탐지 결과와 번호표([#id])를 그린다."""
    img = image.copy()
    d = ImageDraw.Draw(img)
    W, H = img.size
    size = max(14, W // 45)
    label_font = korean_font(size)  # 배경 라벨 크기는 기존 글꼴 기준으로 유지
    font = korean_font(max(10, int(size * 0.8)))
    line = max(2, W // 300)
    colors, counters = {}, {}

    for o in objects:
        oid = o["id"]
        if oid not in names:
            continue
        name = names[oid]
        color = colors.setdefault(name, PALETTE[len(colors) % len(PALETTE)])
        counters[name] = counters.get(name, 0) + 1
        is_manual = o.get("task") == "USER"
        label = f"[#{oid}] {name} {counters[name]}" + (" (직접추가)" if is_manual else "")

        x1, y1, x2, y2 = o["box"]
        # 현재 선택된 박스는 노란색 두꺼운 테두리로 강조
        box_color = (255, 215, 0) if oid == selected_id else color
        box_width = line * 2 if oid == selected_id else line
        d.rectangle([x1, y1, x2, y2], outline=box_color, width=box_width)

        l, t, r, b = d.textbbox((0, 0), label, font=label_font)
        tw, th = r - l + 8, b - t + 6
        lx = min(max(0, x1), W - tw)
        ly = y1 - th if y1 - th >= 0 else y1
        d.rectangle([lx, ly, lx + tw, ly + th], fill=color)
        tl, tt, tr, tb = d.textbbox((0, 0), label, font=font)
        d.text((lx + 4 - tl, ly + (th - (tb - tt)) / 2 - tt), label, fill="white", font=font)

    # 사용자가 '미탐지 물건 추가'를 위해 방금 클릭한 위치 미리보기 박스 표시
    if pending_box:
        px1, py1, px2, py2 = pending_box
        cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
        d.rectangle([px1, py1, px2, py2], outline=(0, 230, 118), width=line + 2)
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(255, 0, 80), outline="white", width=2)
        p_label = "➕ 새 집기 추가 위치"
        l, t, r, b = d.textbbox((0, 0), p_label, font=label_font)
        tw, th = r - l + 8, b - t + 6
        lx = min(max(0, px1), W - tw)
        ly = py1 - th if py1 - th >= 0 else py1
        d.rectangle([lx, ly, lx + tw, ly + th], fill=(0, 180, 90))
        tl, tt, tr, tb = d.textbbox((0, 0), p_label, font=font)
        d.text((lx + 4 - tl, ly + (th - (tb - tt)) / 2 - tt), p_label, fill="white", font=font)

    return img


def rebuild_items_from_boxes(objects: list[dict], names: dict[int, str], orig_items: list[dict]) -> list[dict]:
    """사용자가 수정/삭제/추가한 박스(names) 상태를 바탕으로 최종 품목 요약표(items)를 다시 묶는다."""
    orig_meta = {it["name"]: it for it in orig_items}
    grouped: dict[str, list[int]] = {}
    for o in objects:
        oid = o["id"]
        if oid in names:
            nm = names[oid]
            grouped.setdefault(nm, []).append(oid)

    new_items = []
    for nm, s_ids in grouped.items():
        meta = orig_meta.get(nm, {})
        new_items.append({
            "name": nm,
            "count": len(s_ids),
            "source_ids": s_ids,
            "in_dictionary": meta.get("in_dictionary", True),
            "uncertain": meta.get("uncertain", False),
            "category": meta.get("category", "집기"),
            "note": meta.get("note") or ("사용자 추가" if nm not in orig_meta else None),
        })
    return new_items


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
use_grounding = (st.toggle("전문 장비 한 번 더 찾기 (제빙기·쇼케이스 등 놓칠 때만 켜기, 시간 약 7배)", value=False)) if vision.DETECTOR == "florence" else False

if file is not None:
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"{MAX_UPLOAD_MB}MB 이하 사진만 올릴 수 있습니다.")
        st.stop()
    image = vision.prepare(Image.open(file))
    st.image(image, caption="올린 사진", use_container_width=True)

    import hashlib
    key = hashlib.md5(image.tobytes()).hexdigest() + str(use_grounding)
    already = st.session_state.get("last", {}).get("key") == key  # 같은 사진 재분석 방지 (안내 문구는 표시 안 함)
    if st.button("집기 분석하기", type="primary", disabled=runs >= MAX_RUNS_PER_SESSION or already):
        if not take_daily_quota():
            st.error("오늘 서비스 전체 분석 한도에 도달했습니다. 내일 다시 이용해 주세요.")
            st.stop()
        st.session_state.runs = runs + 1
        import time as _t
        T = {"load": _t.time()}
        model, processor = get_vision()
        T["모델 준비"] = _t.time() - T.pop("load")
        _s = _t.time()
        with st.spinner(f"① 사진에서 물체를 찾는 중 ({'RF-DETR' if vision.DETECTOR == 'rfdetr' else 'Florence-2'})..."):
            det = vision.detect(model, processor, image, use_grounding)
        kept, dropped = vision.code_filter(det["objects"], image.size)
        T["1단계 탐지"] = _t.time() - _s; _s = _t.time()
        with st.spinner("② 판매할 수 있는 집기만 고르는 중 (Gemini)..."):
            try:
                result = llm.classify_items(kept) if kept else {"items": [], "excluded": []}
            except Exception as e:
                st.error("AI 판별 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                st.caption(type(e).__name__)
                st.stop()
        T["Gemini 판별"] = _t.time() - _s; _s = _t.time()
        with st.spinner("③ 놓친 장비를 찾는 중 (Gemini가 사진 전체 확인)..."):
            try:
                new_objs, new_items = llm.find_missing(image, result, det["objects"])
                det["objects"] += new_objs
                result["items"] += new_items
            except Exception:
                pass  # 보완 실패 시 기존 결과 유지
        T["Gemini 보완"] = _t.time() - _s; _s = _t.time()
        with st.spinner("④ 헷갈리는 장비만(보완 결과 포함) 이미지로 다시 확인하는 중 (Gemini)..."):
            try:
                result = llm.verify_ambiguous(image, kept + [o for o in det["objects"] if o.get("task") == "GEMINI"], result)
            except Exception:
                result["verified"] = []  # 재확인 실패 시 1차 판별 결과를 그대로 사용
        T["Gemini 재확인"] = _t.time() - _s
        det["timing"] = {k: round(v, 1) for k, v in T.items()}
        st.session_state.last = {
            "key": key, "image": image, "det": det, "kept": kept, "dropped": dropped, "result": result,
            # [소정] 사용자 직접 수정용 상태: 박스 번호별 품목명, 새 박스 번호, 클릭 위치, 선택 박스
            "box_names": {i: it["name"] for it in result["items"] for i in it["source_ids"]},
            "next_id": max([o["id"] for o in det["objects"]], default=0) + 1,
            "pending_box": None,
            "selected_box_id": None,
        }
        st.rerun()
    if runs >= MAX_RUNS_PER_SESSION:
        st.warning("이 화면에서 가능한 분석 횟수를 모두 사용했습니다.")

last = st.session_state.get("last")
if last:
    W, H = last["image"].size
    box_names: dict[int, str] = last.setdefault(
        "box_names", {i: it["name"] for it in last["result"]["items"] for i in it["source_ids"]}
    )
    objects: list[dict] = last["det"]["objects"]

    st.subheader("인식된 집기 (클릭하여 직접 수정·추가 가능)")
    st.caption(
        "💡 **잘못 인식된 박스**나 **AI가 놓친 물건**이 있나요? "
        "기존 박스는 눌러 수정하고, 놓친 물건은 영역을 드래그해 추가할 수 있습니다."
    )

    mode = st.radio(
        "사진 조작", ["기존 박스 선택", "새 물건 영역 그리기"], horizontal=True,
        key=f"canvas_mode_{last['key']}",
    )
    pending_box = last.get("pending_box")
    rendered_img = draw_boxes(
        last["image"], objects, box_names, selected_id=last.get("selected_box_id"),
    )
    event = select_box(
        rendered_img, mode="draw" if mode == "새 물건 영역 그리기" else "select",
        pending_box=pending_box,
        reset_token=f"{last['key']}:{last.get('canvas_revision', 0)}:{mode}",
        key=f"interactive_canvas_{last['key']}",
    )
    if event and event.get("event_id") != last.get("canvas_event_id"):
        last["canvas_event_id"] = event.get("event_id")
        if event.get("kind") == "box" and mode == "새 물건 영역 그리기":
            new_box = validated_box(event.get("box"), W, H)
            if new_box:
                last["pending_box"] = new_box
                st.rerun()
        elif event.get("kind") == "click" and mode == "기존 박스 선택":
            x, y = event["x"], event["y"]
            hits = [o for o in objects if o["id"] in box_names
                    and o["box"][0] <= x <= o["box"][2]
                    and o["box"][1] <= y <= o["box"][3]]
            if hits:
                selected = min(hits, key=lambda o: (o["box"][2]-o["box"][0]) * (o["box"][3]-o["box"][1]))["id"]
                last["selected_box_id"] = selected
                st.session_state[f"edit_box_{last['key']}"] = selected
                st.rerun()

    # 사용자가 수정한 내역을 바탕으로 items 실시간 재계산
    items = rebuild_items_from_boxes(objects, box_names, last["result"]["items"])
    last["result"]["items"] = items

    st.caption(
        f"탐지 {len(objects)}개 → 코드 필터 후 {len(last['kept'])}개 → "
        f"현재 집기 {len(items)}종 (총 {len(box_names)}개 박스) · {last['det']['seconds']}초"
        + (" | " + " · ".join(f"{k} {v}초" for k, v in last["det"].get("timing", {}).items()))
        + (" · ⚠ 물체가 많아 일부 누락 가능 (나눠 찍기 권장)" if last["det"]["truncated"] else "")
    )

    # ---------------- 사용자 직접 수정 / 미탐지 추가 UI ----------------
    with st.container(border=True):
        st.markdown("#### 🛠️ AI 탐지 결과 직접 수정 · 미탐지 물건 추가")
        tab_edit, tab_add = st.tabs([
            "✏️ 잘못 감지된 물건 수정 / 삭제",
            "➕ 감지 못한 물건 직접 추가 (자연어 입력)",
        ])

        # 탭 1: 기존에 잡힌 박스 이름 변경 또는 삭제
        with tab_edit:
            active_ids = sorted(box_names.keys())
            if not active_ids:
                st.info("현재 표시된 집기 박스가 없습니다. 오른쪽 탭에서 새 물건을 추가해 보세요.")
            else:
                default_idx = (
                    active_ids.index(last["selected_box_id"])
                    if last.get("selected_box_id") in active_ids
                    else 0
                )
                sel_id = st.selectbox(
                    "수정하거나 삭제할 박스 번호([#번호])를 선택하세요 (사진 위 박스를 직접 클릭해도 선택됩니다)",
                    key=f"edit_box_{last['key']}",
                    options=active_ids,
                    index=default_idx,
                    format_func=lambda x: f"[#{x}] {box_names[x]}",
                )
                last["selected_box_id"] = sel_id

                col_in, col_btn1, col_btn2 = st.columns([3, 1, 1])
                with col_in:
                    new_label_raw = st.text_input(
                        "올바른 품목명을 자연어로 입력하세요",
                        value=box_names[sel_id],
                        placeholder="예: 커피 그라인더, 이건 제빙기야, 에스프레소 머신으로 변경",
                        key=f"edit_input_{sel_id}",
                    )
                with col_btn1:
                    st.write("")
                    st.write("")
                    if st.button("이름 수정", type="primary", use_container_width=True):
                        cleaned = normalize_natural_input(new_label_raw)
                        if cleaned:
                            box_names[sel_id] = cleaned
                            st.toast(f"[#{sel_id}] 품목이 '{cleaned}'(으)로 수정되었습니다!")
                            st.rerun()
                with col_btn2:
                    st.write("")
                    st.write("")
                    if st.button("박스 삭제", use_container_width=True):
                        removed_name = box_names.pop(sel_id, "")
                        last["selected_box_id"] = None
                        st.toast(f"[#{sel_id}] '{removed_name}' 박스를 삭제했습니다.")
                        st.rerun()

        # 탭 2: AI가 놓친 물건을 클릭 + 자연어로 새로 추가
        with tab_add:
            st.caption("위 사진에서 ‘새 물건 영역 그리기’를 선택한 뒤, 마우스나 손가락으로 물건을 감싸는 사각형을 그려 주세요. 기존 박스와 겹쳐도 추가할 수 있습니다.")
            if pending_box:
                st.success("영역이 선택되었습니다. 품목명을 입력해 추가하세요. 다시 그리면 영역이 바뀝니다.")
                if st.button("선택 영역 지우기"):
                    last["pending_box"] = None
                    last["canvas_revision"] = last.get("canvas_revision", 0) + 1
                    st.rerun()
            else:
                st.info("먼저 사진 위에서 추가할 물건의 영역을 그려 주세요.")

            add_col1, add_col2 = st.columns([3, 1])
            with add_col1:
                natural_add_text = st.text_input(
                    "추가할 물건을 자연어로 적어주세요",
                    placeholder="예: 커피 그라인더, 카운터 위 제빙기 추가해줘, 에스프레소 머신",
                    key="natural_add_input",
                )
            with add_col2:
                st.write("")
                st.write("")
                if st.button("➕ 이 위치에 추가", type="primary", use_container_width=True):
                    cleaned_name = normalize_natural_input(natural_add_text)
                    if not cleaned_name:
                        st.warning("추가할 물건의 이름을 입력해 주세요!")
                    elif not pending_box:
                        st.warning("사진 위에서 추가할 물건의 영역을 먼저 그려 주세요.")
                    else:
                        new_id = last["next_id"]
                        box = pending_box
                        objects.append({"id": new_id, "label": cleaned_name, "box": [float(v) for v in box], "task": "USER"})
                        box_names[new_id] = cleaned_name
                        last["next_id"] = new_id + 1
                        last["pending_box"] = None
                        last["canvas_revision"] = last.get("canvas_revision", 0) + 1
                        st.toast(f"[#{new_id}] '{cleaned_name}' 을(를) 추가했습니다.")
                        st.rerun()

    if items:
        items = sorted(items, key=lambda it: {"집기": 0, "재고·소모품": 1, "기타": 2}.get(it.get("category", "집기"), 0))  # 집기 → 재고·소모품 → 기타
        df = pd.DataFrame([{"품목": it["name"], "수량": it["count"], "분류": it.get("category", "집기"),
                            "확인 필요": it.get("note") or ("예" if it["uncertain"] or not it["in_dictionary"] else "")}
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
        st.write("**이미지 재확인으로 바뀐 이름·고정 여부**")
        st.dataframe(pd.DataFrame(last["result"].get("verified", [])), use_container_width=True, hide_index=True)

st.divider()
st.caption("시세는 참고용이며 실제 거래가와 다를 수 있습니다. 올린 사진은 저장하지 않습니다.")

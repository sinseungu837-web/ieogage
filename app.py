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
import price
import vision

from box_selector import select_box, validated_box
from inventory import inventory_rows
from estimate_view import safe_estimate
import presentation

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

MAX_RUNS_PER_SESSION = 10      # 링크 공개 시 무료 API 한도 보호
MAX_UPLOAD_MB = 10

st.set_page_config(page_title="이어가게", page_icon="🏪", layout="centered")
presentation.apply_theme()


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


def analyze_photo(image, key, count_mode="dedupe"):
    if not take_daily_quota():
        st.error("오늘 서비스 전체 분석 한도에 도달했습니다. 내일 다시 이용해 주세요.")
        st.stop()
    st.session_state.runs = st.session_state.get("runs", 0) + 1
    import time as _t
    T = {"load": _t.time()}
    model, processor = get_vision()
    T["모델 준비"] = _t.time() - T.pop("load")
    _s = _t.time()
    if True:  # Gemini 단독 탐지 (최종): 1회 호출로 탐지·이름·분류까지
        with st.spinner("사진 전체에서 판매할 물건을 찾는 중 (Gemini)..."):
            try:
                det, result = llm.detect_all(image, count_mode=count_mode)
            except Exception as e:
                code = getattr(e, "code", None)
                if code in (429, 500, 503, 504) or "Timeout" in type(e).__name__:
                    st.error("AI 서버가 일시적으로 혼잡합니다 (Google Gemini 과부하). 1~2분 뒤 다시 'AI 분석하기'를 눌러 주세요.")
                else:
                    st.error("AI 분석 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                st.caption(f"{type(e).__name__} {code or ''}")
                st.stop()
        T["Gemini 탐지"] = _t.time() - _s
        det["timing"] = {k: round(v, 1) for k, v in T.items()}
        st.session_state.last = {
            "key": key, "image": image, "det": det, "kept": det["objects"], "dropped": [], "result": result,
            "box_names": {i: it["name"] for it in result["items"] for i in it["source_ids"]},
            "next_id": max([o["id"] for o in det["objects"]], default=0) + 1,
            "pending_box": None,
            "selected_box_id": None,
            # 사진에서 로고·명판 글자가 읽힌 경우만 채워짐 (3단계 표에서 사용자가 수정 가능)
            # Gemini 가 시세표 후보 중에서 예측한 제조사·모델 (3단계 표에서 사용자가 수정 가능)
            "manufacturers": {o["id"]: o["brand"] for o in det["objects"] if o.get("brand")},
            "models": {o["id"]: o["model"] for o in det["objects"] if o.get("model")},
            "specs": {o["id"]: o["spec"] for o in det["objects"] if o.get("spec")},
            "brand_basis": {o["id"]: o["brand_basis"] for o in det["objects"] if o.get("brand_basis")},
        }
        go_to(3)
        return


def edit_page(last):
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


    rows = inventory_rows(last)
    st.caption(f"현재 집기 {len(rows)}종 · 총 {len(box_names)}개 박스")
    if last['det'].get('truncated'):
        st.caption('물체가 많아 일부 누락될 수 있습니다. 놓친 물건은 직접 추가해 주세요.')

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



def go_to(step):
    st.switch_page(PAGES[step - 1])


def apply_inventory_edit(last, rows, widget_key):
    for index, fields in st.session_state[widget_key].get('edited_rows', {}).items():
        if not 0 <= int(index) < len(rows):
            continue
        row = rows[int(index)]
        name = str(fields.get('집기명', row['집기명']) or '').strip()
        maker = str(fields.get('제조사', row['제조사']) or '').strip()
        for oid in row['box_ids']:
            if name:
                last['box_names'][oid] = name
                if name != row['집기명']:
                    st.session_state[f'edit_input_{oid}'] = name
            if maker != row['제조사']:
                last.setdefault('edited_makers', set()).add(oid)  # 화면에 '사용자 입력'으로 표시
            last.setdefault('manufacturers', {})[oid] = maker if maker != '미확인' else ''
            for column, key in (('모델명', 'models'), ('사양', 'specs'), ('연식(년)', 'conditions')):
                last.setdefault(key, {})[oid] = str(fields.get(column, row.get(column, '')) or '').strip()
    last['table_revision'] = last.get('table_revision', 0) + 1


def start_page():
    with st.container(key='hero_layout'):
        artwork, story = st.columns([1.05, 1], gap='large', vertical_alignment='center')
        with artwork:
            presentation.hero_visual()
        with story:
            presentation.section_label(1, '남은 집기의 다음 가능성')
            st.header('남은 집기, 다음 가게로 이어주세요')
            st.write('매장 사진을 올리면 AI가 집기를 정리합니다. 결과를 직접 고친 뒤 최종 목록을 확인하세요.')
            presentation.service_facts()
    presentation.start_chapter()
    with st.container(key='start_actions'):
        upload, camera = st.columns(2)
        if upload.button('📷 사진 올려 시작하기', type='primary', use_container_width=True):
            st.session_state.input_method = '사진 올리기'
            go_to(2)
        if camera.button('📸 카메라로 시작하기', use_container_width=True):
            st.session_state.input_method = '카메라로 찍기'
            go_to(2)
        if st.session_state.get('last') and st.button('이전 분석 이어서 수정하기'):
            go_to(3)


def photo_page():
    presentation.section_label(2, '사진 입력')
    st.header('사진을 준비해 주세요')
    st.caption('집기가 잘 보이도록 매장을 넓게 찍어 주세요. JPG·PNG, 최대 10MB')
    methods = ['사진 올리기', '카메라로 찍기']
    source = st.radio('사진 입력', methods, index=methods.index(st.session_state.get('input_method', methods[0])), horizontal=True)
    st.session_state.input_method = source
    file = st.file_uploader('매장 사진', type=['jpg', 'jpeg', 'png']) if source == methods[0] else st.camera_input('매장을 넓게 찍어 주세요')
    if file is not None:
        if file.size > MAX_UPLOAD_MB * 1024 * 1024:
            st.error(f'{MAX_UPLOAD_MB}MB 이하 사진만 올릴 수 있습니다.')
        else:
            # 새 파일일 때만 다시 읽는다 (같은 파일이면 사용자가 돌린 사진을 유지)
            file_id = getattr(file, 'file_id', None) or f'{file.name}:{file.size}'
            if st.session_state.get('photo_draft', {}).get('file_id') != file_id:
                try:
                    image = vision.prepare(Image.open(file))
                except Exception:
                    st.error('사진을 읽을 수 없습니다. JPG 또는 PNG 사진을 다시 선택해 주세요.')
                else:
                    st.session_state.photo_draft = {'image': image, 'file_id': file_id}
    if 'photo_draft' not in st.session_state and st.session_state.get('last'):
        st.session_state.photo_draft = {'image': st.session_state.last['image']}
    draft = st.session_state.get('photo_draft')
    if draft:
        image = draft['image']
        st.image(image, caption='분석할 사진', use_container_width=True)
        st.caption('사진이 옆으로 누웠거나 뒤집혀 있으면 똑바로 돌린 뒤 분석하세요.')
        rot_left, rot_right = st.columns(2)
        if rot_left.button('↺ 왼쪽으로 90° 돌리기', use_container_width=True):
            draft['image'] = image.rotate(90, expand=True)
            st.rerun()
        if rot_right.button('↻ 오른쪽으로 90° 돌리기', use_container_width=True):
            draft['image'] = image.rotate(-90, expand=True)
            st.rerun()
        import hashlib
        modes = list(llm.COUNT_MODES)
        count_mode = st.radio('개수 세는 방식', modes, format_func=llm.COUNT_MODES.get, horizontal=True,
                              index=modes.index(st.session_state.get('count_mode', 'dedupe')),
                              help='중복 제거: 같은 물건을 두 번 세지 않도록 확실히 보이는 것만 셉니다. '
                                   '보이는 대로 모두: 일부만 보이거나 가려진 물건까지 모두 셉니다 (과다 집계 가능).')
        st.session_state.count_mode = count_mode
        key = hashlib.md5(image.tobytes() + count_mode.encode()).hexdigest()  # 방식이 바뀌면 새로 분석
        already = st.session_state.get('last', {}).get('key') == key
        if already:
            st.caption('이 사진의 분석 결과가 있습니다. 기존 수정 내용을 이어서 확인할 수 있어요.')
        limit = st.session_state.get('runs', 0) >= MAX_RUNS_PER_SESSION
        if st.button('분석 결과 이어보기' if already else 'AI 분석하기 →', type='primary', disabled=limit and not already):
            if already:
                go_to(3)
            if not llm.get_api_key():
                st.error('서버에 AI 키가 설정되지 않았습니다. 관리자에게 문의하세요.')
            else:
                analyze_photo(image, key, count_mode)
        if limit:
            st.warning('이 화면에서 가능한 분석 횟수를 모두 사용했습니다.')
    if st.button('← 시작 화면'):
        go_to(1)


def edit_inventory_table(last):
    st.subheader('집기 목록 수정')
    st.caption('제조사·모델명은 AI가 팀 조사 시세표의 후보 중에서 예측한 값입니다 (로고가 안 보이면 형태로 예측). 틀리면 직접 고쳐 주세요. 집기명·제조사·모델명을 수정할 수 있습니다. **연식(년)** 칸에 몇 년 사용했는지 숫자로 적으면 신품가에서 연식만큼 감가한 중고가를 계산합니다 (1년 미만은 0.5). 개수는 박스 추가·삭제에 맞춰 자동으로 계산됩니다.')
    rows = inventory_rows(last)
    if not rows:
        st.info('목록이 비어 있습니다. 사진에서 영역을 지정해 물건을 추가할 수 있습니다.')
        return
    table = pd.DataFrame([{k: row.get(k, '') for k in ('집기명', '제조사', '개수', '모델명', '연식(년)')} for row in rows])
    widget_key = f"inventory_{last['key']}_{last.get('table_revision', 0)}"
    st.data_editor(table, hide_index=True, use_container_width=True, num_rows='fixed',
                   disabled=['개수'], key=widget_key, on_change=apply_inventory_edit,
                   args=(last, rows, widget_key),
                   column_config={'집기명': st.column_config.TextColumn(required=True),
                                  '제조사': st.column_config.TextColumn(),
                                  '개수': st.column_config.NumberColumn(format='%d개')})


@st.cache_data(ttl=3600, show_spinner=False)
def cached_estimate(name: str, manufacturer='', model='', spec='', condition='', revision=None) -> dict:
    # DB 교체 즉시 캐시 키가 바뀐다. 수집은 이 함수나 업로드 과정에서 실행하지 않는다.
    return price.service_estimate(name, manufacturer, model, spec, condition)


def final_page(last):
    presentation.section_label(4, '최종 분석 · 매각 견적')
    st.header('최종 분석 · 매각 견적')
    all_rows = inventory_rows(last)
    # Gemini 가 붙인 분류. 사용자가 새로 그린 박스나 바꾼 이름은 '집기'
    category = {it['name']: it.get('category', '집기') for it in last.get('result', {}).get('items', [])}
    for r in all_rows:
        r['분류'] = category.get(r['집기명'], '집기')
    # 고정 설비(카운터·하부장·천장형 에어컨 등)는 떼어 팔기 어려워 최종 견적에서 제외
    fixed = [r for r in all_rows if r['분류'] == '고정 설비']
    # 재고·소모품·장식은 가격 조회 없이 '묶음 1건'으로 따로 표시 (총액 제외)
    supplies = [r for r in all_rows if r['분류'] in ('재고·소모품', '기타')]
    rows = [r for r in all_rows if r not in fixed and r not in supplies]
    estimates = []
    with st.spinner('저장된 가격 DB와 보조 공매 기록을 확인하는 중...'):
        for row in rows:
            estimates.append(safe_estimate(row, price, cached_estimate))
    priced = [(r, e) for r, e in zip(rows, estimates) if e['mid'] is not None]
    low = sum(e['low'] * r['개수'] for r, e in priced)
    mid = sum(e['mid'] * r['개수'] for r, e in priced)
    high = sum(e['high'] * r['개수'] for r, e in priced)
    with st.container(key='estimate_summary'):
        presentation.estimate_label()
        if priced:
            # 최종 가격 하나만 크게 (계산 과정은 아래 '계산 과정·근거 보기'에서 원할 때만)
            st.markdown(f'### {mid:,}원')
            st.caption(f'가격이 나온 {len(priced)}개 품목 그룹의 예상 중고가 × 개수 합계 · 시세 및 판매처에 따라 차이가 있을 수 있습니다.')
            if len(priced) < len(rows):
                st.warning(f'부분 견적입니다. 가격 근거 부족 {len(rows) - len(priced)}개 품목 그룹은 합계에서 제외했습니다.')
            if len({e['price_basis'] for _, e in priced}) > 1:
                st.info('품목별 게시 가격과 공매 낙찰가 등 서로 다른 근거의 금액을 합산한 참고 합계입니다. 실제 매각액과 다를 수 있습니다.')
        else:
            st.markdown('### — 원 ~ — 원')
            st.caption('가격 근거가 있는 품목이 없습니다. 아래 검색 링크로 직접 확인해 주세요.')
    st.image(draw_boxes(last['image'], last['det']['objects'], last['box_names']),
             caption='수정 내용이 반영된 최종 분석 사진', use_container_width=True)
    st.subheader('최종 집기 목록')
    columns = ['집기명', '제조사', '개수', '모델명', '연식(년)']  # 사양 칸은 화면에서 제거 (2026-11-14, 내부 값은 모델명에서 자동 추출)
    table = pd.DataFrame([{k: row.get(k, '') for k in columns} for row in rows], columns=columns)
    edited = last.get('edited_makers', set())
    basis = last.get('brand_basis', {})
    table['제조사 근거'] = ['사용자 입력' if any(i in edited for i in r['box_ids']) else
                       next((basis[i] for i in r['box_ids'] if basis.get(i)), '') for r in rows]
    st.dataframe(table, hide_index=True, use_container_width=True)
    if fixed:
        st.caption('고정 설비로 분류되어 견적에서 제외: '
                   + ', '.join(f"{r['집기명']} {r['개수']}" for r in fixed)
                   + ' (떼어 팔 수 있다면 3단계에서 이름을 바꿔 주세요)')
    if supplies:
        st.subheader('재고·소모품 묶음')
        st.caption('컵·원두·병 음료·소품 등은 개별 시세 대신 묶음 1건으로 정리합니다. 총 매각액에는 포함하지 않습니다.')
        st.dataframe(pd.DataFrame([{'품목': r['집기명'], '개수': r['개수'], '분류': r['분류']} for r in supplies]),
                     hide_index=True, use_container_width=True)
    st.subheader('물품별 가격 · 시세')
    e = estimates
    prices = table[['집기명', '제조사', '개수', '모델명', '연식(년)']].copy()
    prices['개당 예상 중고가 (원)'] = [x['mid'] for x in e]
    prices['예상 매각액 (원)'] = [x['mid'] * c if x['mid'] is not None else None for x, c in zip(e, prices['개수'])]
    money = st.column_config.NumberColumn(format='localized')  # 6,000,000 처럼 3자리마다 쉼표
    st.dataframe(prices, hide_index=True, use_container_width=True,
                 column_config={'개당 예상 중고가 (원)': money, '예상 매각액 (원)': money})
    st.caption('시세 및 판매처에 따라 차이가 있을 수 있습니다.')
    with st.expander('계산 과정·근거 보기'):
        for r, x in zip(rows, e):
            st.markdown(f"**{r['집기명']} · {r.get('제조사', '')} {r.get('모델명', '')}**".strip())
            lines = x.get('calc') or [x['source']]
            st.markdown('\n'.join(f'- {line}' for line in lines))
            if not str(r.get('연식(년)', '')).strip():
                st.caption('연식을 입력하면 신품가 기준 감가로 다시 계산합니다 (3단계 표의 연식(년) 칸).')
        st.caption('연식 감가 규칙: 1년 차 개봉 감가 -30%, 2년 차부터 매년 1년 차 가격의 10%씩 차감, 최저 신품가의 10%. '
                   '감가 속도의 근거: 법인세법 시행규칙 [별표5] 기구 및 비품 기준내용연수 5년. '
                   '신품가는 팀 조사 추정가이며, 개봉 감가 30%는 중고 시장 관행을 반영한 가정값입니다.')
    st.caption('현재 가격은 팀 조사 시세표(품목·제조사·대표 모델별 중고 시세 범위)에서만 냅니다. 모델 → 제조사 → 품목 평균 순서로 찾으며, 개별 매물 원문 링크가 없는 조사값이라 실제 매각액과 다를 수 있습니다. 개당 Q1(25%)·중앙값·Q3(75%) × 수량으로 계산합니다. 유효 표본 3건 미만은 가격 근거 부족으로 표시합니다. '
               'DB는 최근 180일의 실제 개별 매물만 사용하며 샘플·묶음은 제외합니다. '
               '제조사·모델이 없으면 신뢰도가 낮은 참고 시세입니다. 판매 완료 게시 가격도 실제 거래액을 보장하지 않습니다. '
               '보조 온비드 기록은 최근 2년의 공매 낙찰가로 일반 중고 시세보다 낮을 수 있습니다.')
    with st.expander('가격 근거 보기 (출처·원본 매물·수집일)'):
        ev = [{'집기명': r['집기명'], '입력 제조사': r['제조사'], **record}
              for r, x in zip(rows, e) for record in x['evidence']]
        ev_frame = pd.DataFrame(ev)
        for col in ('price', '낙찰가'):  # 근거 표의 가격도 3자리 쉼표로 (숫자형으로 맞춘 뒤 표시 형식만 지정)
            if col in ev_frame:
                ev_frame[col] = pd.to_numeric(ev_frame[col], errors='coerce')
        st.dataframe(ev_frame, hide_index=True, use_container_width=True,
                     column_config={'url': st.column_config.LinkColumn('원본 매물/검색', display_text='보기'),
                                    'price': st.column_config.NumberColumn('가격 (원)', format='localized'),
                                    '낙찰가': st.column_config.NumberColumn('낙찰가 (원)', format='localized')})
    export = prices
    if supplies:
        bundle = {'집기명': '재고·소모품 묶음 (' + ', '.join(f"{r['집기명']} {r['개수']}" for r in supplies) + ')',
                  '개수': 1, '근거': '총액 제외'}
        export = pd.concat([prices, pd.DataFrame([bundle])], ignore_index=True)
    st.download_button('최종 목록 CSV 내려받기', export.to_csv(index=False).encode('utf-8-sig'),
                       file_name='이어가게_최종목록.csv', mime='text/csv')
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 분석 내용 다시 수정', use_container_width=True):
            go_to(3)
        if right.button('시작 화면으로', use_container_width=True):
            go_to(1)


def edit_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    presentation.section_label(3, '분석 결과 확인 · 직접 수정')
    edit_page(last)
    edit_inventory_table(last)
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 사진 입력으로', use_container_width=True):
            go_to(2)
        if right.button('수정 완료 · 최종 견적 보기 →', type='primary', use_container_width=True):
            go_to(4)


def final_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    final_page(last)


# ---------------- 화면 ----------------
PAGES = [st.Page(start_page, title='시작', default=True),
         st.Page(photo_page, title='사진 입력', url_path='photo'),
         st.Page(edit_screen, title='분석·수정', url_path='edit'),
         st.Page(final_screen, title='최종 견적', url_path='estimate')]
navigation = st.navigation(PAGES, position='hidden')
presentation.masthead()
st.session_state.setdefault('runs', 0)
steps = ['시작', '사진 입력', '분석·수정', '최종 견적']
step = steps.index(navigation.title) + 1
presentation.stage_navigation(step, steps)
with st.container(key=f'page_{step}'):
    navigation.run()
presentation.footer()
st.caption('사진과 수정 내용은 현재 세션에서만 사용하며, 새로고침하거나 접속을 종료하면 초기화될 수 있습니다.')

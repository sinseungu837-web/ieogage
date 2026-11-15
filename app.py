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
import disposal_view
from disposal import row_key, sync_states
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
        p_label = "➕ 새 물품 추가 위치"
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

    st.header("분석된 물품을 확인하고, 수정해 주세요")
    st.caption("아래 사진은 AI가 판매할 수 있는 물품의 영역을 탐지한 것입니다.")
    mode = st.radio(
        "물품 수정", ["기존 박스 선택", "새 물건 영역 그리기"], horizontal=True,
        key=f"canvas_mode_{last['key']}",
        format_func=lambda m: "기존 영역 수정 : 사진 위 박스를 눌러 선택" if m == "기존 박스 선택"
        else "새로운 영역 추가 : 사진 위에 사각형을 그려 추가",
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
    st.caption(f"현재 물품 {len(rows)}종 · 총 {len(box_names)}개 박스")
    if last['det'].get('truncated'):
        st.caption('물체가 많아 일부 누락될 수 있습니다. 놓친 물건은 직접 추가해 주세요.')

    # 영역을 그리면 바로 아래에서 이름을 넣고 추가 (예전엔 두 번째 탭 안에 있어 "드래그가 안 된다"로 보였음, 2026-11-15)
    if last.get("pending_box") and mode == "새 물건 영역 그리기":
        with st.container(border=True):
            st.success("영역을 선택했습니다. 무엇인지 적고 [추가]를 누르세요. 다시 그리면 영역이 바뀝니다.")
            q_name = st.text_input("추가할 물건", key=f"quick_add_{last['key']}_{last.get('canvas_revision', 0)}",
                                   placeholder="예: 테이블, 제빙기, 커피 그라인더")
            q_add, q_cancel = st.columns(2)
            if q_add.button("➕ 이 영역에 추가", type="primary", use_container_width=True, key=f"quick_add_btn_{last['key']}"):
                cleaned_name = normalize_natural_input(q_name)
                if not cleaned_name:
                    st.warning("추가할 물건의 이름을 입력해 주세요.")
                else:
                    new_id = last["next_id"]
                    objects.append({"id": new_id, "label": cleaned_name,
                                    "box": [float(v) for v in last["pending_box"]], "task": "USER"})
                    box_names[new_id] = cleaned_name
                    last["next_id"] = new_id + 1
                    last["pending_box"] = None
                    last["canvas_revision"] = last.get("canvas_revision", 0) + 1
                    st.toast(f"[#{new_id}] '{cleaned_name}' 을(를) 추가했습니다.")
                    st.rerun()
            if q_cancel.button("영역 지우기", use_container_width=True, key=f"quick_cancel_{last['key']}"):
                last["pending_box"] = None
                last["canvas_revision"] = last.get("canvas_revision", 0) + 1
                st.rerun()

    if mode == "새 물건 영역 그리기":
        if not last.get("pending_box"):
            st.info("사진 위에서 추가할 물건을 감싸도록 사각형을 그려 주세요.")
        return
    # ① 기존 영역 수정: 박스 선택 → 자연어로 이름 수정 / 박스 삭제
    active_ids = sorted(box_names.keys())
    if not active_ids:
        st.info("표시된 물품 박스가 없습니다. '새로운 영역 추가'로 물품을 추가해 주세요.")
        return
    default_idx = active_ids.index(last["selected_box_id"]) if last.get("selected_box_id") in active_ids else 0
    sel_id = st.selectbox("수정할 박스 (사진 위 박스를 눌러도 선택됩니다)", key=f"edit_box_{last['key']}",
                          options=active_ids, index=default_idx, format_func=lambda x: f"[#{x}] {box_names[x]}")
    last["selected_box_id"] = sel_id
    new_label_raw = st.text_input("올바른 물품명 (자연어로 입력)", value=box_names[sel_id],
                                  placeholder="예: 커피 그라인더, 이건 제빙기야", key=f"edit_input_{sel_id}")
    with st.container(key="edit_box_actions"):
        b_rename, b_delete = st.columns(2)
        if b_rename.button("이름 수정", type="primary", use_container_width=True):
            cleaned = normalize_natural_input(new_label_raw)
            if cleaned:
                box_names[sel_id] = cleaned
                st.toast(f"[#{sel_id}] 품목이 '{cleaned}'(으)로 수정되었습니다.")
                st.rerun()
        if b_delete.button("박스 삭제", use_container_width=True):
            removed_name = box_names.pop(sel_id, "")
            last["selected_box_id"] = None
            st.toast(f"[#{sel_id}] '{removed_name}' 박스를 삭제했습니다.")
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
        if '개수' in fields and fields['개수']:
            last.setdefault('count_override', {})[row['box_ids'][0]] = max(1, int(fields['개수']))
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
        st.header('남은 물품의 가치를 찾고, 새로운 시작을 준비하세요.')
        st.markdown('매장 사진 한 장으로 물품을 파악하고, 예상 중고 시세와 판매 경로까지 확인하세요.')
        presentation.step_list(['사진 업로드', '물품 탐색 및 수정', '최종 견적 확인', '판매 준비'])
    with st.container(key='start_actions'):
        if st.button('⇨ 이어가기', type='primary', use_container_width=True):
            go_to(2)
        if st.session_state.get('last') and st.button('이전 분석 이어서 보기', use_container_width=True):
            go_to(3)


def photo_page():
    st.header('사진을 준비해 주세요')
    st.caption('물품이 잘 보이도록 매장을 넓게 찍어 주세요.')
    methods = ['사진 올리기', '카메라로 찍기']
    source = st.radio('사진 입력', methods, index=methods.index(st.session_state.get('input_method', methods[0])), horizontal=True)
    st.session_state.input_method = source
    # iPhone 문제 대응 (2026-11-15): ① 확장자만 지정하면 iOS 사진 선택 창에서 완료가 안 되는 경우가 있어 accept 를 image/* 로 바꾸고
    # HEIC 도 받는다 ② 카메라는 페이지 안 작은 웹캠(st.camera_input) 대신 휴대폰 기본 카메라 앱을 연다 (capture=environment)
    photo_types = ['jpg', 'jpeg', 'png', 'heic', 'heif', 'webp']
    if source == methods[0]:
        file = st.file_uploader('매장 사진', type=photo_types, accept_multiple_files=False, key='photo_upload')
    else:
        file = st.file_uploader('카메라로 찍기 (휴대폰은 기본 카메라 앱이 열립니다)', type=photo_types,
                                accept_multiple_files=False, key='camera_upload')
    presentation.native_photo_inputs()
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
                    st.error('사진을 읽을 수 없습니다. JPG·PNG·HEIC 사진을 다시 선택해 주세요.')
                else:
                    st.session_state.photo_draft = {'image': image, 'file_id': file_id}
    if 'photo_draft' not in st.session_state and st.session_state.get('last'):
        st.session_state.photo_draft = {'image': st.session_state.last['image']}
    draft = st.session_state.get('photo_draft')
    if draft:
        image = draft['image']
        if st.button('✕ 사진 지우기', key='clear_photo', help='다른 사진을 올리려면 지우고 다시 선택하세요.'):
            st.session_state.pop('photo_draft', None)
            for k in ('photo_upload', 'camera_upload'):
                st.session_state.pop(k, None)
            st.rerun()
        st.image(image, caption='업로드한 사진', use_container_width=True)
        st.caption('사진이 옆으로 누웠거나 뒤집혀 있으면 똑바로 돌린 뒤 분석하세요.')
        with st.container(key='rotate_buttons'):  # 모바일에서도 두 버튼을 한 줄에 (styles/fieldsignal.css)
            rot_left, rot_right = st.columns(2)
            if rot_left.button('↺ 왼쪽으로 90° 돌리기', use_container_width=True):
                draft['image'] = image.rotate(90, expand=True)
                st.rerun()
            if rot_right.button('↻ 오른쪽으로 90° 돌리기', use_container_width=True):
                draft['image'] = image.rotate(-90, expand=True)
                st.rerun()
        import hashlib
        # 개수 세는 방식 선택은 팀 요청으로 화면에서 제거 (2026-11-15). 중복 제거 규칙 하나로 고정
        count_mode = 'dedupe'
        key = hashlib.md5(image.tobytes()).hexdigest()
        already = st.session_state.get('last', {}).get('key') == key
        if already:
            st.caption('이 사진의 분석 결과가 있습니다. 기존 수정 내용을 이어서 확인할 수 있어요.')
        limit = st.session_state.get('runs', 0) >= MAX_RUNS_PER_SESSION
        with st.container(key='page_actions'):
            back, run = st.columns(2)
            if back.button('← 이전 화면', use_container_width=True):
                go_to(1)
            if run.button('분석 결과 이어보기 →' if already else 'AI 분석하기 →', type='primary',
                          use_container_width=True, disabled=limit and not already):
                if already:
                    go_to(3)
                if not llm.get_api_key():
                    st.error('서버에 AI 키가 설정되지 않았습니다. 관리자에게 문의하세요.')
                else:
                    analyze_photo(image, key, count_mode)
        if limit:
            st.warning('이 화면에서 가능한 분석 횟수를 모두 사용했습니다.')
    else:
        if st.button('← 이전 화면'):
            go_to(1)


def edit_inventory_table(last):
    st.header('분석된 물품을 확인하고, 수정해 주세요')
    st.caption('아래 표는 AI가 정리한 물품 목록입니다. 올바르게 입력되었는지 확인하고, 물품의 연식을 입력해 주세요. '
               '제조사와 모델명은 AI가 추정한 정보입니다.')
    import datetime as _dt
    opened = st.date_input('이 가게를 개업한 날짜가 어떻게 되나요?', value=last.get('opened_at'),
                           min_value=_dt.date(1990, 1, 1), max_value=_dt.date.today(), format='YYYY-MM-DD',
                           key=f"opened_{last['key']}")
    last['opened_at'] = opened
    if opened:
        st.caption(f'연식을 비워 둔 물품은 개업일 기준 {opening_years(last):g}년으로 계산합니다.')
    rows = inventory_rows(last)
    if not rows:
        st.info('목록이 비어 있습니다. 이전 화면에서 사진에 영역을 그려 물품을 추가할 수 있습니다.')
        return
    table = pd.DataFrame([{k: row.get(k, '') for k in ('집기명', '개수', '연식(년)', '제조사', '모델명')} for row in rows])
    widget_key = f"inventory_{last['key']}_{last.get('table_revision', 0)}"
    st.data_editor(table, hide_index=True, use_container_width=True, num_rows='fixed',
                   key=widget_key, on_change=apply_inventory_edit, args=(last, rows, widget_key),
                   column_config={'집기명': st.column_config.TextColumn('물품명', required=True, width=66),
                                  '개수': st.column_config.NumberColumn(min_value=1, step=1, format='%d개', width=50),
                                  '연식(년)': st.column_config.TextColumn('연식', width=58,
                                                                        help='몇 년 사용했는지 숫자 (1년 미만은 0.5). 비우면 개업일 기준'),
                                  '제조사': st.column_config.TextColumn(width=80),
                                  '모델명': st.column_config.TextColumn()})
    st.caption('표 안의 내용을 눌러 직접 수정할 수 있습니다.')


def opening_years(last):
    opened = last.get('opened_at')
    if not opened:
        return None
    import datetime as _dt
    return max(0.5, round((_dt.date.today() - opened).days / 365, 1))


def priced_rows(last):
    """견적·판매 준비에 쓰는 물품 행. 연식이 비면 개업일 기준 연식을 채운다."""
    rows, fixed, supplies = disposal_rows(last)
    years = opening_years(last)
    for r in rows:
        if not str(r.get('연식(년)', '')).strip() and years is not None:
            r['연식(년)'] = f'{years:g}'
            r['연식 근거'] = '개업일 기준'
    return rows, fixed, supplies


@st.cache_data(ttl=3600, show_spinner=False)
def cached_estimate(name: str, manufacturer='', model='', spec='', condition='', revision=None) -> dict:
    # DB 교체 즉시 캐시 키가 바뀐다. 수집은 이 함수나 업로드 과정에서 실행하지 않는다.
    return price.service_estimate(name, manufacturer, model, spec, condition)


def disposal_rows(last):
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
    return rows, fixed, supplies


def final_page(last):
    presentation.section_label(3, '최종 견적 확인')
    st.header('우리 가게가 이어갈 가치')
    rows, fixed, supplies = priced_rows(last)
    with st.spinner('시세표에서 가격을 찾는 중...'):
        estimates = [safe_estimate(row, price, cached_estimate) for row in rows]
    priced = [(r, e) for r, e in zip(rows, estimates) if e['mid'] is not None]
    low = sum(e['low'] * r['개수'] for r, e in priced)
    mid = sum(e['mid'] * r['개수'] for r, e in priced)
    high = sum(e['high'] * r['개수'] for r, e in priced)
    with st.container(key='estimate_summary', border=True):
        if priced:
            st.markdown(f'### 약 {mid:,}원')
            st.markdown(f'**{low:,}원 ~ {high:,}원** (물품별 시세 범위 합계)')
            st.caption('팀 조사 시세(전문 커뮤니티 장터·B2B 중고주방·개인 직거래 시세 종합)에 기반한 것이며, '
                       '시세 및 판매처에 따라 차이가 있을 수 있습니다.')
            if len(priced) < len(rows):
                st.warning(f'가격 정보가 없는 {len(rows) - len(priced)}개 품목은 합계에서 제외했습니다.')
        else:
            st.markdown('### — 원')
            st.caption('가격 정보가 있는 물품이 없습니다.')
    with st.expander('최종 탐지 결과 확인 (분석된 사진)'):
        st.image(draw_boxes(last['image'], last['det']['objects'], last['box_names']), use_container_width=True)
    e = estimates
    table = pd.DataFrame([{k: row.get(k, '') for k in ('집기명', '개수', '연식(년)', '제조사', '모델명')} for row in rows])
    prices = table.copy()
    prices['개당 예상 중고가 (원)'] = [x['mid'] for x in e]
    prices['개당 범위'] = [f"{x['low']:,}~{x['high']:,}" if x['low'] is not None else '' for x in e]
    prices['합계 (원)'] = [x['mid'] * c if x['mid'] is not None else None for x, c in zip(e, prices['개수'])]
    head_l, head_r = st.columns([3, 2], vertical_alignment='bottom')
    head_l.subheader('물품별 가격 · 시세')
    head_r.download_button('CSV 내려받기', prices.rename(columns={'집기명': '물품명', '개당 범위': '범위(원)'}).to_csv(index=False).encode('utf-8-sig'),
                           file_name='이어가게_최종목록.csv', mime='text/csv', use_container_width=True)
    st.dataframe(prices[['집기명', '개수', '개당 예상 중고가 (원)', '개당 범위', '합계 (원)']], hide_index=True, use_container_width=True,
                 column_config={'집기명': st.column_config.TextColumn('물품명', width=90),
                                '개수': st.column_config.NumberColumn('수량', width=48),
                                '개당 예상 중고가 (원)': st.column_config.NumberColumn('개당(원)', width=95, format='localized'),
                                '개당 범위': st.column_config.TextColumn('범위(원)', width=180),
                                '합계 (원)': st.column_config.NumberColumn('합계(원)', width=100, format='localized')})
    if fixed:
        st.caption('고정 설비로 분류되어 견적에서 제외: ' + ', '.join(f"{r['집기명']} {r['개수']}" for r in fixed))
    if supplies:
        st.caption('재고·소모품은 개별 시세 없이 제외: ' + ', '.join(f"{r['집기명']} {r['개수']}" for r in supplies))
    with st.expander('가격 근거 보기'):
        for r, x in zip(rows, e):
            st.markdown(f"**{r['집기명']} · {r.get('제조사', '')} {r.get('모델명', '')}**".strip())
            lines = list(x.get('calc') or [x['source']])
            if r.get('연식 근거'):
                lines.insert(0, f"연식 {r['연식(년)']}년 ({r['연식 근거']})")
            st.markdown('\n'.join(f'- {line}' for line in lines))
        st.caption('산정 방식: 모델별 중고 시세 범위를 기본으로, 연식 1년 이하는 범위 상단·5년은 하단·그 사이는 비례 위치, '
                   '5년 초과는 하단에서 해마다 10%씩 추가 하락(최저 신품가의 10%). 5년 기준: 법인세법 시행규칙 [별표5] 기구 및 비품 기준내용연수. '
                   '시세는 팀 조사 추정값이며 개별 매물 원문 링크는 없습니다.')
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 이전 화면', use_container_width=True):
            go_to(4)
        if right.button('이어가기 →', type='primary', use_container_width=True):
            go_to(6)


def sale_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    presentation.section_label(4, '판매 준비')
    st.header('나의 물품 이어가기')
    st.caption('물품을 확인하고, 판매처를 알고 싶은 물품의 [확인하기] 버튼을 눌러 주세요.')
    region = st.text_input('현재 가게의 소재지를 입력해 주세요 (지역명 또는 주소)', value=last.get('vendor_region', ''),
                           placeholder='예: 서울 마포구', key=f"region_{last['key']}")
    last['vendor_region'] = region.strip()
    st.caption('서울 마포구, 경기 양평군, 전남 순천시 등 지역명 또는 주소를 입력하세요. 입력한 지역 근처의 업체를 카카오맵에서 찾아 드립니다.')
    rows, _, _ = priced_rows(last)
    if not rows:
        st.info('판매할 물품이 없습니다. 2단계에서 물품을 확인해 주세요.')
    for i, r in enumerate(rows):
        est = safe_estimate(r, price, cached_estimate)
        with st.container(border=True, key=f'sale_row_{i}'):
            info, action = st.columns([3, 1], vertical_alignment='center')
            ident = ' '.join(x for x in (r.get('제조사', ''), r.get('모델명', '')) if x and x != '미확인')
            price_txt = f" · 약 {est['mid']:,}원" if est.get('mid') else ''
            info.markdown(f"**{r['집기명']}** {r['개수']}개{price_txt}  \n{ident}")
            if action.button('확인하기', key=f'vendor_btn_{i}', use_container_width=True,
                             disabled=not last['vendor_region']):
                last['vendor_item'] = {'집기명': r['집기명'], '개수': r['개수'], '제조사': r.get('제조사', ''),
                                       '모델명': r.get('모델명', '')}
                go_to(7)
    if rows and not last['vendor_region']:
        st.caption('소재지를 입력하면 [확인하기] 버튼이 활성화됩니다.')
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 이전 화면', use_container_width=True):
            go_to(5)
        if right.button('처음 화면으로 돌아가기', use_container_width=True):
            go_to(1)


def vendor_screen():
    last = st.session_state.get('last')
    if not last or not last.get('vendor_item'):
        go_to(6)
    import vendors
    item, region = last['vendor_item'], last.get('vendor_region', '')
    presentation.section_label(4, '판매 준비')
    res = cached_vendor_search(item['집기명'], region)
    st.caption(f"{res['area']} 기준")
    st.header(f"[{item['집기명']}] 판매처를 추천해 드려요")
    if res['places']:
        st.caption(f"카카오맵에서 '{'·'.join(res['queries'])}'(으)로 찾은 근처 업체입니다. 제휴·보증 업체가 아닙니다.")
        for n, p in enumerate(res['places'], 1):
            with st.container(border=True):
                phone = f" ({p['phone']})" if p['phone'] else ''
                st.markdown(f"**{n}. {p['name']}**{phone}  \n주소: {p['address']} · {p['distance_km']}km  \n"
                            f"[카카오맵에서 보기 ↗]({p['url']})")
    if res['reason']:
        st.info(res['reason'])
    st.markdown('지도에서 직접 찾기: ' + ' · '.join(
        f"[카카오맵 '{q}']({vendors.map_link(region, q)}) / [네이버 지도 '{q}']({vendors.naver_link(region, q)})"
        for q in res['queries']))
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 이전 화면', use_container_width=True):
            go_to(6)
        if right.button('처음 화면으로 돌아가기', use_container_width=True):
            go_to(1)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_vendor_search(item, region):
    import vendors
    return vendors.search(item, region)


def edit_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    presentation.section_label(2, '물품 탐색 및 수정')
    edit_page(last)
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 이전 화면', use_container_width=True):
            go_to(2)
        if right.button('수정 완료 →', type='primary', use_container_width=True):
            go_to(4)


def items_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    presentation.section_label(2, '물품 탐색 및 수정')
    edit_inventory_table(last)
    with st.container(key='page_actions'):
        left, right = st.columns(2)
        if left.button('← 이전 화면', use_container_width=True):
            go_to(3)
        if right.button('최종 견적 확인 →', type='primary', use_container_width=True):
            go_to(5)


def final_screen():
    last = st.session_state.get('last')
    if not last:
        go_to(2)
    final_page(last)


# ---------------- 화면 ----------------
PAGES = [st.Page(start_page, title='시작', default=True),
         st.Page(photo_page, title='사진 업로드', url_path='photo'),
         st.Page(edit_screen, title='물품 탐색 및 수정', url_path='edit'),
         st.Page(items_screen, title='물품 목록 확인', url_path='items'),
         st.Page(final_screen, title='최종 견적 확인', url_path='estimate'),
         st.Page(sale_screen, title='판매 준비', url_path='sale'),
         st.Page(vendor_screen, title='판매처 추천', url_path='vendor')]
navigation = st.navigation(PAGES, position='hidden')
presentation.masthead()
st.session_state.setdefault('runs', 0)
steps = ['사진 업로드', '물품 탐색 및 수정', '최종 견적 확인', '판매 준비']
STEP_OF = {'사진 업로드': 1, '물품 탐색 및 수정': 2, '물품 목록 확인': 2, '최종 견적 확인': 3, '판매 준비': 4, '판매처 추천': 4}
step = STEP_OF.get(navigation.title, 0)
presentation.stage_navigation(step, steps)
with st.container(key=f'page_{step}'):
    navigation.run()
presentation.footer()
st.caption('사진과 수정 내용은 현재 세션에서만 사용하며, 새로고침하거나 접속을 종료하면 초기화될 수 있습니다.')

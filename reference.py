"""팀 조사 시세표 (data/reference_prices.csv, 2026-11-12 팀 결정).

서비스는 이 표에 있는 품목만 찾고, 제조사·모델은 이 표의 후보 중에서 고르며, 가격도 이 표에서만 낸다.
표의 가격은 팀이 조사한 범위값이고 개별 매물 원문 링크가 없으므로 화면에 '팀 조사 시세표'로 표시한다.
"""
from __future__ import annotations

import csv
import re
import statistics
from functools import lru_cache
from pathlib import Path

import listings

PATH = Path(__file__).parent / 'data' / 'reference_prices.csv'
SURVEYED_AT = '2026-11-12'
# 탐지·사용자 입력 이름 → 시세표 품목 이름
ALIAS = {'커피머신': '에스프레소 머신', '커피 머신': '에스프레소 머신', '에스프레소머신': '에스프레소 머신',
         '그라인더': '커피 그라인더', '커피그라인더': '커피 그라인더', '상업용 블렌더': '블렌더',
         '냉장고': '업소용 냉장고', '테이블 냉장고': '업소용 냉장고', '업소용 냉동고': '업소용 냉장고', '냉동고': '업소용 냉장고',
         '수직형 냉장고': '업소용 냉장고', '쇼케이스': '쇼케이스 냉장고', '디스펜서': '온수기', '핫워터 디스펜서': '온수기',
         '스텐 작업대': '작업대', '스텐 싱크대': '싱크대', '스툴': '의자', '카페 의자': '의자', '카페 테이블': '테이블',
         '천장형 에어컨': '에어컨', '냉난방기': '에어컨', '스피커/음향': '스피커',
         'TV': '디지털 메뉴보드', '메뉴판 TV': '디지털 메뉴보드', '메뉴보드': '디지털 메뉴보드', '모니터': '디지털 메뉴보드',
         '펜던트 조명': '조명', '조명 스탠드': '조명', '포스': '포스기', '콤비 오븐': '오븐', '아이패드': '태블릿', '태블릿/메뉴판': '태블릿',
         '와플메이커': '와플 메이커', '크로플 메이커': '와플 메이커', '믹서': '스탠드 믹서', '영수증프린터': '영수증 프린터'}


@lru_cache(maxsize=4)
def _rows(path, mtime_ns):
    """mtime 이 캐시 키 → CSV 가 바뀌면 다시 읽음 (서버가 켜진 채 git pull 로 표만 바뀌어도 반영).
    2026-11-13 버그: 예전엔 처음 1회만 읽어, 배포 후에도 지운 '디자인가구(일반)' 행이 계속 쓰였음."""
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ('used_low', 'used_high'):
            r[k] = int(r[k])
        r['new_price'] = int(r['new_price']) if str(r.get('new_price', '')).strip() else None  # 신품가 미조사 행 허용
    return tuple(rows)


def rows():
    return list(_rows(PATH, PATH.stat().st_mtime_ns))


def item_key(name):
    text = re.sub(r'\s+', ' ', str(name or '')).strip()
    return ALIAS.get(text, text)


def items():
    return list(dict.fromkeys(r['item'] for r in rows()))


def candidates(item):
    return [r for r in rows() if r['item'] == item_key(item)]


def _clean(value):
    text = re.sub(r'\((추정|형태 추정|로고)\)', '', str(value or '')).strip()
    return '' if text.startswith('미확인') else text


def _base_model(model):
    return listings.token(re.sub(r'\(.*?\)', '', model))


def match_maker(item, value):
    """입력 제조사를 시세표의 제조사 이름으로 맞춘다 (한글·영문 별칭). 없으면 ''."""
    v = listings.token(_clean(value))
    if not v:
        return ''
    for r in candidates(item):
        names = {listings.token(r['manufacturer'])} | ({listings.token(r['maker_alias'])} if r['maker_alias'] else set())
        if v in names or any(n and (n in v or v in n) for n in names):
            return r['manufacturer']
    return ''


def match_model(item, maker, value):
    v = listings.token(_clean(value))
    if not v:
        return ''
    for r in candidates(item):
        if maker and r['manufacturer'] != maker:
            continue
        names = {_base_model(r['model'])} | {listings.token(x) for x in re.findall(r'\((.*?)\)', r['model'])}  # '독스타 (DOCKSTA)' 영문도
        if any(b and len(b) >= 2 and (b in v or v in b) for b in names):
            return r['model']
    return ''


RANGE_YEARS = (1.0, 5.0)  # 1년 이하 → 범위 상단, 5년 → 범위 하단 (기구·비품 기준내용연수 5년)
OVER_RATE = 0.10          # 5년 초과: 범위 하단에서 해마다 10%씩 추가 하락
FLOOR_RATE = 0.10         # 최저: 신품가의 10%
LIFE_BASIS = '법인세법 시행규칙 [별표5] 기구 및 비품 기준내용연수 5년'
DATA_BASIS = '팀 조사 시세 (전문 커뮤니티 장터의 브랜드별 가격 방어율 · B2B 중고주방 시세 · 개인 직거래 시세 종합)'


def parse_years(value):
    """'3', '3년', '1년 미만' → 연식(년, 실수). 모르면 None."""
    text = str(value or '').strip()
    if not text:
        return None
    if '미만' in text:
        return 0.5
    m = re.search(r'\d+(?:\.\d+)?', text)
    return float(m.group()) if m else None


def position_by_age(low, high, years, new_price=None):
    """연식으로 시세 범위 안의 대표가를 정한다 (범위 위로는 벗어나지 않음). 반환: (대표가, 하한, 상한, 설명)."""
    y1, y5 = RANGE_YEARS
    if years <= y1:
        return high, low, high, f'연식 {years:g}년(1년 이하) → 시세 범위 상단'
    if years <= y5:
        mid = high - (high - low) * (years - y1) / (y5 - y1)
        return int(round(mid, -3)), low, high, f'연식 {years:g}년 → 1년(상단)~5년(하단) 사이 비례 위치'
    factor = max(0.0, 1 - OVER_RATE * (years - y5))
    floor = int(new_price * FLOOR_RATE) if new_price else 0
    lo2, hi2 = max(floor, int(low * factor)), max(floor, int(low))
    mid = max(floor, int(round(low * factor, -3)))
    note = f'연식 {years:g}년(5년 초과) → 범위 하단에서 해마다 {OVER_RATE:.0%}씩 추가 하락'
    if floor and mid == floor:
        note += f', 최저 신품가의 {FLOOR_RATE:.0%} 적용'
    return mid, min(lo2, mid), max(hi2, mid), note


def estimate(name, manufacturer='', model='', spec='', years=None):
    """모델 → 제조사 → 품목 순서로 시세표 행을 고르고, 시세 범위(하한~상한)와 대표가를 낸다.
    연식이 있으면 범위 안에서 위치를 정하고, 없으면 범위 중간값. 시세표에 없는 품목이면 None."""
    rs = candidates(name)
    if not rs:
        return None
    maker = match_maker(name, manufacturer)
    exact = match_model(name, maker, model)
    if exact:
        sel, match, conf = [r for r in rs if r['model'] == exact], '동일 모델', '보통'
    elif maker:
        sel, match, conf = [r for r in rs if r['manufacturer'] == maker], '동일 제조사 모델 평균', '낮음'
    else:
        sel, match, conf = rs, '품목 전체 모델 평균 (제조사 미확인)', '낮음'
    if spec and len(sel) > 1:
        same = [r for r in sel if r['spec'] and listings.spec_key(r['spec']) == listings.spec_key(spec)]
        sel = same or sel
    if len(sel) == 1:
        low, high = sel[0]['used_low'], sel[0]['used_high']
    else:  # 여러 모델: 하한들의 하위 25%~상한들의 상위 25% 대신 중앙값으로 (이상치 영향 줄임)
        low = int(statistics.median(r['used_low'] for r in sel))
        high = int(statistics.median(r['used_high'] for r in sel))
        low, high = min(low, high), max(low, high)
    news = [r['new_price'] for r in sel if r['new_price']]
    new = int(statistics.median(news)) if news else None
    basis = (f"{sel[0]['manufacturer']} {sel[0]['model']}" if len(sel) == 1 else f"{match} {len(sel)}개 모델")
    calc = [f"기준: {basis}" + (f" (신품가 {new:,}원)" if new else ""),
            f"중고 시세 범위: {low:,} ~ {high:,}원" + (f" · {sel[0]['note']}" if len(sel) == 1 and sel[0]['note'] else "")]
    if years is not None:
        mid, low, high, how = position_by_age(low, high, years, new)
        calc.append(how)
    else:
        mid = int(round((low + high) / 2, -3))
        calc.append('연식 미입력 → 범위 중간값 (2단계 표에 연식을 넣으면 범위 안에서 위치를 정합니다)')
    mid = min(max(mid, low), high)
    calc.append(f"예상 중고가 {mid:,}원 (범위 {low:,} ~ {high:,}원)")
    calc.append(f"근거: {DATA_BASIS}")
    return {'low': low, 'mid': mid, 'high': high, 'n': len(sel), 'price_basis': 'reference',
            'confidence': conf, 'match': match + ' (팀 조사 시세표)', 'collected_at': SURVEYED_AT,
            'source': f"{DATA_BASIS} · {basis}", 'calc': calc,
            'search_url': 'https://search.daum.net/search?q=' + re.sub(r'\s+', '+', f"{basis} 중고"),
            'evidence': [{'title': f"{r['manufacturer']} {r['model']}", 'price': (r['used_low'] + r['used_high']) // 2,
                          '중고 시세 범위': f"{r['used_low']:,} ~ {r['used_high']:,}", '신품 추정가': r['new_price'],
                          'source': '팀 조사 시세표', '비고': r['note']} for r in sel]}


def prompt_table():
    """Gemini 탐지 프롬프트용: 품목별 제조사·모델 후보."""
    lines = []
    for item in items():
        cands = ' / '.join(dict.fromkeys(f"{r['manufacturer']}{'(' + r['maker_alias'] + ')' if r['maker_alias'] else ''}: {r['model']}"
                                         for r in candidates(item)))  # 같은 모델 중복 행은 한 번만
        lines.append(f'- {item}: {cands}')
    return '\n'.join(lines)

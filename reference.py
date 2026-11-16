"""팀 조사 시세표 (data/reference_prices.csv, 2026-11-12 팀 결정).

서비스는 이 표에 있는 품목만 찾고, 제조사·모델은 이 표의 후보 중에서 고르며, 가격도 이 표에서만 낸다.
표의 가격은 팀이 조사한 범위값이고 개별 매물 원문 링크가 없으므로 화면에 '팀 조사 시세표'로 표시한다.
"""
from __future__ import annotations

import csv
import math
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


def find_product(manufacturer, model):
    """품목 이름과 상관없이 제조사+모델명이 시세표의 한 제품과 맞으면 그 행들을 돌려준다 (2026-11-16).
    예: 물품명을 '주방기기'로 적었거나 AI 가 다른 품목으로 잡아도, 제조사 '훼마'·모델 'E98 UP' 이면 그 제품 시세로 계산."""
    m, v = listings.token(_clean(manufacturer)), listings.token(_clean(model))
    if len(m) < 2 or len(v) < 2:
        return []
    hits = []
    for r in rows():
        makers = {listings.token(r['manufacturer'])} | ({listings.token(r['maker_alias'])} if r['maker_alias'] else set())
        if not any(n and (n == m or n in m or m in n) for n in makers):
            continue
        names = {_base_model(r['model'])} | {listings.token(x) for x in re.findall(r'\((.*?)\)', r['model'])}
        if any(b and len(b) >= 2 and (b in v or v in b) for b in names):
            hits.append((min(abs(len(b) - len(v)) for b in names if b), r))
    if not hits:
        return []
    best = min(d for d, _ in hits)  # 'E65S' 입력이면 'E65S GbW' 보다 'E65S' 가 가까움
    model_name = next(r['model'] for d, r in hits if d == best)
    return [r for _, r in hits if r['model'] == model_name]


# 2026-11-16 법령 기반으로 교체 (팀 가정 0개):
#  - 거래사례(중고 시세)로 평가: 감정평가 실무기준(국토교통부 고시) 630 기계기구류 1.3 ②
#  - 기구·비품 기준내용연수 5년: 법인세법 시행령 제28조, 시행규칙 제15조
#  - 정액법(매년 균등), 개월 단위·1개월 미만은 1개월: 법인세법 시행령 제26조 제2항 제1호·제9항
#  - 내용연수 경과 후 감가 종료: 같은 조 제6항·제7항
LIFE_YEARS = 5
LIFE_BASIS = ('감정평가 실무기준 630 1.3②(거래사례 기준) · 법인세법 시행령 제26조(정액법·월할)·'
              '제28조(기구·비품 기준내용연수 5년)')
DATA_BASIS = '팀 조사 시세 (전문 커뮤니티 장터의 브랜드별 가격 방어율 · B2B 중고주방 시세 · 개인 직거래 시세 종합)'


def parse_years(value):
    """'3', '3년', '1년 1개월', '13개월', '1년 미만' → 연식(년, 실수). 모르면 None."""
    text = str(value or '').strip()
    if not text:
        return None
    if '미만' in text:
        return 0.5
    if '개월' in text:  # '1년 1개월' · '13개월'
        y = re.search(r'(\d+(?:\.\d+)?)\s*년', text)
        mo = re.search(r'(\d+)\s*개월', text)
        return (float(y.group(1)) if y else 0.0) + int(mo.group(1)) / 12
    m = re.search(r'\d+(?:\.\d+)?', text)
    return float(m.group()) if m else None


def months_of(years):
    """연식(년) → 개월 수. 1개월 미만은 1개월 (법인세법 시행령 제26조 제9항)."""
    return max(1, math.ceil(round(years * 12, 6)))


def position_by_age(low, high, years, new_price=None):
    """정액법: 시세 상한에서 5년 동안 매년 균등하게 하한까지, 5년 이후 하한. 반환: (대표가, 하한, 상한, 설명).
    new_price 는 계산에 쓰지 않는다 (호출 호환용)."""
    months = months_of(years)
    if months >= LIFE_YEARS * 12:
        return low, low, high, (f'사용 {months}개월(5년 이상) → 내용연수 경과로 감가 종료, 시세 하한 '
                                '(실제 매입가는 하한보다 낮을 수 있음)')
    mid = high - (high - low) * months / (LIFE_YEARS * 12)
    return int(round(mid, -3)), low, high, f'사용 {months}개월 → 상한에서 5년간 매년 균등 감가(정액법)'


def estimate(name, manufacturer='', model='', spec='', years=None):
    """모델 → 제조사 → 품목 순서로 시세표 행을 고르고, 시세 범위(하한~상한)와 대표가를 낸다.
    연식이 있으면 범위 안에서 위치를 정하고, 없으면 범위 중간값. 시세표에 없는 품목이면 None."""
    rs = candidates(name)
    maker = match_maker(name, manufacturer) if rs else ''
    exact = match_model(name, maker, model) if rs else ''
    product = [] if exact else find_product(manufacturer, model)
    if not rs and not product:
        return None
    if exact:
        sel, match, conf = [r for r in rs if r['model'] == exact], '동일 모델', '보통'
    elif product:  # 품목 이름은 달라도 입력한 제조사·모델이 시세표 제품과 일치
        sel, match, conf = product, '입력한 제조사·모델과 동일 제품', '보통'
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
        calc.append('연식 미입력 → 범위 중간값 (2단계 표에 연식이나 개업일을 넣으면 연식대로 계산합니다)')
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

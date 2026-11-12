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
         '냉장고': '업소용 냉장고', '테이블 냉장고': '업소용 냉장고', '업소용 냉동고': '업소용 냉장고',
         '쇼케이스': '쇼케이스 냉장고', '디스펜서': '온수기', '핫워터 디스펜서': '온수기',
         '싱크대': '작업대', '스텐 작업대': '작업대', '스툴': '의자', '카페 의자': '의자',
         '천장형 에어컨': '에어컨', '냉난방기': '에어컨', '스피커/음향': '스피커'}


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


def estimate(name, manufacturer='', model='', spec=''):
    """모델 → 제조사 → 품목 순서로 시세표 행을 고른다. 시세표에 없는 품목이면 None."""
    rs = candidates(name)
    if not rs:
        return None
    maker = match_maker(name, manufacturer)
    exact = match_model(name, maker, model)
    if exact:
        sel, match, conf = [r for r in rs if r['model'] == exact], '동일 모델 (팀 조사 시세표)', '보통'
    elif maker:
        sel, match, conf = [r for r in rs if r['manufacturer'] == maker], '동일 제조사 대표 모델 (팀 조사 시세표)', '낮음'
    else:
        sel, match, conf = rs, '품목 대표 모델 평균, 제조사 미확인 (팀 조사 시세표)', '낮음'
    if spec and len(sel) > 1:
        same = [r for r in sel if r['spec'] and listings.spec_key(r['spec']) == listings.spec_key(spec)]
        sel = same or sel
    if len(sel) > 1 and all(r['used_low'] == r['used_high'] for r in sel):
        # 게시가 1건씩인 행 여러 개 (통합 시세표) → 가격들의 Q1·중앙값·Q3 로 범위
        vals = sorted(r['used_low'] for r in sel)
        q1, _, q3 = statistics.quantiles(vals, n=4, method='inclusive')
        low, mid, high = int(q1), int(statistics.median(vals)), int(q3)
    else:
        low = int(statistics.median(r['used_low'] for r in sel))
        high = int(statistics.median(r['used_high'] for r in sel))
        mid = int(statistics.median((r['used_low'] + r['used_high']) / 2 for r in sel))
    mid = min(max(mid, low), high)
    sources = sorted({s.strip() for r in sel for s in r['survey_sources'].split(',') if s.strip()})
    return {'low': low, 'mid': mid, 'high': high, 'n': len(sel), 'price_basis': 'reference',
            'confidence': conf, 'match': match, 'collected_at': SURVEYED_AT,
            'source': f"팀 조사 시세표 {len(sel)}개 모델 · 개별 매물 원문 링크 없음 · 조사 출처: {', '.join(sources)}",
            'search_url': 'https://search.daum.net/search?q=' + re.sub(r'\s+', '+', f'{name} 중고'),
            'evidence': [{'title': f"{r['manufacturer']} {r['model']}", 'price': (r['used_low'] + r['used_high']) // 2,
                          '중고 시세 범위': f"{r['used_low']:,} ~ {r['used_high']:,}", '신품 추정가': r['new_price'],
                          'source': '팀 조사 시세표', '조사 출처': r['survey_sources'], '비고': r['note']} for r in sel]}


def prompt_table():
    """Gemini 탐지 프롬프트용: 품목별 제조사·모델 후보."""
    lines = []
    for item in items():
        cands = ' / '.join(dict.fromkeys(f"{r['manufacturer']}{'(' + r['maker_alias'] + ')' if r['maker_alias'] else ''}: {r['model']}"
                                         for r in candidates(item)))  # 같은 모델 중복 행은 한 번만
        lines.append(f'- {item}: {cands}')
    return '\n'.join(lines)

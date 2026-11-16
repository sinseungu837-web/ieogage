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
    if not m or len(v) < 2:  # 제조사는 한 글자('톤')도 허용, 모델명은 2글자 이상
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


# 2026-11-16 팀 지시문: 법정 정률법 (법인세법 시행령 제26조 제2항 제2호, 시행규칙 별표4 상각률)
#  - 품목별 자산 분류·기준내용연수·상각률은 data/asset_classes.csv (confirmed=Y 인 품목만 적용)
#  - 잔존가액: 미상각잔액이 처음 취득가액의 5% 이하가 되는 해에 상각 종료 (시행령 제26조 제6·7항)
#  - 1년 미만 기간은 월수 비례, 1개월 미만은 1개월 (시행령 제26조 제8·9항)
#  - 분류 미확정·신품가 없음·상각 종료·연식 미입력 → 조사 중고 시세 중간값 (L + U) ÷ 2
ASSET_PATH = Path(__file__).parent / 'data' / 'asset_classes.csv'
RESIDUAL_RATE = 0.05   # 시행령 제26조 제6항 "취득가액의 100분의 5"
NOTICE = '본 가격은 법정 감가상각률을 참고하여 산출한 예상가격으로, 실제 거래가격 또는 공인 감정평가액과 다를 수 있습니다.'
DATA_BASIS = '팀 조사 시세 (전문 커뮤니티 장터의 브랜드별 가격 방어율 · B2B 중고주방 시세 · 개인 직거래 시세 종합)'


def parse_years(value):
    """'3', '3년', '1년 1개월', '13개월', '1년 미만' → 연식(년, 실수). 모르면 None."""
    text = str(value or '').strip()
    if not text or text.startswith('-'):  # 음수 연식은 무효
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


@lru_cache(maxsize=4)
def _assets(path, mtime_ns):
    with open(path, encoding='utf-8-sig') as f:
        return {r['item']: r for r in csv.DictReader(f)}


def asset_class(item):
    """품목의 법정 자산 분류. 분류가 확인된(confirmed=Y) 품목만 dict, 아니면 None."""
    try:
        row = _assets(ASSET_PATH, ASSET_PATH.stat().st_mtime_ns).get(item_key(item))
    except OSError:
        return None
    if not row or row.get('confirmed') != 'Y':
        return None
    return {'class': row['asset_class'], 'life': int(row['life_years']), 'rate': float(row['db_rate']), 'law': row['law'],
            # 내용연수범위(기준내용연수 ± 25%, 시행령 제28조 제1항 제2호)의 양 끝 상각률 (별표4)
            'life_min': int(row['life_min']), 'life_max': int(row['life_max']),
            'rate_short': float(row['rate_short']), 'rate_long': float(row['rate_long'])}


def book_value(new, rate, months):
    """정률법 미상각잔액(원). 상각 종료 시 시행령 제26조 제7항의 장부가액(취득가액 5%와 1천원 중 적은 금액)."""
    ratio = declining_balance(rate, months)
    return min(new * RESIDUAL_RATE, 1000) if ratio is None else new * ratio


def declining_balance(rate, months):
    """정률법 미상각잔액 비율. 12개월 단위로 잔액 × 상각률, 남은 개월은 월수 비례(× 개월 ÷ 12).
    잔액이 처음 취득가액의 5% 이하가 되는 기간에 상각 종료 → None 반환 (시행령 제26조 제6·7항)."""
    full, rest = divmod(months, 12)
    ratio = 1.0
    for m in [12] * full + ([rest] if rest else []):
        ratio -= ratio * rate * m / 12
        if ratio <= RESIDUAL_RATE:
            return None
    return ratio


def _round_won(value):
    return int(round(value, -3)) if value >= 10000 else int(round(value, -2))


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
    survey_mid = _round_won((low + high) / 2)
    asset = asset_class(sel[0]['item'])
    method, law = '조사 시세 중간값', ''
    if years is None:
        mid, why = survey_mid, '연식·개업일 미입력'
    elif not asset:
        mid, why = survey_mid, '법정 자산 분류 미확정 품목'
    elif not new:
        mid, why = survey_mid, '신품가 없음'
    else:
        months = months_of(years)
        ratio = declining_balance(asset['rate'], months)
        if ratio is None:
            mid, why = survey_mid, f'사용 {months}개월 시점에 법정 상각 종료(잔액 ≤ 취득가액 5%)'
        else:
            mid = _round_won(new * ratio)
            # 범위: 내용연수범위 양 끝(짧은 연수 = 큰 상각률 → 하한, 긴 연수 = 작은 상각률 → 상한)
            low = _round_won(book_value(new, asset['rate_short'], months))
            high = _round_won(book_value(new, asset['rate_long'], months))
            method, law = '법정 정률법', f"{asset['law']} · 법인세법 시행령 제26조·제28조"
            full, rest = divmod(months, 12)

            def expr(r):
                return f"{new:,} × (1 − {r})^{full}" + (f" × (1 − {r} × {rest}/12)" if rest else '')
            short_end = declining_balance(asset['rate_short'], months) is None
            calc = [
                f"기준 제품: {basis}",
                f"취득가액(신품가): {new:,}원",
                f"경과기간: {months}개월 ({full}년" + (f" {rest}개월)" if rest else ")") + " — 1월 미만의 일수는 1월로 계산 (법인세법 시행령 제26조 제9항)",
                f"자산 구분: {asset['class']} — 법인세법 시행규칙 [별표 5] 구분 1 '공구, 기구, 금형 및 비품'",
                f"기준내용연수 {asset['life']}년, 내용연수범위 {asset['life_min']}~{asset['life_max']}년 — "
                "'기준내용연수에 그 기준내용연수의 100분의 25를 가감'한 범위 (법인세법 시행령 제28조 제1항 제2호)",
                "상각방법: 정률법 — 미상각잔액(취득가액에서 이미 감가상각비로 손금에 산입한 금액을 공제한 잔액)'에 해당 자산의 "
                "내용연수에 따른 상각률을 곱하여 계산한 각 사업연도의 상각범위액이 매년 체감되는 상각방법' (법인세법 시행령 제26조 제2항 제2호)",
                f"상각률 (법인세법 시행규칙 [별표 4] 감가상각자산의 상각률표, 정률법): "
                f"{asset['life_min']}년 {asset['rate_short']} · {asset['life']}년 {asset['rate']} · {asset['life_max']}년 {asset['rate_long']}",
                "산출식: 미상각잔액 = 취득가액 × (1 − 상각률)^경과연수" + (" × (1 − 상각률 × 잔여월수 ÷ 12)" if rest else ""),
                f"예상가격(기준내용연수 {asset['life']}년): {expr(asset['rate'])} = 약 {mid:,}원",
                f"범위 상한(내용연수 {asset['life_max']}년): {expr(asset['rate_long'])} = 약 {high:,}원",
                f"범위 하한(내용연수 {asset['life_min']}년): "
                + (f"{asset['life_min']}년 기준으로는 상각 종료 → 장부가액 취득가액의 5%와 1천원 중 적은 금액 = {low:,}원 (같은 조 제7항)"
                   if short_end else f"{expr(asset['rate_short'])} = 약 {low:,}원"),
                "잔존가액: 정률법은 '취득가액의 100분의 5'를 잔존가액으로 하여 미상각잔액이 최초로 취득가액의 100분의 5 이하가 되는 "
                "사업연도에 상각을 마치며, 이후 장부가액은 '취득가액의 100분의 5와 1천원중 적은 금액' (법인세법 시행령 제26조 제6항·제7항)",
                f"유의: 세법상 감가상각자산의 미상각잔액(장부가액) 계산 방식을 중고 예상가격에 준용한 것입니다. {NOTICE}",
            ]
    if method == '조사 시세 중간값':
        calc.append(f"{why} → 조사 중고 시세 중간값 ({low:,} + {high:,}) ÷ 2 = {survey_mid:,}원")
        calc.append(f"근거: {DATA_BASIS}")
    return {'low': low, 'mid': mid, 'high': high, 'n': len(sel), 'price_basis': 'reference',
            'method': method, 'law': law, 'new_price': new,
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

"""사전 수집 CSV 우선 가격 조회와 온비드 공매 낙찰가 보조 조회.

숫자는 코드에서만 계산하며, 서로 다른 가격 성격은 합쳐 평균내지 않는다.
온비드 키는 DATA_GO_KR_KEY (.env 또는 Streamlit Secrets).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import urllib.parse
import urllib.request
from datetime import date, timedelta

ONBID_URL = "https://apis.data.go.kr/B010003/OnbidCltrBidRsltListSrvc2/getCltrBidRsltList2"
ALL_PRPT = "0007,0010,0005,0004,0002,0003,0006,0008,0011,0013"  # 재산유형 전체
LOOKBACK_DAYS = 730  # 최근 2년 낙찰만
MIN_SAMPLES = 3      # 이 미만이면 '추정 불가'
LIVE_SEARCH = True   # DB 근거 부족 시 헬로마켓 실시간 검색 보완 (등록 출처: data/sources.json)
MIN_WON, MAX_WON = 1_000, 50_000_000

# 품목명(한국어) → 온비드 물건명 검색어. 없으면 이름의 마지막 단어로 검색
KEYWORDS = {
    "에스프레소 머신": ["에스프레소", "커피머신"],
    "커피머신": ["커피머신", "커피메이커"],
    "커피 그라인더": ["그라인더"],
    "제빙기": ["제빙기"],
    "블렌더": ["블렌더"],
    "정수기": ["정수기"],
    "업소용 냉장고": ["냉장고"],
    "테이블 냉장고": ["냉장고"],
    "쇼케이스 냉장고": ["쇼케이스"],
    "업소용 냉동고": ["냉동고"],
    "식기세척기": ["식기세척기"],
    "전자레인지": ["전자레인지"],
    "오븐": ["오븐"],
    "의자": ["의자"],
    "스툴": ["스툴"],
    "테이블": ["테이블"],
    "소파": ["소파"],
    "선반": ["선반"],
    "수납장": ["수납장"],
    "포스기": ["포스", "POS"],
    "키오스크": ["키오스크"],
    "TV": ["TV", "텔레비전"],
    "모니터": ["모니터"],
    "디지털 메뉴보드": ["TV", "디스플레이"],
    "에어컨": ["에어컨"],
    "펜던트 조명": ["조명등", "펜던트"],
    "조명": ["조명등", "스탠드조명"],
    # (2026-11-12 보강) 검색어가 없거나 마지막 단어로 잘못 잡히던 품목
    "조명 스탠드": ["스탠드조명", "스탠드등", "플로어스탠드"],  # "스탠드" 만 쓰면 스탠드 에어컨이 잡힘
    "레일 조명": ["레일조명", "레일등"],
    "천장형 에어컨": ["시스템에어컨", "천장형에어컨", "천장형 냉난방기"],
    "하부장": ["하부장", "싱크대"],
    "싱크대": ["싱크대", "씽크대"],
    "작업대": ["작업대"],
    "카드 단말기": ["카드단말기", "카드결제기"],
    "거울": ["거울"],
    "선풍기": ["선풍기"],
}

# 개당 가격 상한 (수량 표시 없이 여러 개를 판 묶음이 섞이는 것을 막음). 없으면 MAX_WON
MAX_BY_NAME = {
    "의자": 300_000, "스툴": 200_000, "테이블": 600_000, "소파": 1_000_000, "꽃병": 100_000,
    "선반": 500_000, "펜던트 조명": 300_000, "조명 스탠드": 300_000, "레일 조명": 300_000, "거울": 200_000, "카드 단말기": 500_000, "선풍기": 300_000, "천장형 에어컨": 3_000_000, "조명": 300_000, "수납장": 800_000, "TV": 2_000_000, "모니터": 500_000, "디지털 메뉴보드": 2_000_000,
}

# 묶음 매각 표시 → 개당 가격을 알 수 없으므로 제외
BUNDLE = re.compile(r"\s외(\s|\d|$)|일괄|등\b|\d+\s*(종|점|개|대)|,|·|및|세트|폐자재|고철")
# 카페 집기와 무관한 같은 이름 물건
EXCLUDE = re.compile(r"약품|혈액|의료|실험|시약|검체|백신|머시닝|공작|선반기|밀링|피복기|차량|자동차|부품|원두|드립백"
                     r"|소모품|조명탑|가로등|컴퓨터|노트북|학생용|강당|시청각|폐|벼|원종|종자|고소작업|리프트|천장형\s*TV")


def _has_keyword(title: str, kw: str) -> bool:
    """영문 키워드(TV, POS)는 모델 코드 속 글자와 구분하려고 앞뒤가 영숫자가 아닐 때만 인정."""
    if re.fullmatch(r"[A-Za-z]+", kw):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])", title, re.I) is not None
    return kw in title
QTY = re.compile(r"\((\d+)\s*(대|개|점|EA|ea)\)")


def _key() -> str | None:
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("DATA_GO_KR_KEY")
        except Exception:
            key = None
    return key


def keywords_for(name: str) -> list[str]:
    if name in KEYWORDS:
        return KEYWORDS[name]
    words = name.split()
    return [words[-1]] if words else []


class OnbidAPIError(RuntimeError):
    pass


def _query(keyword: str, key: str) -> list[dict]:
    end = date.today() - timedelta(days=1)
    params = {
        "serviceKey": urllib.parse.unquote(key.strip()), "pageNo": 1, "numOfRows": 100, "resultType": "json",
        "cltrTypeCd": "0003", "dspsMthodCd": "0001", "prptDivCd": ALL_PRPT, "pbctStatCd": "0010",
        "opbdDtStart": (end - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
        "opbdDtEnd": end.strftime("%Y%m%d"), "onbidCltrNm": keyword,
    }
    with urllib.request.urlopen(ONBID_URL + "?" + urllib.parse.urlencode(params), timeout=15) as r:
        payload = json.load(r)
    payload = payload.get('response', payload)
    code = str((payload.get('header') or {}).get('resultCode', '00'))
    if code not in ('0', '00', '0000'):
        raise OnbidAPIError('API_ERROR_' + code if code.isdigit() else 'API_RESPONSE_ERROR')
    if 'body' not in payload:
        raise OnbidAPIError('API_RESPONSE_ERROR')
    body = payload['body']
    items = (body.get("items") or {})
    items = items.get("item", []) if isinstance(items, dict) else items
    return items if isinstance(items, list) else [items]


def _unit_price(raw) -> int | None:
    """'835100(1대)' / '4200(8개)' / 650000 → 개당 가격. 읽을 수 없으면 None."""
    s = str(raw or "").replace(",", "")
    m = re.match(r"\s*(\d+)", s)
    if not m:
        return None
    amount = int(m.group(1))
    q = QTY.search(s)
    qty = int(q.group(1)) if q else 1
    return amount // max(qty, 1)


def evidence(name: str, diagnostics=None) -> list[dict]:
    """품목의 개별 매각 낙찰 기록 (묶음·무관 물건 제외)."""
    key = _key()
    if not key:
        if diagnostics is not None:
            diagnostics.append({'item': name, 'status': 'MISSING_API_KEY'})
        return []
    rows, seen = [], set()
    cap = MAX_BY_NAME.get(name, MAX_WON)
    for kw in keywords_for(name):
        try:
            items = _query(kw, key)
        except Exception as exc:
            if diagnostics is not None:
                status = str(exc) if isinstance(exc, OnbidAPIError) else ('HTTP_' + str(exc.code) if hasattr(exc, 'code') else type(exc).__name__)
                diagnostics.append({'item': name, 'keyword': kw, 'status': status})
            continue
        accepted_before = len(rows)
        excluded_reasons = {}
        def rejected(reason):
            excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
        for x in items:
            title = (x.get("onbidCltrNm") or "").strip()
            mng = x.get("cltrMngNo")
            if not title or not mng:
                rejected('missing_title_or_id')
                continue
            if mng in seen:
                rejected('duplicate_id')
                continue
            if not _has_keyword(title, kw):
                rejected('keyword_mismatch')
                continue
            if BUNDLE.search(title):
                rejected('bundle_title')
                continue
            if EXCLUDE.search(title):
                rejected('irrelevant_title')
                continue
            price = _unit_price(x.get("scfbAmt"))
            if price is None or not MIN_WON <= price <= cap:
                rejected('invalid_price_or_item_price_cap')
                continue
            seen.add(mng)
            rows.append({"물건명": title, "낙찰가": price, "개찰일": str(x.get("cltrOpbdDt") or "")[:8],
                         "물건관리번호": mng})
        if diagnostics is not None:
            diagnostics.append({'item': name, 'keyword': kw, 'status': 'OK',
                                'api_records': len(items), 'accepted': len(rows) - accepted_before,
                                'excluded': len(items) - (len(rows) - accepted_before),
                                'exclusion_reasons': excluded_reasons, 'first_page_limit_reached': len(items) >= 100})
    return rows


def _drop_outliers(vals: list[int]) -> list[int]:
    if len(vals) < 4:
        return vals
    q1, _, q3 = statistics.quantiles(vals, n=4)
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return [v for v in vals if lo <= v <= hi]


def onbid_estimate(name: str, manufacturer="", model="", spec="", condition="") -> dict:
    """{'low','mid','high','n','source','evidence','search_url'}. 근거 부족이면 low/mid/high=None."""
    rows = evidence(name)
    from listings import maker, token
    # 온비드에는 별도 제조사/모델 열이 없어 제목으로 확인 가능한 기록만 쓴다.
    for value in (manufacturer if maker(manufacturer) else '', model, spec, condition):
        if value:
            rows = [r for r in rows if token(value) in token(r['물건명'])]
    vals = _drop_outliers(sorted(r["낙찰가"] for r in rows))
    search_url = "https://search.daum.net/search?q=" + urllib.parse.quote(f"{name} 중고")
    if len(vals) < MIN_SAMPLES:
        return {"low": None, "mid": None, "high": None, "n": len(vals),
                "source": "근거 부족 (온비드 개별 낙찰 3건 미만)", "evidence": rows, "search_url": search_url}
    import pandas as pd
    q1, med, q3 = pd.Series(vals).quantile([.25, .5, .75])
    return {"low": int(q1), "mid": int(statistics.median(vals)), "high": int(q3), "n": len(vals),
            "source": f"온비드 공매 개별 낙찰 {len(vals)}건 (최근 2년)", "evidence": rows, "search_url": search_url}


# 최후 예비: 조달청 나라장터쇼핑몰 신품 계약단가 × 감가 (공공데이터포털 15129471, 같은 DATA_GO_KR_KEY)
# 가전(냉장고·제빙기·커피머신)은 이 API에 개별 단가가 없어 가구·오븐·모니터만 대상.
PPS_URL = "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getShoppingMallPrdctInfoList"
PPS_ITEMS = {"의자": "의자", "스툴": "의자", "테이블": "테이블", "소파": "소파", "오븐": "오븐",
             "모니터": "모니터", "디지털 메뉴보드": "모니터",
             # (2026-11-12 확대) 조달청 품명 확인: 캐비닛 91건·책장 99건. 가전·조명은 단가 미제공
             "수납장": "캐비닛", "하부장": "캐비닛", "선반": "책장"}
# 연식을 모를 때의 잔존가 비율 (배민외식업광장 폐업 컨설턴트 기준: 2년 내 40~50%, 5년 내 20~30%, 5년+ 10% 이하)
DEPRECIATION = (0.2, 0.3, 0.5)


def vendor_new_price_estimate(name: str) -> dict:
    """업체 신품 판매가(data/new_prices.csv, 등록 출처) 중앙값 × 잔존율. 조달청에 없는 쇼케이스·냉동고용."""
    import csv
    from pathlib import Path
    path = Path(__file__).parent / "data" / "new_prices.csv"
    if not path.exists():
        return empty_estimate(name)
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("item") == name and str(r.get("price", "")).isdigit()]
    vals = _drop_outliers(sorted(int(r["price"]) for r in rows))
    if len(vals) < MIN_SAMPLES:
        return empty_estimate(name, n=len(vals))
    med = statistics.median(vals)
    lo, mi, hi = DEPRECIATION
    src = rows[0]["source"]
    return {**empty_estimate(name), "low": int(med * lo), "mid": int(med * mi), "high": int(med * hi), "n": len(vals),
            "source": f"신품가 기준 추정 · {src} 신품 판매가 {len(vals)}건 중앙값 {int(med):,}원 × 잔존율 20~50%",
            "match": "품목 신품가 × 감가 (연식 미확인)", "confidence": "낮음", "price_basis": "new_depreciated",
            "collected_at": rows[0]["collected_at"],
            "evidence": [{"title": r["title"], "price": int(r["price"]), "source": r["source"], "url": r["url"]} for r in rows[:20]]}


def new_price_estimate(name: str) -> dict:
    key, word = _key(), PPS_ITEMS.get(name)
    if not key or not word:
        return vendor_new_price_estimate(name)
    end = date.today()
    params = {"serviceKey": key, "type": "json", "pageNo": 1, "numOfRows": 100, "inqryDiv": "1",
              "inqryBgnDt": (end - timedelta(days=3 * 365)).strftime("%Y%m%d0000"),
              "inqryEndDt": end.strftime("%Y%m%d0000"), "prdctClsfcNoNm": word}
    with urllib.request.urlopen(PPS_URL + "?" + urllib.parse.urlencode(params), timeout=15) as r:
        body = json.load(r)["response"]["body"]
    items = body.get("items") or []
    items = items if isinstance(items, list) else items.get("item", [])
    vals = _drop_outliers(sorted(int(x["cntrctPrceAmt"]) for x in items
                                 if str(x.get("cntrctPrceAmt", "")).isdigit() and x.get("prdctUnit") in ("개", "대", "EA")))
    if len(vals) < MIN_SAMPLES:
        return empty_estimate(name, n=len(vals))
    med = statistics.median(vals)
    lo, mi, hi = DEPRECIATION
    return {**empty_estimate(name), "low": int(med * lo), "mid": int(med * mi), "high": int(med * hi), "n": len(vals),
            "source": f"신품가 기준 추정 · 조달청 쇼핑몰 신품 계약단가 {len(vals)}건 중앙값 {int(med):,}원 × 잔존율 20~50%",
            "match": "품목 신품가 × 감가 (연식 미확인)", "confidence": "낮음", "price_basis": "new_depreciated",
            "collected_at": end.isoformat(),
            "evidence": [{"title": x.get("prdctSpecNm", ""), "price": x.get("cntrctPrceAmt"), "source": "조달청 나라장터쇼핑몰"}
                         for x in items[:20]]}


# 새 경로: 사전 수집 CSV 우선. 게시 가격과 공매 낙찰가는 결합하지 않는다.
def db_revision():
    from listings import LISTINGS
    try:
        stat = LISTINGS.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


REFERENCE_ONLY = True  # 2026-11-12 팀 결정: 서비스 화면 가격은 팀 조사 시세표에서만


def service_estimate(name: str, manufacturer='', model='', spec='', condition='') -> dict:
    """앱이 쓰는 가격 조회. 시세표 모드면 팀 조사 시세표만, 아니면 기존 DB·실시간·공매 경로."""
    if REFERENCE_ONLY:
        import reference
        # 3단계 표의 '연식(년)' 값이 condition 으로 들어온다
        return (reference.estimate(name, manufacturer, model, spec, reference.parse_years(condition))
                or empty_estimate(name, '팀 조사 시세표에 없는 품목'))
    return estimate(name, manufacturer, model, spec, condition)


def empty_estimate(name, reason='가격 근거 부족', n=0):
    return {'low': None, 'mid': None, 'high': None, 'n': n, 'source': reason,
            'evidence': [], 'search_url': 'https://search.daum.net/search?q=' +
            urllib.parse.quote(f'{name} 중고'), 'collected_at': '',
            'match': '', 'confidence': '낮음', 'price_basis': ''}


def estimate(name: str, manufacturer='', model='', spec='', condition='', *,
             listings_path=None, allow_onbid=True, diagnostics=None, allow_live=None):
    """최소 3건, Q1/중앙값/Q3. 샘플·180일 초과·묶음·외화 제외.

    모델/사양이 확인되면 맞지 않는 제품으로 표본을 채우지 않는다.
    동일 제조사·사양 fallback도 입력 사양이 명시된 경우에만 허용한다.
    """
    import listings
    brand = listings.maker(manufacturer)
    result = empty_estimate(name)
    def trace(stage, frame):
        if diagnostics is not None:
            diagnostics.append({'stage': stage, 'remaining': len(frame)})
    try:
        stored = listings.read(listings_path or listings.LISTINGS)
        trace('normalized_db', stored)
        data = listings.production(stored)
        trace('recent_real_records', data)
        data = data[data['item'] == listings.item(name)]
        trace('item', data)
        if brand:
            data = data[data.manufacturer == brand]
            trace('manufacturer', data)
        if condition:
            data = data[data.condition.map(listings.token) == listings.token(condition)]
            trace('condition', data)
        if model:
            exact = data[data.model.map(listings.token) == listings.token(model)]
            if not exact.empty:
                data = exact
                match = '동일 품목·제조사·모델' if brand else '동일 품목·모델 (제조사 미확인)'
            elif spec:
                data = data[data.spec.map(listings.spec_key) == listings.spec_key(spec)]
                match = '동일 품목·동일 사양' + ('·제조사' if brand else '')
            else:
                data = exact
                match = '동일 모델 근거 부족'
        else:
            match = '동일 품목·제조사 참고 시세' if brand else '품목만 확인된 참고 시세'
        trace('model_or_spec_fallback', data)
        if spec:
            data = data[data.spec.map(listings.spec_key) == listings.spec_key(spec)]
            match += '·동일 사양'
            trace('spec', data)
        if not data.empty:
            # 서로 다른 가격 성격을 섞지 않음. 충분한 게시 가격 우선, 없으면 가장 많은 군집.
            cohorts = []
            for basis in ('asking', 'sold_posted', 'auction'):
                groups = [g for _, g in data[data.price_basis == basis].groupby(
                    data[data.price_basis == basis].spec.map(listings.spec_key))]
                groups.sort(key=lambda g: (-len(g), not bool(g.spec.iloc[0])))
                cohorts.extend((basis, g) for g in groups)
            sufficient = [(b, g) for b, g in cohorts if len(g) >= MIN_SAMPLES]
            basis, selected = sufficient[0] if sufficient else max(cohorts, key=lambda pair: len(pair[1]))
            if not spec:
                match += ' · ' + ('동일 사양군: ' + selected.spec.iloc[0] if selected.spec.iloc[0] else '사양 미확인군')
            trace('price_basis_' + basis, selected)
            label = {'asking': '판매 중 매물의 게시 가격 기준 참고 시세',
                     'sold_posted': '판매 완료 매물의 게시 가격 (실제 거래액 미확인)',
                     'auction': '공매 낙찰가 기준 참고 시세'}[basis]
            result.update(n=len(selected), match=match, price_basis=basis,
                          source=label + ' · ' + ', '.join(sorted(selected.source.unique())),
                          collected_at=selected.collected_at.max(),
                          evidence=selected.to_dict('records'),
                          confidence='높음' if brand and model and selected.spec.ne('').all() and '동일 모델 근거 부족' not in match
                          and selected.model.map(listings.token).eq(listings.token(model)).all()
                          # 보통: 같은 제조사 매물만으로 계산 (모델 미확인 또는 사양 일치)
                          else '보통' if brand and selected.spec.ne('').all() and selected.manufacturer.eq(brand).all() else '낮음')
            if len(selected) >= MIN_SAMPLES:
                q1, mid, q3 = selected.price.astype(float).quantile([.25, .5, .75])
                result.update(low=int(q1), mid=int(mid), high=int(q3))
                return result
            result['source'] += ' · 가격 근거 부족 (3건 미만)'
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append({'stage': 'db_error', 'error': type(exc).__name__})
        result['source'] = '가격 DB 조회 오류 · 가격 근거 부족'
    if allow_live is None:
        # 외부 조회가 허용되고(allow_onbid) 서비스 키가 설정된 배포 환경에서만 실시간 검색 (테스트·키 없는 로컬은 끔)
        allow_live = LIVE_SEARCH and allow_onbid and bool(_key())
    if allow_live:
        # DB 근거가 부족하면 헬로마켓 검색 결과를 실시간으로 보완 (판매 중 게시 가격, 최근 180일). 앱에서만 켠다.
        try:
            import crawler
            live = listings.normalize(crawler.live_rows(listings.item(name)))
            # 입력한 조건(제조사·모델·용량)이 제목에 모두 있는 매물만. 3건 미만이면 다른 제품으로 채우지 않는다.
            if brand:
                names = {listings.token(brand)} | {k for k, v in listings.MAKERS.items() if v == brand}
                live = live[live.title.map(listings.token).map(lambda t: any(n in t for n in names))]
            if model:
                live = live[live.title.map(listings.token).str.contains(listings.token(model), regex=False)]
            if spec:
                live = live[live.title.map(listings.spec_key).str.contains(listings.spec_key(spec), regex=False)]
            if len(live) < MIN_SAMPLES:
                live = live.iloc[0:0]
            vals = _drop_outliers(sorted(int(v) for v in live.price))
            if len(vals) >= MIN_SAMPLES:
                q1, mid, q3 = statistics.quantiles(vals, n=4)
                return {**result, 'low': int(q1), 'mid': int(statistics.median(vals)), 'high': int(q3),
                        'n': len(vals), 'price_basis': 'asking', 'confidence': '낮음',
                        'match': ('동일 ' + '·'.join(k for k, v in (('제조사', brand), ('모델', model), ('사양', spec)) if v)
                                  if (brand or model or spec) else '품목만 확인된') + ' 실시간 검색',
                        'source': f'판매 중 매물의 게시 가격 기준 참고 시세 · 헬로마켓 실시간 검색 {len(vals)}건',
                        'collected_at': listings.today().isoformat(),
                        'evidence': live[live.price.isin(vals)].to_dict('records')}
        except Exception:
            pass
    if allow_onbid:
        try:
            fallback = onbid_estimate(name, manufacturer, model, spec, condition)
            as_onbid = {**result, **fallback, 'price_basis': 'auction',
                        'match': '온비드 제목 기반 참고 시세', 'confidence': '낮음',
                        'collected_at': listings.today().isoformat()}
            if fallback['mid'] is not None:
                return as_onbid
        except Exception:
            fallback, as_onbid = {'n': 0}, None
        try:  # 마지막 예비: 신품가 × 감가 (가구·오븐·모니터)
            newp = new_price_estimate(name)
            if newp['mid'] is not None:
                return newp
        except Exception:
            pass
        if as_onbid and not result['n'] and fallback['n']:
            return as_onbid
    return result

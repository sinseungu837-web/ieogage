"""품목별 참고 중고가: 온비드 공매 낙찰가(개별 매각만) → 없으면 '추정 불가' + 검색 링크.

숫자는 모두 코드가 계산한다 (LLM 미사용).
- 근거: 한국자산관리공사 차세대 온비드 물건 입찰결과목록 조회서비스 (공공데이터포털 15157252)
- 키: DATA_GO_KR_KEY (.env 또는 Streamlit Secrets)
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
    "조명": ["조명등", "스탠드"],
}

# 개당 가격 상한 (수량 표시 없이 여러 개를 판 묶음이 섞이는 것을 막음). 없으면 MAX_WON
MAX_BY_NAME = {
    "의자": 300_000, "스툴": 200_000, "테이블": 600_000, "소파": 1_000_000, "꽃병": 100_000,
    "선반": 500_000, "펜던트 조명": 300_000, "조명": 300_000, "수납장": 800_000, "TV": 2_000_000, "모니터": 500_000, "디지털 메뉴보드": 2_000_000,
}

# 묶음 매각 표시 → 개당 가격을 알 수 없으므로 제외
BUNDLE = re.compile(r"\s외(\s|\d|$)|일괄|등\b|\d+\s*(종|점|개|대)|,|·|및|세트|폐자재|고철")
# 카페 집기와 무관한 같은 이름 물건
EXCLUDE = re.compile(r"약품|혈액|의료|실험|시약|검체|백신|머시닝|공작|선반기|밀링|피복기|차량|자동차|부품|원두|드립백"
                     r"|소모품|조명탑|가로등|컴퓨터|노트북|학생용|강당|시청각|폐|벼|원종|종자")


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


def _query(keyword: str, key: str) -> list[dict]:
    end = date.today() - timedelta(days=1)
    params = {
        "serviceKey": key, "pageNo": 1, "numOfRows": 100, "resultType": "json",
        "cltrTypeCd": "0003", "prptDivCd": ALL_PRPT, "pbctStatCd": "0010",
        "opbdDtStart": (end - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
        "opbdDtEnd": end.strftime("%Y%m%d"), "onbidCltrNm": keyword,
    }
    with urllib.request.urlopen(ONBID_URL + "?" + urllib.parse.urlencode(params), timeout=15) as r:
        body = json.load(r).get("body", {})
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


def evidence(name: str) -> list[dict]:
    """품목의 개별 매각 낙찰 기록 (묶음·무관 물건 제외)."""
    key = _key()
    if not key:
        return []
    rows, seen = [], set()
    cap = MAX_BY_NAME.get(name, MAX_WON)
    for kw in keywords_for(name):
        try:
            items = _query(kw, key)
        except Exception:
            continue
        for x in items:
            title = (x.get("onbidCltrNm") or "").strip()
            mng = x.get("cltrMngNo")
            if not title or mng in seen or not _has_keyword(title, kw):
                continue
            if BUNDLE.search(title) or EXCLUDE.search(title):
                continue
            price = _unit_price(x.get("scfbAmt"))
            if price is None or not MIN_WON <= price <= cap:
                continue
            seen.add(mng)
            rows.append({"물건명": title, "낙찰가": price, "개찰일": str(x.get("cltrOpbdDt") or "")[:8],
                         "물건관리번호": mng})
    return rows


def _drop_outliers(vals: list[int]) -> list[int]:
    if len(vals) < 4:
        return vals
    q1, _, q3 = statistics.quantiles(vals, n=4)
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return [v for v in vals if lo <= v <= hi]


def estimate(name: str) -> dict:
    """{'low','mid','high','n','source','evidence','search_url'}. 근거 부족이면 low/mid/high=None."""
    rows = evidence(name)
    vals = _drop_outliers(sorted(r["낙찰가"] for r in rows))
    search_url = "https://search.daum.net/search?q=" + urllib.parse.quote(f"{name} 중고")
    if len(vals) < MIN_SAMPLES:
        return {"low": None, "mid": None, "high": None, "n": len(vals),
                "source": "근거 부족 (온비드 개별 낙찰 3건 미만)", "evidence": rows, "search_url": search_url}
    q1, med, q3 = statistics.quantiles(vals, n=4)
    return {"low": int(q1), "mid": int(statistics.median(vals)), "high": int(q3), "n": len(vals),
            "source": f"온비드 공매 개별 낙찰 {len(vals)}건 (최근 2년)", "evidence": rows, "search_url": search_url}

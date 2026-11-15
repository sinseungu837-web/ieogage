"""전문업체 찾기: 카카오 로컬(장소 검색) API. 물품 → 업체 종류 검색어는 규칙(코드), 업체는 실제 검색 결과.

- 키: KAKAO_REST_KEY (.env 또는 Streamlit Secrets). 앱에 '카카오맵 API' 활성화 필요.
- 알선·보증 아님. 목록 + 카카오맵 장소 링크만 보여 준다 (다른 지도 위에 표시하지 않음).
- 검색어는 2026-11-15 성북구 반경 10km 실측으로 결과가 나온 것만 사용
  (예: '중고주방' 9건, '중고주방기기 매입'·'제빙기' 0건).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

API = 'https://dapi.kakao.com/v2/local/'
RADIUS_M = 10000
# 시세표 품목 → 찾을 업체 종류(검색어)
QUERIES = {
    '에스프레소 머신': ['중고주방', '커피머신'],
    '커피 그라인더': ['커피머신', '중고주방'],
    '블렌더': ['중고주방'],
    '제빙기': ['중고주방', '업소용냉장고'],
    '업소용 냉장고': ['중고주방', '업소용냉장고'],
    '쇼케이스 냉장고': ['중고주방', '업소용냉장고'],
    '오븐': ['중고주방'],
    '온수기': ['중고주방'],
    '의자': ['중고가구'],
    '테이블': ['중고가구'],
}
FALLBACK_QUERY = '중고주방'
DISPOSAL_QUERY = '폐업 철거'
# 검색어와 무관한 업종(예: '주방기기' 검색의 다이소)을 거르기 위한 업종 단어
GOOD_CATEGORY = ('중고', '주방', '가구', '전자제품', '냉난방', '시공', '철거', '렌탈', '재활용')


def api_key():
    key = os.environ.get('KAKAO_REST_KEY')
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get('KAKAO_REST_KEY')
        except Exception:
            key = None
    return key.strip() if isinstance(key, str) and key.strip() else None


def _call(path, key, **query):
    url = API + path + '?' + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={'Authorization': 'KakaoAK ' + key})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def locate(region, key):
    """'성북구', '서울 성북구 안암동' → (x, y, 표시 이름). 못 찾으면 None."""
    for path in ('search/address.json', 'search/keyword.json'):
        docs = _call(path, key, query=region, size=1).get('documents', [])
        if docs:
            d = docs[0]
            return d['x'], d['y'], d.get('address_name') or d.get('place_name') or region
    return None


def map_link(region, query):
    """API 를 못 쓸 때의 대안: 카카오맵 검색 결과 링크."""
    return 'https://map.kakao.com/link/search/' + urllib.parse.quote(f'{region} {query}'.strip())


def naver_link(region, query):
    return 'https://map.naver.com/p/search/' + urllib.parse.quote(f'{region} {query}'.strip())


def queries_for(item):
    from reference import item_key
    return QUERIES.get(item_key(item), [FALLBACK_QUERY])


def search(item, region, limit=3, key=None, disposal=False):
    """반환: {'ok': bool, 'places': [...], 'queries': [...], 'area': str, 'reason': str}"""
    key = key or api_key()
    queries = [DISPOSAL_QUERY] if disposal else queries_for(item)
    out = {'ok': False, 'places': [], 'queries': queries, 'area': region, 'reason': '',
           'links': [map_link(region, q) for q in queries]}
    if not region.strip():
        out['reason'] = '지역을 입력해 주세요.'
        return out
    if not key:
        out['reason'] = '업체 검색 키가 설정되지 않아 카카오맵 검색 링크로 안내합니다.'
        return out
    try:
        loc = locate(region, key)
        if not loc:
            out['reason'] = f"'{region}' 위치를 찾지 못했습니다. 구·동 이름으로 다시 입력해 주세요."
            return out
        x, y, out['area'] = loc
        seen = {}
        for q in queries:
            for d in _call('search/keyword.json', key, query=q, x=x, y=y, radius=RADIUS_M,
                           sort='accuracy', size=15).get('documents', []):
                if d['id'] in seen or not any(w in d.get('category_name', '') for w in GOOD_CATEGORY):
                    continue
                seen[d['id']] = {'name': d['place_name'], 'category': d['category_name'].split(' > ')[-1],
                                 'phone': d.get('phone', ''), 'address': d.get('road_address_name') or d.get('address_name', ''),
                                 'distance_km': round(int(d.get('distance') or 0) / 1000, 1),
                                 'url': d.get('place_url', ''), 'query': q}
        # 이름·업종에 '중고'가 있는 곳(매입 가능성 높음)을 먼저, 그다음 가까운 순
        out['places'] = sorted(seen.values(), key=lambda p: ('중고' not in p['name'] + p['category'], p['distance_km']))[:limit]
        out['ok'] = True
        if not out['places']:
            out['reason'] = '반경 10km 안에서 맞는 업체를 찾지 못했습니다. 아래 지도 검색 링크로 넓게 찾아보세요.'
    except urllib.error.HTTPError as exc:
        out['reason'] = f'업체 검색 서버 오류 ({exc.code}). 카카오맵 검색 링크로 안내합니다.'
    except Exception as exc:
        out['reason'] = f'업체 검색 실패 ({type(exc).__name__}). 카카오맵 검색 링크로 안내합니다.'
    return out


def quote_request(rows, estimates):
    """여러 업체에 똑같이 보낼 견적 요청서 (사실만, 코드가 조립)."""
    lines = ['[매입 견적 요청]', '카페 폐업으로 아래 집기의 일괄 매입 견적을 요청드립니다.', '']
    for r, e in zip(rows, estimates):
        ident = ' '.join(str(r.get(k, '')).strip() for k in ('제조사', '모델명') if r.get(k) and r[k] != '미확인')
        years = str(r.get('연식(년)', '')).strip()
        price = f" / 참고 중고가 개당 {e['mid']:,}원" if e.get('mid') else ''
        lines.append(f"- {r['집기명']}{' (' + ident + ')' if ident else ''} {r['개수']}개"
                     f"{' / 사용 ' + years + '년' if years else ''}{price}")
    lines += ['', '확인하고 싶은 점:', '1. 철거·운반 비용이 매입가에 포함되나요?',
              '2. 매입가는 어떤 기준으로 정하시나요? (연식·상태·브랜드)',
              '3. 방문 견적과 수거 가능한 날짜는 언제인가요?', '', '사진은 별도로 보내 드리겠습니다.']
    return '\n'.join(lines)

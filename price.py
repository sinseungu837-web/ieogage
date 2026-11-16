"""앱의 가격 조회 진입점. 계산은 reference.py (팀 조사 시세표 + 법정 정률법)가 한다.

숫자는 코드에서만 계산하며, AI 는 가격을 만들지 않는다.
"""
from __future__ import annotations

import urllib.parse

REFERENCE_ONLY = True  # 2026-11-12 팀 결정: 서비스 화면 가격은 팀 조사 시세표에서만


def service_estimate(name: str, manufacturer='', model='', spec='', condition='') -> dict:
    """앱이 쓰는 가격 조회. condition 에는 2단계 표의 '연식(년)' 값이 들어온다."""
    import reference
    return (reference.estimate(name, manufacturer, model, spec, reference.parse_years(condition))
            or empty_estimate(name, '팀 조사 시세표에 없는 품목'))


def db_revision():
    """앱 캐시 키: 시세표·자산 분류 파일이 바뀌면 가격을 다시 계산한다."""
    import reference
    try:
        return tuple(p.stat().st_mtime_ns for p in (reference.PATH, reference.ASSET_PATH))
    except OSError:
        return None


def empty_estimate(name, reason='가격 근거 부족', n=0):
    return {'low': None, 'mid': None, 'high': None, 'n': n, 'source': reason,
            'evidence': [], 'search_url': 'https://search.daum.net/search?q=' +
            urllib.parse.quote(f'{name} 중고'), 'collected_at': '',
            'match': '', 'confidence': '낮음', 'price_basis': ''}

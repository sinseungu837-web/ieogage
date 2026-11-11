"""가격 모듈의 실패가 최종 화면으로 전파되지 않도록 하는 표시 경계.

가격/DB/Streamlit을 import하지 않아 fallback이 조회 모듈에 의존하지 않는다.
"""
from __future__ import annotations

import logging
import math
from numbers import Real
from urllib.parse import quote

LOGGER = logging.getLogger(__name__)


def unavailable_estimate(name, reason='가격 근거 부족'):
    return {'low': None, 'mid': None, 'high': None, 'n': 0, 'source': reason,
            'evidence': [], 'search_url': 'https://search.daum.net/search?q=' + quote(f'{name} 중고'),
            'collected_at': '', 'match': '', 'confidence': '낮음', 'price_basis': ''}


def normalize_estimate(name, result):
    """과거 반환 형식의 누락된 표시 필드는 채우고, 계산 불가능한 가격은 거부한다."""
    if not isinstance(result, dict):
        raise ValueError('가격 결과 형식 오류')
    normalized = unavailable_estimate(name)
    for key in ('source', 'search_url', 'collected_at', 'match', 'confidence', 'price_basis'):
        value = result.get(key)
        if isinstance(value, str) and value:
            normalized[key] = value
    count = result.get('n', 0)
    if isinstance(count, bool) or not isinstance(count, Real) or not math.isfinite(count) or count < 0 or int(count) != count:
        raise ValueError('표본 수 형식 오류')
    normalized['n'] = int(count)
    evidence = result.get('evidence', [])
    if isinstance(evidence, list):
        normalized['evidence'] = [row for row in evidence if isinstance(row, dict)]
    values = [result.get(key) for key in ('low', 'mid', 'high')]
    if any(value is not None for value in values):
        if normalized['n'] < 3 or not all(isinstance(value, Real) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0 for value in values):
            raise ValueError('유효 가격 또는 표본 부족')
        if not values[0] <= values[1] <= values[2]:
            raise ValueError('가격 범위 순서 오류')
        normalized.update(zip(('low', 'mid', 'high'), (int(value) for value in values)))
    if normalized['mid'] is None and '가격 근거 부족' not in normalized['source']:
        normalized['source'] += ' · 가격 근거 부족'
    return normalized


def _log_failure(stage, backend, exc):
    # 예외 원문/traceback에는 serviceKey가 담긴 URL이 포함될 수 있다.
    LOGGER.warning('Price lookup unavailable: stage=%s error=%s module=%s functions=%s',
                   stage, type(exc).__name__, getattr(backend, '__file__', '<unknown>'),
                   {name: callable(getattr(backend, name, None))
                    for name in ('estimate', 'db_revision', 'empty_estimate')})


def safe_estimate(row, backend, lookup):
    """lookup은 앱의 캐시 함수를 주입한다. 품목별 실패를 별도로 격리한다."""
    name = row['집기명']
    revision = None
    try:
        revision_fn = getattr(backend, 'db_revision', None)
        if callable(revision_fn):
            revision = revision_fn()
    except Exception as exc:
        _log_failure('db_revision', backend, exc)
    try:
        result = lookup(name, row.get('제조사', ''), row.get('모델명', ''),
                        row.get('사양', ''), row.get('상태', ''), revision)
        return normalize_estimate(name, result)
    except Exception as exc:
        _log_failure('estimate', backend, exc)
        return unavailable_estimate(name, '가격 조회 실패 · 가격 근거 부족')

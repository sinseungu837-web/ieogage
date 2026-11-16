"""시세표 매칭용 문자열 정규화 (reference.py 가 사용)."""
from __future__ import annotations

import re

SPEC = re.compile(r'(\d{1,4})\s*(kg|키로|킬로|그룹|group|박스|mm)(?![a-z])', re.I)
SPEC_UNIT = {'키로': 'kg', '킬로': 'kg', 'group': '그룹'}


def token(value):
    """공백·밑줄·하이픈을 지우고 대소문자를 무시한 비교용 문자열."""
    return re.sub(r'[\s_\-]+', '', str(value or '')).casefold()


def spec_key(value):
    """'50키로'·'50 KG' → '50kg', '2 group' → '2그룹'. 단위가 없으면 공백만 제거한 원문."""
    text = str(value or '')
    if re.search(r'\d\s*[x×*]\s*\d', text, re.I):  # 1800×750mm 같은 치수는 통째로 비교
        return re.sub(r'[×*]', 'x', token(text)).removesuffix('mm')
    found = SPEC.findall(text)
    if not found:
        return token(value)
    return ','.join(sorted({n + SPEC_UNIT.get(u.lower(), u.lower()) for n, u in found}))

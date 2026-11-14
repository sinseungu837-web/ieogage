"""처분 상태와 판매글 근거. 탐지·가격 계산과 독립적인 순수 함수."""
import hashlib
import json

METHODS = ('미선택', '개인 중고거래', '전문업체 매각', '양도·나눔')


def row_key(row):
    return ','.join(str(i) for i in sorted(row['box_ids']))


def sync_states(last, rows):
    states = last.setdefault('disposal', {})
    active = {row_key(r) for r in rows}
    # 삭제·분할·병합한 묶음의 초안을 다른 물품에 자동 연결하지 않는다.
    previous = dict(states)
    for key in set(states) - active:
        last.setdefault('disposal_archive', {})[key] = states.pop(key)
    for row in rows:
        ancestors = [s for key, s in previous.items() if set(key.split(',')) & set(row_key(row).split(','))]
        decisions = {(s['selected'], s['method']) for s in ancestors}
        selected, method = next(iter(decisions)) if len(decisions) == 1 else (True, '미선택')
        states.setdefault(row_key(row), dict(selected=selected, method=method, price=None,
            price_initialized=False, condition='작동 여부 미확인', notes='', accessories='',
            title='', body='', generated_for=None, candidate=None))
    return states


def grouped(rows, states):
    return {method: [r for r in rows if states[row_key(r)]['selected']
                    and states[row_key(r)]['method'] == method] for method in METHODS}


def reference_total(rows, estimates, states):
    return sum(e['mid'] * r['개수'] for r, e in zip(rows, estimates)
               if states[row_key(r)]['selected'] and e['mid'] is not None)


def fingerprint(row, state, estimate):
    data = [row, {k: state[k] for k in ('price', 'condition', 'notes', 'accessories')}, estimate.get('mid')]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sale_facts(row, state):
    """금액과 수량은 코드가 확정. 모델은 이 문장들의 순서만 편집한다."""
    name = str(row['집기명'])
    identity = ' '.join(str(row.get(k, '')).strip() for k in ('제조사', '모델명')
                        if row.get(k) and row[k] != '미확인')
    titles = [f'{name} {row["개수"]}개 판매', f'{identity} {name} 판매'.strip()]
    facts = [f'{name} {row["개수"]}개 판매합니다.']
    if identity:
        facts.append(f'제조사·모델: {identity}')
    if str(row.get('연식(년)', '')).strip():
        facts.append(f'사용 연수: {row["연식(년)"]}년')
    facts.append(f'작동 상태: {state["condition"]}')
    if state['notes'].strip():
        facts.append('외관·특이사항: ' + state['notes'].strip())
    if state['accessories'].strip():
        facts.append('구성품·추가 정보: ' + state['accessories'].strip())
    facts.append(f'희망 판매가격 (전체 {row["개수"]}개 합계): {state["price"]:,}원'
                 if state['price'] is not None else '희망 판매가격: 미정 (판매자에게 확인해 주세요)')
    return titles, facts


def validate_draft(data, titles, facts):
    if not isinstance(data, dict):
        raise ValueError('판매글 응답 형식 오류')
    title, order = data.get('title_index'), data.get('sentence_order')
    if type(title) is not int or title not in range(len(titles)):
        raise ValueError('판매글 제목 형식 오류')
    if not isinstance(order, list) or any(type(i) is not int for i in order) or sorted(order) != list(range(len(facts))):
        raise ValueError('판매글 근거 누락 또는 허위 정보')
    return {'title': titles[title], 'body': '\n\n'.join(facts[i] for i in order)}

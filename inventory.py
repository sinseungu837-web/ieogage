"""수정된 박스를 기준으로 품목·제조사·모델·사양·상태·수량을 정리한다."""
from __future__ import annotations


def inventory_rows(last):
    brands = last.setdefault('manufacturers', {})
    groups = {}
    for obj in last['det']['objects']:
        oid = obj['id']
        if oid not in last['box_names']:
            continue
        name = last['box_names'][oid]
        maker = brands.get(oid, '').strip() or '미확인'
        details = tuple(str(last.get(key, {}).get(oid, '')).strip()
                        for key in ('models', 'specs', 'conditions'))
        groups.setdefault((name, maker, *details), []).append(oid)
    rows = []
    for (name, maker, model, spec, condition), ids in groups.items():
        row = {'집기명': name, '제조사': maker, '개수': len(ids), 'box_ids': ids}
        # 이전 세션과 호출자의 기본 행 형식 호환 유지.
        row.update({key: value for key, value in zip(('모델명', '사양', '상태'), (model, spec, condition)) if value})
        rows.append(row)
    return rows

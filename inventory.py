"""수정된 박스를 기준으로 품목·제조사·수량을 정리한다."""
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
        groups.setdefault((name, maker), []).append(oid)
    return [{'집기명': name, '제조사': maker, '개수': len(ids), 'box_ids': ids}
            for (name, maker), ids in groups.items()]

"""가격 CSV 원본을 보존하면서 검증한 스냅샷만 견적에 허용한다."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import listings

LEDGER = listings.BASE / 'listing_verifications.csv'
COLUMNS = ['url', 'snapshot_sha256', 'status', 'checked_at', 'availability',
           'working_status', 'published_at', 'verification_note']


def fingerprint(row):
    snapshot = {c: str(row.get(c, '')) for c in listings.COLUMNS}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def eligible(frame, ledger_path=LEDGER):
    """검증 당시의 가격/모델/상태가 바뀌면 재검증. 누락·만료·삭제는 제외."""
    if frame.empty:
        return frame
    try:
        ledger = pd.read_csv(ledger_path, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return frame.iloc[:0]
    if not set(COLUMNS) <= set(ledger.columns):
        return frame.iloc[:0]
    lookup = {r['url']: r for r in ledger.to_dict('records')}
    def allowed(row):
        check = lookup.get(row['url'], {})
        if check.get('status') != 'verified' or check.get('snapshot_sha256') != fingerprint(row):
            return False
        try:
            age = (listings.today() - pd.Timestamp(check['checked_at']).date()).days
        except (ValueError, TypeError):
            return False
        if not 0 <= age <= (180 if row['price_basis'] == 'auction' else 30):
            return False
        return (check.get('availability') == ('sold' if row['sale_status'] == 'sold' else 'available')
                and check.get('working_status') in ('seller_reports_normal', 'tested'))
    return frame[frame.apply(allowed, axis=1)].copy()


def cohort(frame):
    """서로 다른 모델·사양·상태를 섞지 않고 가장 큰 단일 그룹만 반환."""
    if frame.empty:
        return frame
    known = frame[(frame.manufacturer != '') & (frame.model != '')]
    groups = list(known.groupby(['manufacturer', 'model', 'spec', 'condition'], sort=True))
    return max(groups, key=lambda pair: len(pair[1]))[1] if groups else frame.iloc[:0]

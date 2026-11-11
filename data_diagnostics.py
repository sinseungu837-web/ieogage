"""기존 매칭/정제 함수를 그대로 실행하는 데이터 운영 진단. API 조회는 하지 않는다."""
from collections import Counter
from pathlib import Path

import pandas as pd
import listings


def diagnose(path=listings.LISTINGS, **query):
    import price
    decisions = []
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        if not set(listings.COLUMNS) <= set(raw.columns):
            return {'db_status': 'SCHEMA_ERROR', 'missing_columns': sorted(set(listings.COLUMNS) - set(raw.columns)), 'onbid_key_configured': bool(price._key())}
        frame = listings.normalize(raw.to_dict('records'), decisions)
    except FileNotFoundError:
        return {'db_status': 'FILE_MISSING', 'onbid_key_configured': bool(price._key())}
    except Exception as exc:
        return {'db_status': 'READ_ERROR', 'error': type(exc).__name__, 'onbid_key_configured': bool(price._key())}
    cutoff = (pd.Timestamp(listings.today()) - pd.Timedelta(days=180)).date().isoformat()
    excluded = []
    for row in frame.to_dict('records'):
        reasons = []
        if row['is_sample'] == 'true':
            reasons.append('sample_record')
        if row['collected_at'] < cutoff:
            reasons.append('older_than_180_days')
        if reasons:
            excluded.append({'url': row['url'], 'reasons': reasons})
    steps = []
    result = price.estimate(query.pop('name', ''), listings_path=path, allow_onbid=False,
                            diagnostics=steps, **query)
    return {'db_status': 'EMPTY' if raw.empty else 'OK', 'raw_records': len(raw),
            'normalized_records': len(frame), 'eligible_records': len(listings.production(frame)),
            'onbid_key_configured': bool(price._key()),
            'normalization_reasons': dict(Counter(d['reason'] for d in decisions if d['status'] == 'excluded')),
            'rows': decisions, 'production_exclusions': excluded, 'matching_steps': steps,
            'matched_samples': result['n'], 'quote_status': 'OK' if result['mid'] is not None else 'INSUFFICIENT_MATCHED_SAMPLES',
            'price_basis': result['price_basis']}

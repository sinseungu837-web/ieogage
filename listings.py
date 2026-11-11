"""CSV 매물 정제·통계. 웹/LLM 호출 없이 같은 규칙으로 저장과 조회를 검증한다."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pandas as pd

BASE = Path(__file__).parent / 'data'
LISTINGS = BASE / 'listings.csv'
COLUMNS = ['item', 'manufacturer', 'model', 'spec', 'title', 'condition', 'price',
           'currency', 'sale_status', 'price_basis', 'quantity', 'source', 'url',
           'collected_at', 'is_sample']
TABLE_COLUMNS = ['품목', '브랜드', '모델', '연식구간', '상태', '신품가', '중고가_하',
                 '중고가_중', '중고가_상', '표본수', '출처', '수집일', '사양', '가격성격']
ALIASES = {'에스프레소머신': '커피머신', '에스프레소 머신': '커피머신', '커피 머신': '커피머신',
           '그라인더': '커피 그라인더', '커피그라인더': '커피 그라인더',
           '냉장고': '업소용 냉장고', '카페 테이블': '테이블', '카페 의자': '의자'}
MAKERS = {'라마르조꼬': '라마르조코', '카이져': '카이저', '카이저제빙기': '카이저',
          '네오트제빙기': '네오트', 'lamarzocco': '라마르조코', '라 마르조코': '라마르조코',
          'nuovasimonelli': '누오바시모넬리', 'mazzer': '메저', '메져': '메저',
          'hoshizaki': '호시자키', 'scotsman': '스코츠맨'}
ITEM_WORDS = {'커피머신': ['커피머신', '에스프레소'], '커피 그라인더': ['그라인더'],
              '제빙기': ['제빙기'], '업소용 냉장고': ['냉장고'], '테이블': ['테이블'], '의자': ['의자']}
BUNDLE = re.compile(r'일괄|묶음|세트|외\s*\d|(?<!\d)(?:[2-9]|[1-9]\d+)\s*(?:개|대|점|조)|및|\+')
IRRELEVANT = re.compile(r'부품|고장|수리용|삽니다|매입합니다|렌탈|대여|원두|의료|실험|가정용|장난감|무료나눔')


def today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def token(value):
    return re.sub(r'[\s_\-]+', '', str(value or '')).casefold()


def maker(value):
    text = str(value or '').strip()
    if text in ('미확인', '모름', 'unknown'):
        return ''
    return MAKERS.get(token(text), MAKERS.get(text, text.casefold()))


def item(value):
    text = str(value or '').strip()
    return ALIASES.get(text, text)


def canonical_url(value):
    u = urlsplit(str(value).strip())
    if u.scheme != 'https' or not u.hostname or u.username or u.password:
        raise ValueError('공개 HTTPS 원본 URL 필요')
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((u.scheme, u.netloc.lower(), u.path.rstrip('/'), urlencode(sorted(query)), ''))


def normalize(rows, diagnostics=None):
    """기존 정제 규칙 적용. diagnostics에 행별 최초 제외 사유를 기록한다."""
    valid, decisions = [], []
    for index, raw in enumerate(rows, start=2):
        r = {c: str(raw.get(c, '')).strip() for c in COLUMNS}
        reason = ''
        try:
            r['item'] = item(r['item'])
            r['manufacturer'] = maker(r['manufacturer'])
            try:
                p = float(r['price'].replace(',', '').removesuffix('원'))
            except ValueError:
                raise ValueError('invalid_price') from None
            if not math.isfinite(p) or not 1000 <= p <= 50_000_000:
                raise ValueError('price_out_of_range')
            try:
                q = float(r['quantity'])
            except ValueError:
                raise ValueError('invalid_quantity') from None
            if q != 1:
                raise ValueError('bundle_quantity')
            try:
                collected = date.fromisoformat(r['collected_at'][:10])
            except ValueError:
                raise ValueError('invalid_collection_date') from None
            if collected > today():
                raise ValueError('future_collection_date')
            if not r['source'] or not r['title'] or not r['item']:
                raise ValueError('missing_identity')
            if r['is_sample'].lower() not in ('true', 'false'):
                raise ValueError('sample_flag_missing')
            if r['currency'] != 'KRW':
                raise ValueError('unsupported_currency')
            expected = {'asking': 'for_sale', 'sold_posted': 'sold', 'auction': 'sold'}
            if r['price_basis'] not in expected or r['sale_status'] != expected[r['price_basis']]:
                raise ValueError('sale_status_basis_mismatch')
            if BUNDLE.search(r['title']):
                raise ValueError('bundle_title')
            # 원두는 그라인더/머신의 용도를 설명하기도 한다. 장비 키워드 검사는 아래에서 별도 수행.
            irrelevant_title = r['title']
            if r['item'] in ('커피 그라인더', '커피머신'):
                irrelevant_title = irrelevant_title.replace('원두', '')
            if IRRELEVANT.search(irrelevant_title):
                raise ValueError('irrelevant_title')
            words = ITEM_WORDS.get(r['item'])
            if words and not any(token(w) in token(r['title']) for w in words):
                raise ValueError('item_keyword_mismatch')
            try:
                r['url'] = canonical_url(r['url'])
            except ValueError:
                raise ValueError('invalid_public_url') from None
            r.update(price=int(p), quantity=1, collected_at=collected.isoformat(),
                     is_sample=r['is_sample'].lower(), _row=index)
            valid.append(r)
        except (ValueError, TypeError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else 'invalid_value'
        decisions.append({'row': index, 'title': r['title'], 'source': r['source'],
                          'status': 'excluded' if reason else 'accepted', 'reason': reason or 'valid'})
    frame = pd.DataFrame(valid, columns=COLUMNS + ['_row'])
    frame = frame.sort_values('collected_at').drop_duplicates('url', keep='last')
    retained = set(frame['_row'])
    for decision in decisions:
        if decision['status'] == 'accepted' and decision['row'] not in retained:
            decision.update(status='excluded', reason='duplicate_url')
    if diagnostics is not None:
        diagnostics.extend(decisions)
    return frame.drop(columns='_row').reset_index(drop=True)


def read(path=LISTINGS):
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if not set(COLUMNS) <= set(frame.columns):
            raise ValueError('매물 CSV 열 불일치')
        return normalize(frame.to_dict('records'))
    except FileNotFoundError:
        return pd.DataFrame(columns=COLUMNS)


def production(frame, max_age=180):
    if frame.empty:
        return frame
    cutoff = (pd.Timestamp(today()) - pd.Timedelta(days=max_age)).date().isoformat()
    return frame[(frame.is_sample == 'false') & (frame.collected_at >= cutoff)]


def summary(frame):
    rows = []
    # 출처·가격 성격을 분리. 상세 매칭은 price.py에서 매물 원본으로 수행한다.
    for keys, group in production(frame).groupby(['item', 'manufacturer', 'model', 'spec', 'condition', 'source', 'price_basis']):
        name, brand, model, spec, cond, source, basis = keys
        if len(group) < 3:
            continue
        q1, mid, q3 = group.price.astype(float).quantile([.25, .5, .75])
        rows.append([name, brand, model, '', cond, '', int(q1), int(mid), int(q3),
                     len(group), source, group.collected_at.max(), spec, basis])
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


def atomic_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.csv.tmp')
    try:
        frame.to_csv(temp, index=False, encoding='utf-8-sig')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def save(rows, path=LISTINGS, table_path=BASE / 'price_table.csv'):
    old = read(path)
    new = normalize(rows)
    if new.empty:
        return 0  # 실패/빈 응답이면 기존 파일을 건드리지 않는다.
    combined = normalize(old.to_dict('records') + new.to_dict('records'))
    atomic_csv(combined, path)
    atomic_csv(summary(combined), table_path)
    return len(new)

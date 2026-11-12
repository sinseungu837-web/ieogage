"""서비스 요청과 분리한 CLI 수집기. 허용 출처의 공개 Product JSON-LD / 온비드 API.

출처는 data/sources.json에서 약관 확인 후 등록한다. 미등록 출처는 수집하지 않는다.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import time
import urllib.request
import urllib.robotparser
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, quote

import listings

USER_AGENT = 'IeogageResearch/1.0'
SOURCES = listings.BASE / 'sources.json'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 정책을 확인하지 않은 리다이렉트 목적지를 자동 방문하지 않는다.


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        return response.read(2_000_000).decode('utf-8')


def source_for(name, path=SOURCES):
    source = json.loads(Path(path).read_text(encoding='utf-8')).get(name)
    if not source or source.get('enabled') is not True or source.get('automation_allowed') is not True:
        raise ValueError('자동 수집 허용이 확인된 등록 출처가 아닙니다')
    checked = date.fromisoformat(source['checked_at'])
    if not 0 <= (listings.today() - checked).days <= 90 or not source.get('permission_evidence'):
        raise ValueError('출처 허용 근거와 90일 이내 정책 확인일이 필요합니다')
    listings.canonical_url(source['terms_url'])
    return source


class ProductParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.buffer = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') == 'application/ld+json':
            self.active, self.buffer = True, []

    def handle_data(self, text):
        if self.active:
            self.buffer.append(text)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.active = False
            try:
                self.documents.append(json.loads(''.join(self.buffer)))
            except ValueError:
                pass


def products(document):
    if isinstance(document, list):
        for child in document:
            yield from products(child)
    elif isinstance(document, dict):
        kinds = document.get('@type', [])
        if 'Product' in ([kinds] if isinstance(kinds, str) else kinds):
            yield document
        for key in ('@graph', 'itemListElement', 'item'):
            if key in document:
                yield from products(document[key])


def parse_products(html, page_url, source, item):
    parser = ProductParser()
    parser.feed(html)
    rows = []
    for doc in parser.documents:
        for product in products(doc):
            offers = product.get('offers', [])
            for offer in offers if isinstance(offers, list) else [offers]:
                # AggregateOffer 범위·품절·경매 진행가는 개별 판매 가격으로 해석하지 않음.
                if offer.get('@type') != 'Offer' or offer.get('availability', '').rsplit('/', 1)[-1] != 'InStock':
                    continue
                quantity = product.get('numberOfItems', 1)
                eligible = offer.get('eligibleQuantity', {})
                if not isinstance(eligible, dict):
                    continue
                if str(quantity) != '1' or str(eligible.get('value', 1)) != '1' or str(eligible.get('minValue', 1)) != '1':
                    continue
                condition = offer.get('itemCondition', product.get('itemCondition', ''))
                if condition.rsplit('/', 1)[-1] != 'UsedCondition':
                    continue
                brand = product.get('brand', '')
                if isinstance(brand, dict):
                    brand = brand.get('name', '')
                rows.append(dict(item=item, manufacturer=brand, model=product.get('model', ''),
                                 spec=product.get('size', ''), title=product.get('name', ''),
                                 condition='중고', price=offer.get('price', ''),
                                 currency=offer.get('priceCurrency', ''), sale_status='for_sale',
                                 price_basis='asking', quantity=1, source=source,
                                 url=urljoin(page_url, offer.get('url') or product.get('url') or page_url),
                                 collected_at=listings.today().isoformat(), is_sample='false'))
    return rows


def crawl(name, urls, item, registry=SOURCES):
    config = source_for(name, registry)
    origin = config['origin'].rstrip('/')
    if urlsplit(origin).scheme != 'https':
        raise ValueError('HTTPS 출처만 지원합니다')
    robot = urllib.robotparser.RobotFileParser()
    robot.parse(fetch(origin + '/robots.txt').splitlines())
    delay = max(2, float(config.get('delay_seconds', 2)), robot.crawl_delay(USER_AGENT) or 0)
    rows = []
    for url in urls:
        listings.canonical_url(url)
        if urlsplit(url).netloc != urlsplit(origin).netloc or not robot.can_fetch(USER_AGENT, url):
            raise ValueError('출처 도메인 또는 robots 정책 불일치')
        time.sleep(delay)
        extracted = parse_products(fetch(url), url, name, item)
        if any(urlsplit(r['url']).netloc != urlsplit(origin).netloc for r in extracted):
            raise ValueError('원본 상품 도메인 불일치')
        rows.extend(extracted)
    return rows


# ---------------- 헬로마켓 (등록 출처: data/sources.json) ----------------
HELLO = '헬로마켓'
# 품목 → 헬로마켓 검색어. 업소용 장비 위주로 좁힌다.
SEARCH_QUERIES = {'커피머신': ['에스프레소 머신', '커피머신 업소용'], '커피 그라인더': ['커피 그라인더'],
                  '제빙기': ['제빙기'], '업소용 냉장고': ['업소용 냉장고'], '테이블': ['카페 테이블', '테이블 업소용'], '의자': ['카페 의자', '카페 체어']}
PHONE = re.compile(r'\d[\d\- ]{7,}\d')  # 제목 속 전화번호 등 긴 숫자열은 저장하지 않는다
AGE = re.compile(r'(\d+)\s*(분|시간|일|개월|년)\s*전')
# 중고 시세에 넣지 않을 매물: 새 제품, 다른 장비와 함께 파는 묶음
NOT_USED = re.compile(r'새상품|새제품|새 제품|미개봉|신품|포함')
OTHER_DEVICE = {'커피 그라인더': re.compile(r'머신|메이커')}


def _usable(item, title):
    return not NOT_USED.search(title) and not (item in OTHER_DEVICE and OTHER_DEVICE[item].search(title))


def _clean_title(title):
    return re.sub(r'\s+', ' ', PHONE.sub('', title)).strip()


def _age_days(text):
    m = AGE.search(text or '')
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {'분': 0, '시간': 0, '일': n, '개월': n * 30, '년': n * 365}[unit]


def _robot(origin):
    robot = urllib.robotparser.RobotFileParser()
    robot.parse(fetch(origin + '/robots.txt').splitlines())
    return robot


def search_cards(query, name=HELLO, registry=SOURCES):
    """검색 결과 1페이지의 카드(제목·가격·등록 시점·링크). 요청 1회."""
    config = source_for(name, registry)
    origin = config['origin'].rstrip('/')
    url = f'{origin}/search?q={quote(query)}'
    if not _robot(origin).can_fetch(USER_AGENT, url):
        raise ValueError('robots 정책 불일치')
    page = fetch(url)
    cards, seen = [], set()
    chunks = re.split(r'href="(/item/\d+)', page)
    for href, body in zip(chunks[1::2], chunks[2::2]):
        if href in seen:
            continue
        parts = [html_lib.unescape(p).strip() for p in re.split(r'<[^>]+>', body[:1500]) if p.strip()]
        price_i = next((i for i, p in enumerate(parts) if re.fullmatch(r'[\d,]+\s*원', p)), None)
        if price_i is None or price_i + 1 >= len(parts):
            continue
        seen.add(href)
        age = next((_age_days(p) for p in parts[price_i + 1:price_i + 5] if _age_days(p) is not None), None)
        cards.append({'title': _clean_title(parts[price_i + 1]), 'price': parts[price_i].replace(',', '').replace('원', '').strip(),
                      'url': origin + href, 'age_days': age})
    return cards


def live_rows(item, max_age=180, name=HELLO):
    """서비스 요청 중 실시간 보완용. 검색 결과 카드만 사용 (품목당 요청 1~2회)."""
    rows = []
    for query in SEARCH_QUERIES.get(item, [item]):
        for card in search_cards(query, name):
            if card['age_days'] is None or card['age_days'] > max_age or not _usable(item, card['title']):
                continue
            rows.append(dict(item=item, manufacturer='', model='', spec='', title=card['title'], condition='중고',
                             price=card['price'], currency='KRW', sale_status='for_sale', price_basis='asking',
                             quantity=1, source=name + ' (실시간 검색)', url=card['url'],
                             collected_at=listings.today().isoformat(), is_sample='false'))
    return rows


def discover(item, limit=12, name=HELLO, registry=SOURCES):
    """검색 → 매물 페이지(JSON-LD Product)를 열어 DB용 행을 만든다. 판매자 정보는 저장하지 않는다."""
    config = source_for(name, registry)
    delay = max(2, float(config.get('delay_seconds', 2)))
    urls = []
    for query in SEARCH_QUERIES.get(item, [item]):
        for card in search_cards(query, name, registry):
            if card['age_days'] is not None and card['age_days'] <= 180 and card['url'] not in urls                     and _usable(item, card['title']):
                urls.append(card['url'])
        time.sleep(delay)
    rows = crawl(name, urls[:limit], item, registry)
    for r in rows:
        r['manufacturer'] = ''  # 헬로마켓 JSON-LD 의 brand 는 판매자 닉네임이라 저장하지 않음
        r['title'] = _clean_title(r['title'])
    return rows


def collect_onbid(diagnostics=None):
    import price
    if not price._key():
        raise MissingAPIKey('MISSING_API_KEY: DATA_GO_KR_KEY')
    rows = []
    for name in listings.ITEM_WORDS:
        for record in price.evidence(name, diagnostics=diagnostics):
            # 물건관리번호가 없는 API 기록은 안정적인 원본 식별 불가.
            if not record['물건관리번호']:
                continue
            rows.append(dict(item=name, manufacturer='', model='', spec='',
                             title=record['물건명'], condition='', price=record['낙찰가'],
                             currency='KRW', sale_status='sold', price_basis='auction', quantity=1,
                             source='온비드 (물건관리번호 검색 링크)',
                             url='https://search.daum.net/search?q=' + quote('온비드 ' + str(record['물건관리번호']) + ' ' + record['물건명']), collected_at=listings.today().isoformat(), is_sample='false'))
    return rows


class MissingAPIKey(ValueError):
    pass


REVIEW_COLUMNS = ['verified_at', 'reviewed_by', 'verification_note']


def import_manual(path, output=listings.LISTINGS, table_path=listings.BASE / 'price_table.csv',
                  audit_path=listings.BASE / 'manual_reviews.csv'):
    """원문을 직접 검증한 사실 데이터 입력. 웹 요청/자동 수집 허용 등록과 분리."""
    import pandas as pd
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not set(listings.COLUMNS + REVIEW_COLUMNS) <= set(frame.columns):
        raise ValueError('수동 CSV 필수 열 또는 검증 메타데이터 누락')
    rows = frame.to_dict('records')
    for row in rows:
        verified = date.fromisoformat(row['verified_at'])
        if not 0 <= (listings.today() - verified).days <= 30:
            raise ValueError('수동 검증일은 최근 30일 이내여야 합니다')
        if row['is_sample'] != 'false' or not row['reviewed_by'].strip() or not row['verification_note'].strip():
            raise ValueError('실데이터 표시·검증자·원문 확인 메모가 필요합니다')
        if row['collected_at'][:10] != row['verified_at']:
            raise ValueError('수집일은 원문을 확인한 검증일과 같아야 합니다')
    decisions = []
    accepted = listings.normalize(rows, decisions)
    n = listings.save(accepted.to_dict('records'), output, table_path)
    if n:
        accepted_lines = {d['row'] for d in decisions if d['status'] == 'accepted'}
        metadata = {listings.canonical_url(r['url']): r for i, r in enumerate(rows, start=2) if i in accepted_lines}
        audit = [r | {key: metadata[r['url']][key] for key in REVIEW_COLUMNS}
                 for r in accepted.to_dict('records')]
        target = Path(audit_path)
        old = pd.read_csv(target, dtype=str, keep_default_na=False).to_dict('records') if target.exists() else []
        merged = pd.DataFrame(old + audit, columns=listings.COLUMNS + REVIEW_COLUMNS)
        merged = merged.drop_duplicates(['url', 'verified_at'], keep='last')
        listings.atomic_csv(merged, target)
    return {'input_records': len(rows), 'accepted': n, 'decisions': decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    onbid = sub.add_parser('onbid')
    onbid.add_argument('--report')
    web = sub.add_parser('crawl')
    web.add_argument('--source', required=True)
    web.add_argument('--item', required=True, choices=list(listings.ITEM_WORDS))
    web.add_argument('urls', nargs='+')
    hello = sub.add_parser('hellomarket', help='헬로마켓 검색 → 매물 페이지 수집 (품목 미지정 시 전체)')
    hello.add_argument('--item', choices=list(listings.ITEM_WORDS))
    hello.add_argument('--limit', type=int, default=12)
    imported = sub.add_parser('import-csv')
    imported.add_argument('file')
    imported.add_argument('--source', required=True)
    manual = sub.add_parser('import-manual')
    manual.add_argument('file')
    diagnose = sub.add_parser('diagnose')
    diagnose.add_argument('--file', default=str(listings.LISTINGS))
    diagnose.add_argument('--item', default='')
    for field in ('manufacturer', 'model', 'spec', 'condition'):
        diagnose.add_argument('--' + field, default='')
    args = parser.parse_args()
    try:
        if args.command == 'diagnose':
            from data_diagnostics import diagnose
            print(json.dumps(diagnose(args.file, name=args.item, manufacturer=args.manufacturer,
                model=args.model, spec=args.spec, condition=args.condition), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'import-manual':
            print(json.dumps(import_manual(args.file), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'onbid':
            report = []
            rows = collect_onbid(diagnostics=report)
            successful = [entry for entry in report if entry.get('status') == 'OK']
            status = 'OK' if rows else ('API_REQUEST_FAILED' if not successful else
                ('FILTERED_ALL_RECORDS' if any(entry.get('api_records', 0) for entry in successful) else 'API_RETURNED_ZERO_RECORDS'))
            summary = {'status': status, 'accepted_before_csv': len(rows), 'queries': report}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            if args.report:
                Path(args.report).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            if status == 'API_REQUEST_FAILED':
                return 1
        elif args.command == 'crawl':
            rows = crawl(args.source, args.urls, args.item)
        elif args.command == 'hellomarket':
            rows = []
            for name in ([args.item] if args.item else list(listings.ITEM_WORDS)):
                got = discover(name, args.limit)
                print(f'{name}: 매물 {len(got)}건 수집')
                rows.extend(got)
        else:
            config = source_for(args.source)
            rows = listings.read(args.file).to_dict('records')
            if any(r['source'] != args.source or urlsplit(r['url']).netloc != urlsplit(config['origin']).netloc for r in rows):
                raise ValueError('CSV 출처 또는 원본 도메인 불일치')
        n = listings.save(rows)
        print(f'유효 매물 {n}건 반영' if n else '유효 신규 매물 0건 · 기존 DB 유지')
    except MissingAPIKey:
        if getattr(args, 'report', None):
            Path(args.report).write_text(json.dumps({'status': 'MISSING_API_KEY', 'api_records': None}), encoding='utf-8')
        print('MISSING_API_KEY: DATA_GO_KR_KEY 미설정 · API 미조회 (매물 부족과 다름) · 기존 DB 유지')
        return 1
    except Exception as exc:
        # API 오류 메시지는 키가 포함된 URL을 담을 수 있어 원문을 출력하지 않는다.
        print(f'수집 실패 ({type(exc).__name__}) · 기존 DB 유지. 출처 정책/설정/키를 확인하세요.')
        return 1
    return 0


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
    raise SystemExit(main())

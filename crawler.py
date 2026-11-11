"""서비스 요청과 분리한 CLI 수집기. 허용 출처의 공개 Product JSON-LD / 온비드 API.

출처는 data/sources.json에서 약관 확인 후 등록한다. 미등록 출처는 수집하지 않는다.
"""
from __future__ import annotations

import argparse
import json
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


def collect_onbid(diagnostics=None):
    import price
    if not price._key():
        raise MissingAPIKey('MISSING_API_KEY: DATA_GO_KR_KEY')
    rows = []
    for name in listings.ITEM_WORDS:
        for record in price.evidence(name, diagnostics=diagnostics, strict=True):
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
        from onbid_api import OnbidError
        if isinstance(exc, OnbidError):
            if getattr(args, 'report', None):
                Path(args.report).write_text(json.dumps({'status': 'API_REQUEST_FAILED',
                    'category': exc.category, 'http_status': exc.http_status,
                    'api_code': exc.api_code, 'queries': report}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'수집 실패 · 유형={exc.category} HTTP={exc.http_status} API코드={exc.api_code} · 기존 DB 유지')
            return 1
        # API 오류 메시지는 키가 포함된 URL을 담을 수 있어 원문을 출력하지 않는다.
        print(f'수집 실패 ({type(exc).__name__}) · 기존 DB 유지. 출처 정책/설정/키를 확인하세요.')
        return 1
    return 0


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
    raise SystemExit(main())

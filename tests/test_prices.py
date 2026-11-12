"""합성 데이터는 임시 디렉터리에만 생성하며 운영 CSV에 저장하지 않는다."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import listings
import price
import crawler
from inventory import inventory_rows


def record(i=0, **changes):
    return dict(item='커피머신', manufacturer='라마르조코', model='Linea', spec='2그룹',
                title='라마르조코 Linea 에스프레소 머신 중고', condition='중고',
                price=1000000 + i * 100000, currency='KRW', sale_status='for_sale',
                price_basis='asking', quantity=1, source='테스트 전용',
                url=f'https://example.org/product/{i}', collected_at=date.today().isoformat(),
                is_sample='false', **{}) | changes


class PriceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'listings.csv'
        self.table = self.path.with_name('price_table.csv')

    def write(self, rows):
        listings.atomic_csv(listings.normalize(rows), self.path)

    def estimate(self, name='커피머신', brand='라마르조코', model='Linea', **kwargs):
        return price.estimate(name, brand, model, listings_path=self.path, allow_onbid=False, **kwargs)

    def test_quartiles_and_quantity(self):
        self.write([record(i) for i in range(4)])
        e = self.estimate()
        self.assertEqual((e['low'], e['mid'], e['high'], e['n']), (1075000, 1150000, 1225000, 4))
        self.assertEqual(e['mid'] * 3, 3450000)
        self.assertEqual(len(e['evidence']), 4)

    def test_different_manufacturer_not_used(self):
        self.write([record(i, manufacturer='메저') for i in range(4)])
        self.assertIsNone(self.estimate()['mid'])

    def test_brand_alias_and_model_normalization(self):
        self.write([record(i, manufacturer='La Marzocco', model='Li-nea') for i in range(3)])
        self.assertEqual(self.estimate()['n'], 3)

    def test_missing_brand_and_model_low_confidence(self):
        self.write([record(i) for i in range(3)])
        e = self.estimate(brand='', model='')
        self.assertIsNotNone(e['mid'])
        self.assertEqual(e['confidence'], '낮음')

    def test_fewer_than_three(self):
        self.write([record(0), record(1)])
        e = self.estimate()
        self.assertEqual(e['n'], 2)
        self.assertIsNone(e['mid'])

    def test_known_model_does_not_fall_back_to_other_models(self):
        self.write([record(i, model='Other') for i in range(4)])
        self.assertIsNone(self.estimate()['mid'])
        self.assertIsNotNone(self.estimate(spec='2그룹')['mid'])
        self.assertIsNone(self.estimate(spec='3그룹')['mid'])

    def test_exact_model_small_sample_not_broadened(self):
        self.write([record(0)] + [record(i, model='Other') for i in range(1, 6)])
        self.assertEqual(self.estimate(spec='2그룹')['n'], 1)

    def test_condition_and_spec_filters(self):
        self.write([record(i) for i in range(4)])
        self.assertIsNone(self.estimate(condition='고장')['mid'])
        self.assertIsNone(self.estimate(spec='1그룹')['mid'])

    def test_asking_sold_and_auction_not_pooled(self):
        self.write([record(0), record(1), record(2, price_basis='auction', sale_status='sold')])
        self.assertIsNone(self.estimate()['mid'])
        self.write([record(i) for i in range(3)] + [record(i, price=10000, price_basis='auction', sale_status='sold') for i in range(3, 6)])
        e = self.estimate()
        self.assertEqual(e['price_basis'], 'asking')
        self.assertEqual(e['mid'], 1100000)

    def test_sold_posted_is_not_actual_transaction(self):
        self.write([record(i, price_basis='sold_posted', sale_status='sold') for i in range(3)])
        self.assertIn('실제 거래액 미확인', self.estimate()['source'])

    def test_samples_and_stale_records_never_used(self):
        self.write([record(i, is_sample='true') for i in range(3)])
        self.assertIsNone(self.estimate()['mid'])
        old = (date.today() - timedelta(days=181)).isoformat()
        self.write([record(i, collected_at=old) for i in range(3)])
        self.assertIsNone(self.estimate()['mid'])

    def test_duplicate_url_does_not_increase_count(self):
        self.write([record(0), record(1, url='https://example.org/product/0?utm_source=test'), record(0)] * 2)
        self.assertEqual(self.estimate()['n'], 1)

    def test_invalid_bundle_currency_irrelevant_excluded(self):
        bad = [record(0, price=0), record(1, price='NaN'), record(2, quantity=2),
               record(3, title='커피머신 일괄'), record(4, title='커피머신 부품'),
               record(5, currency='USD'), record(6, title='테이블'),
               record(7, collected_at='bad'), record(8, url='javascript:alert(1)'),
               record(9, is_sample=''), record(10, sale_status='reserved'), record(11, title='커피머신 2대')]
        self.assertTrue(listings.normalize(bad).empty)

    def test_failure_and_empty_update_preserve_db(self):
        listings.save([record(i) for i in range(3)], self.path, self.table)
        before = self.path.read_bytes()
        self.assertEqual(listings.save([], self.path, self.table), 0)
        self.assertEqual(listings.save([record(9, price=-1)], self.path, self.table), 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(listings.summary(listings.read(self.path))), 1)

    def test_broken_csv_and_api_timeout_do_not_crash(self):
        self.path.write_text('broken\n1\n')
        with patch('price.onbid_estimate', side_effect=TimeoutError):
            e = price.estimate('제빙기', listings_path=self.path)
        self.assertIsNone(e['mid'])
        self.assertIn('DB 조회 오류', e['source'])

    def test_api_fallback_separate(self):
        self.write([record(0)])
        fallback = dict(low=1000, mid=2000, high=3000, n=3, source='온비드', evidence=[], search_url='https://example.org')
        with patch('price.onbid_estimate', return_value=fallback):
            e = price.estimate('커피머신', listings_path=self.path)
        self.assertEqual(e['mid'], 2000)
        self.assertEqual(e['price_basis'], 'auction')

    def test_multiple_items_and_model_groups(self):
        last = {'det': {'objects': [{'id': i} for i in range(1, 5)]},
                'box_names': {1: '커피머신', 2: '커피머신', 3: '제빙기', 4: '커피머신'},
                'manufacturers': {}, 'models': {1: 'A', 2: 'A', 4: 'B'}}
        rows = inventory_rows(last)
        self.assertEqual([(r['집기명'], r.get('모델명'), r['개수']) for r in rows],
                         [('커피머신', 'A', 2), ('제빙기', None, 1), ('커피머신', 'B', 1)])


class CrawlerTest(unittest.TestCase):
    def test_unapproved_source_rejected_without_network(self):
        with patch('crawler.fetch') as fetch:
            with self.assertRaises(ValueError):
                crawler.crawl('번개장터', ['https://m.bunjang.co.kr/products/1'], '커피머신')
            fetch.assert_not_called()

    def test_missing_api_key(self):
        with patch('price._key', return_value=None):
            with self.assertRaises(ValueError):
                crawler.collect_onbid()

    def test_jsonld_used_krw_offer(self):
        product = {'@type': 'Product', 'name': '중고 에스프레소 머신', 'brand': {'name': '라마르조코'},
                   'model': 'Linea', 'offers': {'@type': 'Offer', 'price': 1000000,
                   'priceCurrency': 'KRW', 'availability': 'https://schema.org/InStock',
                   'itemCondition': 'https://schema.org/UsedCondition'}}
        html = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        rows = crawler.parse_products(html, 'https://example.org/1', '테스트', '커피머신')
        self.assertEqual(len(listings.normalize(rows)), 1)
        self.assertEqual(rows[0]['price_basis'], 'asking')
        product['offers']['availability'] = 'https://schema.org/SoldOut'
        html = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        self.assertEqual(crawler.parse_products(html, 'https://example.org/1', '테스트', '커피머신'), [])

    def test_allowed_crawl_to_csv_to_estimate(self):
        product = {'@type': 'Product', 'name': '제빙기 중고', 'model': 'A',
                   'brand': {'name': '호시자키'}, 'offers': {'@type': 'Offer',
                   'price': 1000000, 'priceCurrency': 'KRW',
                   'availability': 'https://schema.org/InStock',
                   'itemCondition': 'https://schema.org/UsedCondition'}}
        html = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        def response(url):
            return 'User-agent: *\nAllow: /' if url.endswith('/robots.txt') else html
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'sources.json'
            registry.write_text(json.dumps({'allowed': dict(enabled=True, automation_allowed=True,
                origin='https://example.org', terms_url='https://example.org/terms',
                permission_evidence='테스트 전용 허용 정책', checked_at=listings.today().isoformat())}))
            with patch('crawler.fetch', side_effect=response), patch('crawler.time.sleep'):
                rows = crawler.crawl('allowed', [f'https://example.org/{i}' for i in range(3)],
                                     '제빙기', registry=registry)
            db, table = root / 'listings.csv', root / 'price_table.csv'
            self.assertEqual(listings.save(rows, db, table), 3)
            e = price.estimate('제빙기', '호시자키', 'A', listings_path=db, allow_onbid=False)
            self.assertEqual((e['mid'], e['n']), (1000000, 3))
            self.assertEqual(listings.read(db).price_basis.unique().tolist(), ['asking'])

    def test_old_source_policy_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'sources.json'
            registry.write_text(json.dumps({'old': dict(enabled=True, automation_allowed=True,
                origin='https://example.org', terms_url='https://example.org/terms',
                permission_evidence='test', checked_at='2000-01-01')}))
            with self.assertRaises(ValueError):
                crawler.source_for('old', registry)

    def test_robots_deny_and_fetch_failure(self):
        with patch('crawler.source_for', return_value={'origin': 'https://example.org'}):
            with patch('crawler.fetch', return_value='User-agent: *\nDisallow: /'):
                with self.assertRaises(ValueError):
                    crawler.crawl('테스트', ['https://example.org/1'], '커피머신')
            with patch('crawler.fetch', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):
                    crawler.crawl('테스트', ['https://example.org/1'], '커피머신')


if __name__ == '__main__':
    unittest.main()

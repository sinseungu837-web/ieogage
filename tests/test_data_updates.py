import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from io import BytesIO

import pandas as pd
import listings
import crawler
import price
from data_diagnostics import diagnose


def row(i=0, **fields):
    return dict(item='제빙기', manufacturer='카이저', model='IMK-3051', spec='50kg',
        title='카이저 중고 제빙기 IMK-3051', condition='중고', price=400000+i*100000,
        currency='KRW', sale_status='for_sale', price_basis='asking', quantity=1,
        source='테스트 전용', url=f'https://example.org/{i}', collected_at=listings.today().isoformat(),
        is_sample='false', verified_at=listings.today().isoformat(), reviewed_by='테스트 검증자',
        verification_note='합성 데이터 원문 검증 경로 테스트') | fields


class DataUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / 'manual.csv'
        self.db = self.root / 'listings.csv'
        self.table = self.root / 'prices.csv'
        self.audit = self.root / 'audit.csv'

    def manual(self, rows):
        pd.DataFrame(rows).to_csv(self.input, index=False)
        return crawler.import_manual(self.input, self.db, self.table, self.audit)

    def test_manual_records_to_existing_price_and_summary(self):
        result = self.manual([row(i) for i in range(3)])
        self.assertEqual(result['accepted'], 3)
        e = price.estimate('제빙기', '카이저', 'IMK-3051', listings_path=self.db, allow_onbid=False)
        self.assertEqual(e['mid'], 500000)
        self.assertEqual(len(pd.read_csv(self.table)), 1)
        self.assertEqual(len(pd.read_csv(self.audit)), 3)
        self.manual([row(0, price=550000)])
        self.assertEqual(len(listings.read(self.db)), 3)

    def test_unverified_or_sample_import_never_writes(self):
        for changes in ({'reviewed_by': ''}, {'is_sample': 'true'}, {'verification_note': ''},
                        {'verified_at': '2000-01-01'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.manual([row() | changes])
        self.assertFalse(self.db.exists())

    def test_exclusion_reasons_and_bean_grinder_false_positive(self):
        decisions = []
        valid = listings.normalize([row(0, quantity=3), row(1, price='문의'), row(2, currency='USD'),
            row(3, item='커피 그라인더', title='중고 메저 원두 커피 그라인더'), row(4, title='제빙기 일괄')], decisions)
        self.assertEqual(len(valid), 1)
        self.assertEqual([d['reason'] for d in decisions],
                         ['bundle_quantity','invalid_price','unsupported_currency','valid','bundle_title'])

    def test_diagnosis_distinguishes_db_filters_matching_and_key(self):
        self.manual([row(i) for i in range(3)])
        with patch('price._key', return_value=None):
            d = diagnose(self.db, name='제빙기', manufacturer='다른제조사')
        self.assertFalse(d['onbid_key_configured'])
        self.assertEqual(d['raw_records'], 3)
        self.assertEqual(d['matched_samples'], 0)
        self.assertEqual(d['matching_steps'][-1]['remaining'], 0)
        self.assertEqual(d['quote_status'], 'INSUFFICIENT_MATCHED_SAMPLES')

    def test_sample_old_duplicate_diagnosis(self):
        rows = [row(0), row(0), row(1, is_sample='true'),
                row(2, collected_at=(listings.today()-timedelta(days=181)).isoformat())]
        pd.DataFrame(rows).to_csv(self.db, index=False)
        d = diagnose(self.db, name='제빙기')
        self.assertEqual(d['normalization_reasons'], {'duplicate_url': 1})
        self.assertEqual(d['eligible_records'], 1)
        self.assertEqual(len(d['production_exclusions']), 2)

    def test_onbid_key_missing_is_not_empty_response(self):
        diagnostics = []
        with patch('price._key', return_value=None), patch('price._query') as query:
            self.assertEqual(price.evidence('제빙기', diagnostics), [])
            query.assert_not_called()
            with self.assertRaises(crawler.MissingAPIKey):
                crawler.collect_onbid()
        self.assertEqual(diagnostics[0]['status'], 'MISSING_API_KEY')

    def test_onbid_error_does_not_look_like_zero_list(self):
        with patch('price.urllib.request.urlopen', return_value=BytesIO(json.dumps(
            {'header': {'resultCode':'30'}, 'body': {}}).encode())):
            with self.assertRaises(price.OnbidAPIError):
                price._query('제빙기', 'test-key-not-real')
        diagnostics = []
        with patch('price._key', return_value='test-key-not-real'), patch('price._query', side_effect=price.OnbidAPIError('API_ERROR_30')):
            self.assertEqual(price.evidence('제빙기', diagnostics), [])
        self.assertEqual(diagnostics[0]['status'], 'API_ERROR_30')

    def test_onbid_rejections_have_reasons(self):
        raw = [{'onbidCltrNm': '제빙기 일괄', 'cltrMngNo': '1', 'scfbAmt': 100000},
               {'onbidCltrNm': '제빙기', 'cltrMngNo':'2', 'scfbAmt': 1}]
        d = []
        with patch('price._key', return_value='test-key'), patch('price._query', return_value=raw):
            self.assertEqual(price.evidence('제빙기', d), [])
        self.assertEqual(d[0]['exclusion_reasons'], {'bundle_title': 1, 'invalid_price_or_item_price_cap': 1})


if __name__ == '__main__':
    unittest.main()

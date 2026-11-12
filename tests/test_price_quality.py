"""격리된 합성 매물로 검증 이력과 실제 견적 연동을 시험한다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import listings
import price
import price_quality as q


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'listings.csv'
        self.ledger = Path(self.tmp.name) / 'checks.csv'
        self.rows = [dict(item='블렌더', manufacturer='바이타믹스', model='콰이어트원', spec='',
            title='바이타믹스 콰이어트원 블렌더', condition='중고', price=900000+i*100000,
            currency='KRW', sale_status='for_sale', price_basis='asking', quantity=1,
            source='격리 테스트', url=f'https://example.org/{i}',
            collected_at=listings.today().isoformat(), is_sample='false') for i in range(3)]
        self.frame = listings.normalize(self.rows)
        listings.atomic_csv(self.frame, self.db)
        self.checks = [dict(url=r['url'], snapshot_sha256=q.fingerprint(r), status='verified',
            checked_at=listings.today().isoformat(), availability='available',
            working_status='seller_reports_normal', published_at='', verification_note='테스트 전용')
            for r in self.frame.to_dict('records')]
        self.write_checks()

    def write_checks(self):
        listings.atomic_csv(pd.DataFrame(self.checks, columns=q.COLUMNS), self.ledger)

    def estimate(self, **args):
        return price.estimate('블렌더', '바이타믹스', '콰이어트원', listings_path=self.db,
            verification_path=self.ledger, allow_onbid=False, allow_live=False, **args)

    def test_verified_db_wins_without_live_request(self):
        with patch('crawler.live_rows', side_effect=AssertionError('DB 충분 시 조회 금지')):
            result = self.estimate()
        self.assertEqual((result['n'], result['mid']), (3, 1000000))

    def test_missing_pending_unknown_deleted_and_expired_are_not_quotes(self):
        for field, value in [('status','pending'), ('working_status','unknown'),
                             ('availability','deleted'), ('checked_at','2000-01-01')]:
            with self.subTest(field=field):
                old = self.checks[0][field]
                self.checks[0][field] = value
                self.write_checks()
                self.assertIsNone(self.estimate()['mid'])
                self.checks[0][field] = old
        self.ledger.unlink()
        self.assertIsNone(self.estimate()['mid'])

    def test_changed_price_or_model_requires_review(self):
        for field, value in [('price',123456), ('model','다른 모델')]:
            frame = self.frame.copy()
            frame.loc[0, field] = value
            listings.atomic_csv(frame, self.db)
            self.assertIsNone(self.estimate()['mid'])

    def test_insufficient_db_reaches_live_but_never_uses_search_cards(self):
        listings.atomic_csv(self.frame.iloc[:1], self.db)
        with patch('crawler.live_rows', return_value=self.rows) as live:
            result = price.estimate('블렌더','바이타믹스','콰이어트원',listings_path=self.db,
                verification_path=self.ledger,allow_onbid=False,allow_live=True)
        live.assert_called_once()
        self.assertIsNone(result['mid'])

    def test_live_detail_requires_every_requested_attribute(self):
        live_rows = [r | {'_detail_verified': True} for r in self.rows]
        for changes in ({'manufacturer':'다른브랜드'}, {'spec':'500L'}, {'condition':'미사용'}, {'model':'다른모델'}):
            args = dict(manufacturer='바이타믹스',model='콰이어트원') | changes
            with self.subTest(changes=changes), patch('crawler.live_rows',return_value=live_rows):
                result=price.estimate('블렌더',**args,listings_path=Path(self.tmp.name)/'empty.csv',
                    allow_onbid=False,allow_live=True)
                self.assertIsNone(result['mid'])

    def test_verified_live_detail_can_supply_quote(self):
        with patch('crawler.live_rows', return_value=[r | {'_detail_verified': True} for r in self.rows]):
            result=price.estimate('블렌더','바이타믹스','콰이어트원',
                listings_path=Path(self.tmp.name)/'empty.csv',allow_onbid=False,allow_live=True)
        self.assertEqual((result['low'],result['mid'],result['high']), (950000,1000000,1050000))

    def test_cli_api_error_is_nonzero_and_never_writes_db(self):
        from onbid_api import OnbidError
        import crawler
        with patch('sys.argv',['crawler.py','onbid','--report',str(Path(self.tmp.name)/'error.json')]), \
             patch('price._key',return_value='test'), \
             patch('price._query',side_effect=OnbidError('parameter',http_status=400,api_code='10')), \
             patch('listings.save') as save, patch('builtins.print'):
            self.assertEqual(crawler.main(),1)
        save.assert_not_called()
        self.assertIn('API_REQUEST_FAILED',(Path(self.tmp.name)/'error.json').read_text())

    def test_models_are_not_pooled_without_model_input(self):
        rows = [r | {'model':f'M{i}', 'title':f'바이타믹스 M{i} 블렌더'} for i,r in enumerate(self.rows)]
        listings.atomic_csv(listings.normalize(rows),self.db)
        result=price.estimate('블렌더',listings_path=self.db,allow_onbid=False,allow_live=False)
        self.assertIsNone(result['mid'])

    def test_production_failure_does_not_use_new_price_depreciation(self):
        with patch.object(listings,'LISTINGS',self.db), \
             patch('crawler.live_rows',side_effect=TimeoutError), \
             patch('price.new_price_estimate',side_effect=AssertionError('추정가 우회 금지')):
            self.checks[0]['status']='pending'; self.write_checks()
            result=price.estimate('블렌더','바이타믹스','콰이어트원',
                verification_path=self.ledger,allow_live=True)
        self.assertIsNone(result['mid'])


if __name__ == '__main__':
    unittest.main()

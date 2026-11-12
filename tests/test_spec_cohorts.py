import tempfile
import unittest
from pathlib import Path
import listings
import price


class SpecCohortTest(unittest.TestCase):
    def quote(self, specs):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[dict(item='블렌더',manufacturer='바이타믹스',model='콰이어트원',spec=spec,
                title='바이타믹스 콰이어트원 블렌더',condition='중고',price=500000+i*100000,
                currency='KRW',sale_status='for_sale',price_basis='asking',quantity=1,
                source='격리 합성 테스트',url=f'https://example.org/{i}',
                collected_at=listings.today().isoformat(),is_sample='false') for i,spec in enumerate(specs)]
            path=Path(tmp)/'l.csv';listings.atomic_csv(listings.normalize(rows),path)
            return price.estimate('블렌더','바이타믹스','콰이어트원',listings_path=path,allow_onbid=False,allow_live=False)

    def test_different_configurations_do_not_make_three_samples(self):
        result=self.quote(['','', '컨테이너 2개'])
        self.assertEqual(result['n'],2)
        self.assertIsNone(result['mid'])

    def test_unknown_spec_group_is_separate_and_low_confidence(self):
        result=self.quote(['','','','컨테이너 2개'])
        self.assertEqual(result['n'],3)
        self.assertEqual(result['confidence'],'낮음')
        self.assertIn('사양 미확인군',result['match'])
        self.assertTrue(all(not row['spec'] for row in result['evidence']))

"""DB 지적 사항 반영 검증: 용량 구분, 실시간 검색 조건, 판매 상태 재확인, 부족 현황, 사람 검증. 임시 폴더만 사용."""
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import crawler
import listings
import price
from test_prices import record


class SpecTest(unittest.TestCase):
    def test_units_unified(self):
        self.assertEqual(listings.spec_key('50키로'), listings.spec_key('50 KG'))
        self.assertEqual(listings.spec_key('2 group'), '2그룹')
        self.assertEqual(listings.spec_key('1800×750mm'), listings.spec_key('1800*750'))
        self.assertEqual(listings.spec_from_title('카이저 IMK-3051 중고'), '')  # 단위 없는 숫자는 사양 아님

    def test_title_spec_separates_capacity(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'listings.csv'
            rows = [record(i, item='제빙기', manufacturer='카이저', model='', spec='',
                           title=f'카이저 제빙기 {kg}키로', price=300000 + i * 1000)
                    for i, kg in enumerate([50, 50, 50, 100, 100, 100])]
            listings.save(rows, path, path.with_name('t.csv'))
            got = price.estimate('제빙기', '카이저', spec='50kg', listings_path=path, allow_onbid=False, allow_live=False)
            self.assertEqual(got['n'], 3)
            self.assertTrue(all(e['spec'] == '50kg' for e in got['evidence']))


class LiveFilterTest(unittest.TestCase):
    def live(self, titles):
        return [record(i, manufacturer='', model='', spec='', title=t, source='헬로마켓 (실시간 검색)')
                for i, t in enumerate(titles)]

    def test_live_requires_brand(self):
        rows = self.live(['훼마 에스프레소 머신'] * 2 + ['씨메 에스프레소 머신'] * 3)
        with tempfile.TemporaryDirectory() as d, patch('crawler.live_rows', return_value=rows):
            got = price.estimate('커피머신', '훼마', listings_path=Path(d) / 'x.csv', allow_onbid=False, allow_live=True)
        self.assertIsNone(got['mid'])  # 훼마 2건뿐 → 다른 제조사로 채우지 않음

    def test_live_brand_match_label(self):
        rows = self.live(['씨메 에스프레소 머신 2그룹'] * 3)
        with tempfile.TemporaryDirectory() as d, patch('crawler.live_rows', return_value=rows):
            got = price.estimate('커피머신', '씨메', spec='2그룹', listings_path=Path(d) / 'x.csv', allow_onbid=False, allow_live=True)
        self.assertEqual(got['n'], 3)
        self.assertIn('제조사·사양', got['match'])


def page(availability, price_value=900000):
    return ('<script type="application/ld+json">{"@type":"Product","name":"라마르조코 Linea 에스프레소 머신",'
            f'"offers":{{"@type":"Offer","price":"{price_value}","priceCurrency":"KRW",'
            f'"availability":"https://schema.org/{availability}","itemCondition":"https://schema.org/UsedCondition"}}}}</script>')


class RefreshTest(unittest.TestCase):
    def test_status_updates(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'listings.csv'
            urls = [f'https://www.hellomarket.com/item/{n}' for n in range(4)]
            listings.save([record(i, source='헬로마켓', url=u, price=1000000) for i, u in enumerate(urls)],
                          path, path.with_name('price_table.csv'))
            responses = {urls[0]: page('InStock'), urls[1]: page('InStock', 800000), urls[2]: page('SoldOut'), urls[3]: 404}

            def fake_fetch(url):
                if url.endswith('robots.txt'):
                    return 'User-agent: *\nAllow: /'
                r = responses[url]
                if r == 404:
                    raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
                return r
            with patch('crawler.fetch', side_effect=fake_fetch), patch('crawler.time.sleep'):
                out = crawler.refresh(path, log_path=Path(d) / 'log.csv')
            frame = listings.read(path).set_index('url')
            self.assertEqual(out['removed'], 1)
            self.assertNotIn(urls[3], frame.index)
            self.assertEqual(frame.at[urls[1], 'price'], 800000)
            self.assertEqual(frame.at[urls[2], 'price_basis'], 'sold_posted')
            self.assertEqual(len(pd.read_csv(Path(d) / 'log.csv')), 4)


class ReviewTest(unittest.TestCase):
    def test_queue_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            path, queue, log = d / 'listings.csv', d / 'q.csv', d / 'log.csv'
            listings.save([record(i) for i in range(3)], path, d / 't.csv')
            self.assertEqual(crawler.review_queue(path, queue, log), 3)
            q = pd.read_csv(queue, dtype=str, keep_default_na=False)
            q.loc[0, ['확인결과', '확인자']] = ['제외', '신승우']
            q.loc[1, ['확인결과', '확인자', 'model']] = ['수정', '신승우', 'Linea PB']
            q.to_csv(queue, index=False)
            out = crawler.apply_review(queue, path, log)
            self.assertEqual((out['removed'], out['edited']), (1, 1))
            frame = listings.read(path)
            self.assertEqual(len(frame), 2)
            self.assertIn('Linea PB', set(frame.model))
            self.assertEqual(crawler.review_queue(path, queue, log), 1)  # 확인한 건은 다시 안 나옴

    def test_reviewer_required(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            listings.save([record(0)], d / 'l.csv', d / 't.csv')
            crawler.review_queue(d / 'l.csv', d / 'q.csv', d / 'log.csv')
            q = pd.read_csv(d / 'q.csv', dtype=str, keep_default_na=False)
            q.loc[0, '확인결과'] = '일치'
            q.to_csv(d / 'q.csv', index=False)
            with self.assertRaises(ValueError):
                crawler.apply_review(d / 'q.csv', d / 'l.csv', d / 'log.csv')


class CoverageTest(unittest.TestCase):
    def test_bases_counted_separately(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rows = [record(i) for i in range(2)] + [record(9 + i, price_basis='auction', sale_status='sold') for i in range(2)]
            listings.save(rows, d / 'l.csv', d / 't.csv')
            t = crawler.coverage(d / 'l.csv', d / 'c.csv')
            r = t[(t['단위'] == '품목') & (t['품목'] == '커피머신')].iloc[0]
            self.assertEqual((r['판매중_게시가'], r['공매_낙찰가'], r['시세계산']), (2, 2, '부족'))  # 2+2 를 합쳐 4로 세지 않음


if __name__ == '__main__':
    unittest.main()

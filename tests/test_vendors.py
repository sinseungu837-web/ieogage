"""전문업체 찾기: 카카오 API 를 흉내 내어 검색어·필터·대안 링크·견적 요청서 확인 (네트워크 없음)."""
import unittest
from pathlib import Path
from unittest.mock import patch

import vendors

ROOT = Path(__file__).resolve().parents[1]


def fake_call(path, key, **q):
    if path == 'search/address.json':
        return {'documents': [{'x': '127.0', 'y': '37.5', 'address_name': '서울 성북구'}]}
    return {'documents': [
        {'id': '1', 'place_name': '창대중고주방', 'category_name': '가정,생활 > 중고가전', 'phone': '02-1', 'road_address_name': 'A', 'distance': '2300', 'place_url': 'http://place.map.kakao.com/1'},
        {'id': '2', 'place_name': '다이소', 'category_name': '가정,생활 > 생활용품점 > 다이소', 'phone': '', 'address_name': 'B', 'distance': '100', 'place_url': 'u'},
        {'id': '1', 'place_name': '창대중고주방', 'category_name': '가정,생활 > 중고가전', 'phone': '', 'address_name': 'A', 'distance': '2300', 'place_url': 'u'}]}


class VendorTest(unittest.TestCase):
    def test_filters_irrelevant_and_dedupes(self):
        with patch.object(vendors, '_call', side_effect=fake_call):
            r = vendors.search('에스프레소 머신', '성북구', key='k')
        self.assertTrue(r['ok'])
        self.assertEqual([p['name'] for p in r['places']], ['창대중고주방'])  # 다이소 제외, 중복 제거
        self.assertEqual(r['queries'], ['중고주방', '커피머신'])

    def test_no_key_gives_map_links(self):
        with patch.object(vendors, 'api_key', return_value=None):
            r = vendors.search('의자', '성북구')
        self.assertFalse(r['ok'])
        self.assertTrue(r['links'][0].startswith('https://map.kakao.com/link/search/'))

    def test_aliases_and_disposal(self):
        self.assertEqual(vendors.queries_for('커피머신'), vendors.QUERIES['에스프레소 머신'])
        with patch.object(vendors, '_call', side_effect=fake_call):
            self.assertEqual(vendors.search('x', '성북구', key='k', disposal=True)['queries'], ['폐업 철거'])

    def test_quote_request_facts(self):
        text = vendors.quote_request([{'집기명': '제빙기', '제조사': '카이저', '모델명': 'IMK-3051', '개수': 1, '연식(년)': '3'}],
                                     [{'mid': 400000}])
        self.assertIn('제빙기 (카이저 IMK-3051) 1개 / 사용 3년 / 참고 중고가 개당 400,000원', text)
        self.assertIn('철거·운반', text)


if __name__ == '__main__':
    unittest.main()

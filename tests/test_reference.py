"""팀 조사 시세표 모드: 표 안의 품목만 탐지, 제조사·모델은 표 후보로 통일, 가격은 표에서만."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import llm
import price
import reference
from estimate_view import normalize_estimate


class ReferenceEstimateTest(unittest.TestCase):
    def test_levels(self):
        model = reference.estimate('커피머신', 'FAEMA', 'E98 UP')
        maker = reference.estimate('에스프레소 머신', '훼마')
        item = reference.estimate('에스프레소 머신')
        self.assertEqual((model['low'], model['high'], model['n']), (2200000, 3200000, 1))
        self.assertIn('동일 모델', model['match'])
        self.assertIn('동일 제조사', maker['match'])
        self.assertEqual(item['n'], 4)
        self.assertIn('제조사 미확인', item['match'])

    def test_aliases_and_unknown_maker_text(self):
        self.assertIsNotNone(reference.estimate('테이블 냉장고', '스타리온'))
        self.assertEqual(reference.match_maker('의자', '미확인'), '')
        self.assertEqual(reference.match_maker('제빙기', 'Hoshizaki (추정)'), '호시자키')

    def test_not_in_table(self):
        self.assertIsNone(reference.estimate('TV'))
        got = price.service_estimate('TV')
        self.assertIsNone(got['mid'])
        self.assertIn('시세표에 없는', got['source'])

    def test_single_row_passes_display_check(self):
        out = normalize_estimate('제빙기', reference.estimate('제빙기', '카이저'))
        self.assertEqual(out['n'], 1)
        self.assertIsNotNone(out['mid'])
        self.assertTrue(out['low'] <= out['mid'] <= out['high'])


class DetectFilterTest(unittest.TestCase):
    def run_detect(self, items):
        resp = SimpleNamespace(text=json.dumps({'items': items}, ensure_ascii=False))
        with patch('llm._generate', return_value=resp), patch('llm._model', return_value='m'):
            return llm.detect_all(Image.new('RGB', (1000, 1000)))

    def test_only_table_items_and_brand_normalized(self):
        det, result = self.run_detect([
            {'name': '에스프레소 머신', 'box_2d': [100, 100, 400, 400], 'brand': 'FAEMA', 'model': 'E98 UP 2그룹', 'brand_basis': '로고'},
            {'name': '의자', 'box_2d': [500, 100, 800, 200], 'brand': '톤', 'model': '체어 14 (No.14)', 'brand_basis': '형태 예측'},
            {'name': '의자', 'box_2d': [500, 300, 800, 400], 'brand': 'Tolix', 'model': 'A Chair'},
            {'name': '화분', 'box_2d': [100, 600, 300, 700], 'brand': '', 'model': ''},
            {'name': '제빙기', 'box_2d': [600, 600, 900, 900], 'brand': '없는회사', 'model': 'X'},
        ])
        labels = [o['label'] for o in det['objects']]
        self.assertNotIn('화분', labels)
        machine, chair1, chair2, ice = det['objects']
        self.assertEqual((machine['brand'], machine['model'], machine['brand_basis'], machine['spec']),
                         ('훼마', 'E98 UP 2그룹', '로고', '2그룹'))
        self.assertEqual((chair1['brand'], chair2['brand']), ('톤', '톨릭스'))  # 다르게 생긴 의자 → 다른 제조사
        self.assertEqual((ice['brand'], ice['brand_basis']), ('', ''))  # 후보 밖 이름은 버림 → 사용자가 입력


if __name__ == '__main__':
    unittest.main()

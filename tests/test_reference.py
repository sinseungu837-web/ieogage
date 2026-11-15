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
        self.assertEqual((model['low'], model['mid'], model['high'], model['n']), (2000000, 2400000, 2800000, 1))
        self.assertIn('동일 모델', model['match'])
        self.assertIn('동일 제조사', maker['match'])
        self.assertEqual(maker['n'], 3)
        self.assertIn('제조사 미확인', item['match'])
        self.assertTrue(item['low'] < item['high'])

    def test_aliases_and_unknown_maker_text(self):
        self.assertIsNotNone(reference.estimate('테이블 냉장고', '스타리온'))
        self.assertEqual(reference.match_maker('의자', '미확인'), '')
        self.assertEqual(reference.match_maker('제빙기', 'Hoshizaki (추정)'), '호시자키')

    def test_not_in_table(self):
        self.assertIsNone(reference.estimate('화분'))
        got = price.service_estimate('화분')
        self.assertIsNone(got['mid'])
        self.assertIn('시세표에 없는', got['source'])

    def test_single_row_passes_display_check(self):
        out = normalize_estimate('제빙기', reference.estimate('제빙기', '아이스트로'))  # 제조사당 1모델
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
            {'name': '에스프레소 머신', 'box_2d': [100, 100, 400, 400], 'brand': 'FAEMA', 'model': 'E98 UP 2GR', 'brand_basis': '로고'},
            {'name': '의자', 'box_2d': [500, 100, 800, 200], 'brand': '톤', 'model': '체어 14 (No.14)', 'brand_basis': '형태 예측'},
            {'name': '의자', 'box_2d': [500, 300, 800, 400], 'brand': 'Tolix', 'model': 'A Chair'},
            {'name': '화분', 'box_2d': [100, 600, 300, 700], 'brand': '', 'model': ''},
            {'name': '제빙기', 'box_2d': [600, 600, 900, 900], 'brand': '없는회사', 'model': 'X'},
        ])
        labels = [o['label'] for o in det['objects']]
        self.assertIn('화분', labels)  # 시세표에 없는 물건도 목록에 남김 (2026-11-15), 제조사는 비움
        pot = next(o for o in det['objects'] if o['label'] == '화분')
        self.assertEqual(pot['brand'], '')
        machine, chair1, chair2, ice = [o for o in det['objects'] if o['label'] != '화분']
        self.assertEqual((machine['brand'], machine['model'], machine['brand_basis'], machine['spec']),
                         ('훼마', 'E98 UP 2GR', '로고', '2그룹'))
        self.assertEqual((chair1['brand'], chair2['brand']), ('톤', '톨릭스'))  # 다르게 생긴 의자 → 다른 제조사
        self.assertEqual((ice['brand'], ice['brand_basis']), ('', ''))  # 후보 밖 이름은 버림 → 사용자가 입력


if __name__ == '__main__':
    unittest.main()


class ReloadTest(unittest.TestCase):
    def test_table_change_is_picked_up(self):
        import os, shutil, tempfile, time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ref.csv'
            shutil.copy(reference.PATH, path)
            with patch.object(reference, 'PATH', path):
                self.assertTrue(any(r['manufacturer'] == '톤' for r in reference.rows()))
                text = path.read_text(encoding='utf-8-sig').replace('톤,', '바뀐회사,')
                path.write_text(text, encoding='utf-8-sig')
                os.utime(path, ns=(time.time_ns() + 10**9,) * 2)
                makers = {r['manufacturer'] for r in reference.rows()}
                self.assertIn('바뀐회사', makers)
                self.assertNotIn('톤', makers)


class DesignGroupTest(unittest.TestCase):
    def run_detect(self, items):
        resp = SimpleNamespace(text=json.dumps({'items': items}, ensure_ascii=False))
        with patch('llm._generate', return_value=resp), patch('llm._model', return_value='m'):
            return llm.detect_all(Image.new('RGB', (1000, 1000)))[0]['objects']

    def chair(self, x, group, brand, rank):
        return {'name': '의자', 'box_2d': [500, x, 800, x + 80], 'design_group': group, 'brand': brand, 'brand_rank': rank}

    def test_different_design_gets_different_maker(self):
        objs = self.run_detect([self.chair(0, 'A', '톤', ['톤', '톨릭스']), self.chair(100, 'A', '톤', ['톤']),
                                self.chair(200, 'A', '톤', ['톤']), self.chair(300, 'B', '톤', ['톤', '톨릭스'])])
        brands = [o['brand'] for o in objs]
        self.assertEqual(brands[:3], ['톤'] * 3)  # 많은 그룹이 첫 후보 유지
        self.assertEqual(brands[3], '톨릭스')     # 다르게 생긴 그룹은 다음 후보

    def test_same_design_unified(self):
        objs = self.run_detect([self.chair(0, 'A', '톤', ['톤']), self.chair(100, 'A', '톤', ['톤']),
                                self.chair(200, 'A', '톨릭스', ['톨릭스'])])
        self.assertEqual({o['brand'] for o in objs}, {'톤'})  # 같은 생김새는 다수결로 통일

    def test_no_group_keeps_gemini_brands(self):
        objs = self.run_detect([{'name': '의자', 'box_2d': [500, x, 800, x + 80], 'brand': '톤'} for x in (0, 100, 200)])
        self.assertEqual({o['brand'] for o in objs}, {'톤'})


class CountModeTest(unittest.TestCase):
    def run_detect(self, mode):
        items = [{'name': '의자', 'box_2d': [562, 410, 1000, 998], 'brand': '톤'},   # 앞쪽 큰 의자
                 {'name': '의자', 'box_2d': [875, 408, 1000, 513], 'brand': '톤'},   # 그 안에 일부만 보이는 뒤 의자
                 {'name': '의자', 'box_2d': [100, 100, 300, 200], 'brand': '톤'},
                 {'name': '의자', 'box_2d': [102, 101, 301, 201], 'brand': '톤'}]   # 같은 의자를 두 번 그림
        resp = SimpleNamespace(text=json.dumps({'items': items}, ensure_ascii=False))
        with patch('llm._generate', return_value=resp), patch('llm._model', return_value='m'):
            return len(llm.detect_all(Image.new('RGB', (1000, 1000)), count_mode=mode)[0]['objects'])

    def test_dedupe_keeps_partly_hidden_chair(self):
        self.assertEqual(self.run_detect('dedupe'), 3)  # 실측 버그: 예전 규칙은 뒤 의자를 지워 2개

    def test_all_mode_keeps_everything_but_exact_double(self):
        self.assertEqual(self.run_detect('all'), 3)


class AgePriceTest(unittest.TestCase):
    """시세 범위 + 연식 위치 (2026-11-15 팀 확정 데이터 기준)."""
    def test_position_in_range(self):
        self.assertEqual(reference.position_by_age(2000000, 2800000, 1)[:3], (2800000, 2000000, 2800000))
        self.assertEqual(reference.position_by_age(2000000, 2800000, 3)[0], 2400000)
        self.assertEqual(reference.position_by_age(2000000, 2800000, 5)[0], 2000000)

    def test_over_five_years_and_floor(self):
        mid, low, high, _ = reference.position_by_age(2000000, 2800000, 8, new_price=4850000)
        self.assertEqual(mid, 1400000)            # 하단 200만 × (1 - 0.1×3)
        self.assertTrue(low <= mid <= high)
        self.assertEqual(reference.position_by_age(2000000, 2800000, 30, new_price=4850000)[0], 485000)  # 신품가 10%

    def test_parse_years(self):
        self.assertEqual(reference.parse_years('3년'), 3.0)
        self.assertEqual(reference.parse_years('1년 미만'), 0.5)
        self.assertIsNone(reference.parse_years(''))

    def test_estimate_with_years_has_range_and_calc(self):
        e = reference.estimate('커피머신', '훼마', 'E98 UP', '', 3)
        self.assertEqual((e['low'], e['mid'], e['high']), (2000000, 2400000, 2800000))
        self.assertTrue(any('비례 위치' in s for s in e['calc']))
        self.assertTrue(any('근거' in s for s in e['calc']))

    def test_new_items_priced(self):
        for name in ('포스기', '키오스크', 'TV', '식기세척기', '에어컨', '싱크대'):
            self.assertIsNotNone(reference.estimate(name), name)


class ExcludeStructureTest(unittest.TestCase):
    def test_people_and_building_removed_but_mounted_items_kept(self):
        names = ['사람', '벽', '천장', '창문', '천장형 에어컨', '벽걸이 TV', '의자']
        items = [{'name': n, 'box_2d': [100, i * 120, 300, i * 120 + 100], 'brand': ''} for i, n in enumerate(names)]
        resp = SimpleNamespace(text=json.dumps({'items': items}, ensure_ascii=False))
        with patch('llm._generate', return_value=resp), patch('llm._model', return_value='m'):
            labels = [o['label'] for o in llm.detect_all(Image.new('RGB', (1000, 1000)))[0]['objects']]
        for gone in ('사람', '벽', '천장', '창문'):
            self.assertNotIn(gone, labels)
        self.assertIn('의자', labels)
        self.assertIn('벽걸이 TV', labels)
        self.assertEqual(len(labels), 3)  # 천장형 에어컨(시세표 이름 '에어컨'으로 통일 가능)·벽걸이 TV·의자

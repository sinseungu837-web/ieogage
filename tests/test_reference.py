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
        out = normalize_estimate('제빙기', reference.estimate('제빙기', '스카츠맨'))  # 제조사당 1모델 (아이스트로는 2026-11-16 ICI-100 추가로 2모델)
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
    """법정 정률법 (2026-11-16 팀 지시문: 법인세법 시행령 제26조, 시행규칙 별표4·5·6)."""
    def test_declining_balance_5_year_rate(self):
        r = 0.451  # 별표4 내용연수 5년 정률
        self.assertAlmostEqual(reference.declining_balance(r, 12), 0.549)
        self.assertAlmostEqual(reference.declining_balance(r, 36), 0.549 ** 3)
        self.assertIsNone(reference.declining_balance(r, 60))   # 5년차: 잔액 4.99% ≤ 5% → 상각 종료
        self.assertIsNone(reference.declining_balance(r, 72))   # 내용연수 초과
        self.assertAlmostEqual(reference.declining_balance(r, 59), 0.549 ** 4 * (1 - r * 11 / 12))  # 종료 직전

    def test_declining_balance_8_year_rate(self):
        r = 0.313  # 별표4 내용연수 8년 정률 (별표6 음식점 및 주점업)
        for years in (1, 3, 5):
            self.assertAlmostEqual(reference.declining_balance(r, years * 12), 0.687 ** years)
        self.assertIsNone(reference.declining_balance(r, 96))   # 8년차: 잔액 4.96% ≤ 5% → 상각 종료

    def test_months_and_zero_year(self):
        self.assertEqual(reference.months_of(0), 1)       # 1개월 미만은 1개월 (제26조 제9항)
        self.assertEqual(reference.months_of(1.5), 18)
        self.assertAlmostEqual(reference.declining_balance(0.451, 18), 0.549 * (1 - 0.451 * 6 / 12))  # 월수 비례
        self.assertAlmostEqual(reference.declining_balance(0.451, 1), 1 - 0.451 / 12)

    def test_confirmed_asset_uses_declining_balance(self):
        e = reference.estimate('의자', '톤', '체어 14', '', 3)   # 신품가 185,000 · 비품 5년
        self.assertEqual(e['method'], '법정 정률법')
        # 기준내용연수 5년 0.451 → 30,612 / 범위: 내용연수범위 4년 0.528 → 19,453, 6년 0.394 → 41,170
        self.assertEqual((e['low'], e['mid'], e['high']), (19000, 31000, 41000))
        self.assertIn('별표4', e['law'])
        self.assertTrue(any('5년 0.451' in s and '4년 0.528' in s and '6년 0.394' in s for s in e['calc']))
        self.assertTrue(any('185,000 × (1 − 0.451)^3' in s for s in e['calc']))
        one = reference.estimate('의자', '톤', '체어 14', '', 1)
        self.assertEqual((one['low'], one['mid'], one['high']), (87000, 102000, 112000))
        four = reference.estimate('의자', '톤', '체어 14', '', 4)   # 4년 상각률로는 상각 종료 → 하한은 제7항 장부가액
        self.assertEqual((four['low'], four['mid']), (1000, 17000))
        self.assertTrue(any('제7항' in s for s in four['calc']))
        self.assertTrue(any('시행령 제28조 제1항 제2호' in s for s in e['calc']))
        self.assertTrue(any('미상각잔액' in s for s in e['calc']))

    def test_fallbacks_to_survey_mid(self):
        ended = reference.estimate('의자', '톤', '체어 14', '', 5)    # 법정 상각 종료
        self.assertEqual((ended['method'], ended['mid']), ('조사 시세 중간값', 75000))
        self.assertTrue(any('상각 종료' in s for s in ended['calc']))
        none = reference.estimate('의자', '톤', '체어 14')            # 연식·개업일 미입력
        self.assertEqual((none['method'], none['mid'], none['low'], none['high']), ('조사 시세 중간값', 75000, 60000, 90000))
        unclear = reference.estimate('커피머신', '훼마', 'E98 UP', '', 3)   # 자산 분류 미확정
        self.assertEqual((unclear['method'], unclear['mid']), ('조사 시세 중간값', 2400000))
        self.assertTrue(any('분류 미확정' in s for s in unclear['calc']))
        with patch.object(reference, 'rows', return_value=[dict(r, new_price=None) for r in reference.rows()]):
            no_new = reference.estimate('의자', '톤', '체어 14', '', 3)   # 신품가 없음
        self.assertEqual((no_new['method'], no_new['mid']), ('조사 시세 중간값', 75000))
        self.assertIsNone(reference.estimate('화분', '', '', '', 3))     # 조사 시세도 없음 → 산정 불가

    def test_asset_mapping_covers_all_items(self):
        import csv
        with open(reference.ASSET_PATH, encoding='utf-8-sig') as f:
            mapped = {r['item']: r for r in csv.DictReader(f)}
        self.assertEqual(set(mapped), set(reference.items()))
        for r in mapped.values():   # 확인된 품목은 별표4 값만 (5년 0.451, 8년 0.313)
            key = (r['life_years'], r['db_rate'], r['life_min'], r['rate_short'], r['life_max'], r['rate_long'])
            if r['confirmed'] == 'Y':   # 별표4: 4년 0.528 · 5년 0.451 · 6년 0.394 / 6년 0.394 · 8년 0.313 · 10년 0.259
                self.assertIn(key, {('5', '0.451', '4', '0.528', '6', '0.394'), ('8', '0.313', '6', '0.394', '10', '0.259')})
            else:
                self.assertEqual(key, ('',) * 6)

    def test_parse_years(self):
        self.assertEqual(reference.parse_years('3년'), 3.0)
        self.assertEqual(reference.parse_years('1년 미만'), 0.5)
        self.assertEqual(reference.parse_years('1년 1개월') * 12, 13)
        self.assertEqual(reference.parse_years('8개월') * 12, 8)
        self.assertIsNone(reference.parse_years(''))
        self.assertIsNone(reference.parse_years('-3'))   # 음수 연식 무효

    def test_user_maker_model_matches_table_product(self):
        # 물품명이 시세표에 없어도 제조사+모델이 맞으면 그 제품 시세 (2026-11-16)
        e = reference.estimate('주방기기', '훼마', 'E98 UP', '', 3)
        self.assertEqual((e['low'], e['mid'], e['high']), (2000000, 2400000, 2800000))
        chair = reference.estimate('가구', '톤', '체어 14', '', 3)   # 품목명이 달라도 매칭된 제품의 자산 분류 적용
        self.assertEqual((chair['method'], chair['mid']), ('법정 정률법', 31000))
        self.assertIn('동일 제품', e['match'])
        # AI 가 다른 품목으로 잡았어도 입력 모델 우선 (그라인더로 잡힌 훼마 머신)
        self.assertEqual(reference.estimate('커피 그라인더', 'FAEMA', 'E98 UP 2GR')['n'], 1)
        # 가까운 모델 우선: E65S 입력이 E65S GbW 로 가지 않음
        self.assertEqual(reference.find_product('말코닉', 'E65S')[0]['model'], 'E65S')
        # 영문 별칭도 매칭
        self.assertEqual(reference.find_product('Mahlkonig', 'EK43S')[0]['model'], 'EK43S')
        # 제조사나 모델 하나만 있으면 품목 밖 매칭 안 함
        self.assertIsNone(reference.estimate('화분', '훼마', ''))
        self.assertIsNone(reference.estimate('화분', '', 'E98 UP'))
        self.assertIsNone(reference.estimate('화분', '없는회사', 'E98 UP'))

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

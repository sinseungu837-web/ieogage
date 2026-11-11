"""가격 모듈 불일치와 조회 실패가 최종 화면을 중단시키지 않는지 검증한다."""
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import price
from estimate_view import safe_estimate

ROOT = Path(__file__).resolve().parents[1]
ROW = {'집기명': '제빙기', '제조사': '호시자키', '개수': 2}
GOOD = {'low': 100000, 'mid': 150000, 'high': 200000, 'n': 3,
        'source': '합성 데이터 · 테스트 전용', 'evidence': []}


class SafeEstimateTest(unittest.TestCase):
    def test_missing_backend_helpers_cannot_break_fallback(self):
        backend = types.SimpleNamespace()  # empty_estimate / db_revision / estimate 모두 없음
        result = safe_estimate(ROW, backend, lambda *args: backend.estimate(*args))
        self.assertIsNone(result['mid'])
        self.assertIn('가격 근거 부족', result['source'])

    def test_revision_failure_does_not_prevent_success(self):
        backend = types.SimpleNamespace(db_revision=Mock(side_effect=OSError))
        lookup = Mock(return_value=GOOD)
        result = safe_estimate(ROW, backend, lookup)
        self.assertEqual(result['mid'], 150000)
        self.assertIsNone(lookup.call_args.args[-1])

    def test_legacy_result_gets_all_display_fields(self):
        result = safe_estimate(ROW, types.SimpleNamespace(), lambda *args: GOOD)
        self.assertEqual(result['n'], 3)
        for key in ('match', 'confidence', 'collected_at', 'price_basis', 'search_url'):
            self.assertIn(key, result)

    def test_invalid_results_become_unpriced(self):
        for value in (None, {}, {'mid': 5}, GOOD | {'low': math.nan},
                      GOOD | {'n': 1}, GOOD | {'n': '3'}, GOOD | {'low': 300000},
                      GOOD | {'high': None}):
            with self.subTest(value=value):
                result = safe_estimate(ROW, types.SimpleNamespace(), lambda *args: value)
                self.assertIsNone(result['mid'])
                self.assertIn('가격 근거 부족', result['source'])

    def test_logs_do_not_include_exception_url_or_key(self):
        def fail(*args):
            raise RuntimeError('https://api.example/?serviceKey=DO_NOT_LOG_THIS')
        with self.assertLogs('estimate_view', level='WARNING') as logs:
            safe_estimate(ROW, types.SimpleNamespace(__file__='/app/price.py'), fail)
        self.assertNotIn('DO_NOT_LOG_THIS', ''.join(logs.output))
        self.assertIn('RuntimeError', ''.join(logs.output))


class FinalPageTest(unittest.TestCase):
    def render(self):
        from streamlit.testing.v1 import AppTest
        import streamlit as st
        st.cache_data.clear()
        script = f'''
import runpy
from PIL import Image
ns = runpy.run_path({str(ROOT / 'app.py')!r})
last = {{'image': Image.new('RGB', (500, 300), 'white'),
        'det': {{'objects': [{{'id': 1, 'box': [10,10,100,100]}}, {{'id': 2, 'box': [120,10,210,100]}}]}},
        'box_names': {{1: '제빙기', 2: '의자'}}, 'manufacturers': {{1: '호시자키'}}}}
ns['final_page'](last)
'''
        return AppTest.from_string(script).run(timeout=20)

    def test_missing_module_functions_renders_final_page(self):
        with patch.dict(sys.modules, {'price': types.SimpleNamespace()}):
            page = self.render()
        self.assertEqual(len(page.exception), 0)
        self.assertTrue(any('— 원' in value.value for value in page.markdown))
        prices = page.dataframe[1].value
        self.assertTrue(prices['개당 대표 가격 (원)'].isna().all())
        self.assertTrue(prices['근거'].str.contains('가격 근거 부족').all())

    def test_one_item_fails_other_price_survives(self):
        def lookup(name, *args):
            if name == '의자':
                raise AttributeError('모듈 불일치 재현')
            return GOOD
        with patch.object(price, 'estimate', side_effect=lookup), patch.object(price, 'empty_estimate', side_effect=AssertionError('호출 금지')):
            page = self.render()
        self.assertEqual(len(page.exception), 0)
        prices = page.dataframe[1].value
        self.assertEqual(prices.iloc[0]['개당 대표 가격 (원)'], 150000)
        self.assertTrue(math.isnan(prices.iloc[1]['개당 대표 가격 (원)']))
        self.assertTrue(any('부분 견적' in value.value for value in page.warning))
        self.assertTrue(any('100,000원 ~ 200,000원' in value.value for value in page.markdown))

    def test_empty_real_db_without_api_key(self):
        with patch.object(price, '_key', return_value=None):
            page = self.render()
        self.assertEqual(len(page.exception), 0)
        self.assertTrue(page.dataframe[1].value['개당 대표 가격 (원)'].isna().all())


if __name__ == '__main__':
    unittest.main()

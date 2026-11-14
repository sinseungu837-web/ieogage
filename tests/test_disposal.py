import copy
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from disposal import sync_states, row_key, grouped, reference_total, fingerprint, sale_facts, validate_draft

ROOT = Path(__file__).resolve().parents[1]
ROWS = [{'집기명': '의자', '제조사': '이케아', '모델명': '테르예', '개수': 2, 'box_ids': [1, 2], '연식(년)': '3'},
        {'집기명': '의자', '제조사': '톤', '개수': 1, 'box_ids': [3]}]


class DisposalTest(unittest.TestCase):
    def setUp(self):
        self.last = {}
        self.rows = copy.deepcopy(ROWS)
        self.states = sync_states(self.last, self.rows)
        self.state = self.states['1,2']

    def test_selection_totals_and_methods(self):
        estimates = [{'mid': 100}, {'mid': None}]
        self.assertEqual(reference_total(self.rows, estimates, self.states), 200)
        self.state['selected'] = False
        self.assertEqual(reference_total(self.rows, estimates, self.states), 0)
        self.state.update(selected=True, method='양도·나눔')
        self.assertEqual(len(grouped(self.rows, self.states)['양도·나눔']), 1)
        self.assertEqual(reference_total(self.rows, estimates, self.states), 200)
        self.assertEqual(len(grouped(self.rows, self.states)['미선택']), 1)

    def test_rename_and_method_round_trip_preserve_draft(self):
        self.state.update(title='직접 수정', method='개인 중고거래', price=300)
        before = fingerprint(self.rows[0], self.state, {'mid': 100})
        self.rows[0]['집기명'] = '테이블'
        self.rows[0]['제조사'] = '사용자 입력'
        self.state['method'] = '전문업체 매각'
        sync_states(self.last, self.rows)
        self.state['method'] = '개인 중고거래'
        self.assertEqual(self.state['title'], '직접 수정')
        self.assertNotEqual(before, fingerprint(self.rows[0], self.state, {'mid': 100}))

    def test_deleted_and_split_groups_never_attach_old_drafts(self):
        self.state.update(title='옛 묶음', selected=False, method='전문업체 매각')
        self.rows[0].update(box_ids=[1], 개수=1)
        states = sync_states(self.last, self.rows)
        self.assertEqual(states['1']['title'], '')
        self.assertFalse(states['1']['selected'])
        self.assertEqual(states['1']['method'], '전문업체 매각')
        self.assertEqual(self.last['disposal_archive']['1,2']['title'], '옛 묶음')
        self.assertEqual(states['3']['title'], '')
        self.assertNotEqual(row_key(self.rows[0]), row_key(self.rows[1]))

    def test_grounded_facts_and_unknown_price(self):
        titles, facts = sale_facts(self.rows[0], self.state)
        draft = validate_draft({'title_index': 0, 'sentence_order': list(range(len(facts)))}, titles, facts)
        self.assertIn('2개', draft['body'])
        self.assertIn('3년', draft['body'])
        self.assertIn('미정', draft['body'])
        self.assertNotIn('정상 작동 보장', draft['body'])
        for bad in ({'title_index': 99, 'sentence_order': []},
                    {'title_index': 0, 'sentence_order': [0]},
                    {'title_index': True, 'sentence_order': list(range(len(facts)))},
                    {'title': '상태 최상', 'body': '정품 보증'}, []):
            with self.assertRaises(ValueError):
                validate_draft(bad, titles, facts)

    def test_actual_gemini_adapter_mock(self):
        import llm
        self.state.update(price=123000, notes='긁힘 있음', accessories='전원선')
        titles, facts = sale_facts(self.rows[0], self.state)
        with patch.object(llm, 'get_api_key', return_value='mock'), patch.object(llm, '_generate', return_value=SimpleNamespace(text=json.dumps(
                {'title_index': 1, 'sentence_order': list(reversed(range(len(facts))))}))) as call:
            draft = llm.generate_sale_post(self.rows[0], self.state)
        self.assertIn('123,000원', draft['body'])
        self.assertIn('긁힘 있음', draft['body'])
        self.assertIn('전원선', draft['body'])
        self.assertNotIn('참고 시세', call.call_args.kwargs['contents'])


class DisposalUITest(unittest.TestCase):
    def page(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string('''
import streamlit as st
import disposal_view
from disposal import reference_total
rows = %r
last = st.session_state.setdefault('last', {'key': 'fixture'})
states = disposal_view.selection(last, rows)
estimates = [{'mid': 100}, {'mid': None}]
st.metric('참고 가치', reference_total(rows, estimates, states))
if st.checkbox('결과 화면 표시', value=True):
    disposal_view.results(last, rows, estimates, states)
''' % ROWS).run()

    def set_method(self, page, method):
        from disposal_view import apply_selection_edits
        apply_selection_edits(page.session_state['last'], ['1,2', '3'], {0: {'처분 방식': method}})
        page.run()

    def test_select_edit_regenerate_preserve_and_failure(self):
        import llm
        page = self.page()
        self.assertFalse(page.exception)
        page.button[1].click().run()
        self.assertEqual(page.metric[0].value, '0')
        page.button[0].click().run()
        self.assertEqual(page.metric[0].value, '200')
        self.set_method(page, '개인 중고거래')
        with patch.object(llm, 'generate_sale_post', return_value={'title': '초안', 'body': '본문'}):
            page.button(key='sale:fixture:1,2:generate').click().run()
        page.text_input(key='sale:fixture:1,2:title').input('내가 수정').run()
        page.checkbox[-1].uncheck().run()
        page.checkbox[-1].check().run()
        self.assertEqual(page.text_input[0].value, '내가 수정')
        self.set_method(page, '전문업체 매각')
        self.set_method(page, '개인 중고거래')
        self.assertEqual(page.text_input[0].value, '내가 수정')
        with patch.object(llm, 'generate_sale_post', return_value={'title': '새 초안', 'body': '새 본문'}):
            page.button(key='sale:fixture:1,2:generate').click().run()
        self.assertEqual(page.text_input[0].value, '내가 수정')
        page.button(key='sale:fixture:1,2:apply').click().run()
        self.assertEqual(page.text_input[0].value, '새 초안')
        page.number_input[0].set_value(999).run()
        self.assertTrue(any('변경' in w.value for w in page.warning))
        self.assertEqual(page.text_input[0].value, '새 초안')
        self.assertEqual(len(page.code), 2)
        with patch.object(llm, 'generate_sale_post', side_effect=RuntimeError('private key')):
            page.button(key='sale:fixture:1,2:generate').click().run()
        self.assertEqual(page.text_input[0].value, '새 초안')
        self.assertNotIn('private key', page.error[0].value)
        self.assertFalse(page.exception)


class MobileRegressionTest(unittest.TestCase):
    def test_editor_updates_by_snapshot_identity(self):
        from disposal_view import apply_selection_edits
        last = {}
        states = sync_states(last, ROWS)
        apply_selection_edits(last, ['3', '1,2'], {0: {'대상': False, '처분 방식': '전문업체 매각'}})
        self.assertFalse(states['3']['selected'])
        self.assertEqual(states['3']['method'], '전문업체 매각')
        self.assertTrue(states['1,2']['selected'])
        # 삭제된 행 및 잘못된 선택은 무시한다.
        sync_states(last, ROWS[:1])
        apply_selection_edits(last, ['3', '1,2'], {0: {'대상': True}, 1: {'처분 방식': 'invalid'}})
        self.assertEqual(last['disposal']['1,2']['method'], '미선택')
        self.assertNotIn('3', last['disposal'])

    def test_sale_empty_and_invalid_responses(self):
        import llm
        state = sync_states({}, ROWS)['1,2']
        with patch.object(llm, 'get_api_key', return_value='mock'):
            for text in (None, '', 'not json', '{"title_index":0,"sentence_order":[0,0]}'):
                with patch.object(llm, '_generate', return_value=SimpleNamespace(text=text)):
                    with self.assertRaises(ValueError):
                        llm.generate_sale_post(ROWS[0], state)

    def test_safe_error_messages_and_missing_key(self):
        import llm
        for code in (400, 401, 403, 404, 429, 500, 503, 504):
            error = RuntimeError('SECRET URL KEY')
            error.code = code
            message = llm.sale_error_message(error)
            self.assertIn(str(code), message)
            self.assertNotIn('SECRET', message)
        with patch.object(llm, 'get_api_key', return_value=None), patch.object(llm, '_generate') as call:
            with self.assertRaises(llm.SaleConfigurationError):
                llm.generate_sale_post(ROWS[0], sync_states({}, ROWS)['1,2'])
            call.assert_not_called()

    def test_sales_retry_is_bounded_without_changing_detection_plan(self):
        import llm
        from google.genai.errors import ServerError
        client = SimpleNamespace(models=SimpleNamespace(generate_content=unittest.mock.Mock(
            side_effect=ServerError(503, {'error': {'message': 'busy', 'status': 'UNAVAILABLE'}}))))
        with patch.object(llm, '_client', return_value=client), patch('time.sleep'):
            with self.assertRaises(ServerError):
                llm._generate(model=llm.DEFAULT_MODEL, contents='test', max_attempts=2)
        self.assertEqual(client.models.generate_content.call_count, 2)

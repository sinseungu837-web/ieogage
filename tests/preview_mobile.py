"""5단계 모바일 테스트용 합성 집기. 탐지/API 호출 없이 상태와 UI만 검증한다."""
import runpy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
from PIL import Image
import llm
from disposal import sale_facts, validate_draft


def generate(row, state):
    if state['notes'] == '실패 테스트':
        raise RuntimeError('DO_NOT_EXPOSE')
    titles, facts = sale_facts(row, state)
    return validate_draft({'title_index': 0, 'sentence_order': list(range(len(facts)))}, titles, facts)

llm.generate_sale_post = generate
if 'last' not in st.session_state:
    st.session_state.last = {'key': 'mobile-fixture', 'image': Image.new('RGB', (600, 400), '#ddd'),
        'det': {'objects': [{'id': i, 'label': 'chair', 'box': [i*100, 20, i*100+90, 250]} for i in (1, 2)],
                'seconds': 0, 'truncated': False},
        'box_names': {1: '의자', 2: '테이블'}, 'manufacturers': {1: '이케아', 2: '이케아'},
        'models': {1: '테르예', 2: '독스타'}, 'result': {'items': [], 'excluded': []},
        'kept': [], 'dropped': [], 'next_id': 3, 'selected_box_id': None}
runpy.run_path(str(Path(__file__).resolve().parents[1] / 'app.py'), run_name='__main__')

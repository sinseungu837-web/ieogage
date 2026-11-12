"""합성 가격/AI 데이터 전용 브라우저 프리뷰. 운영 CSV·실제 API는 사용하지 않는다."""
import runpy
import sys
import tempfile
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
from PIL import Image
import listings
import price_quality

sys.modules['llm'] = types.SimpleNamespace(get_api_key=lambda: 'test')
sys.modules['vision'] = types.SimpleNamespace(DETECTOR='gemini')
# 리런 중 유지할 임시 디렉터리. 테스트를 실행한 프로세스 외에는 접근되지 않는다.
@st.cache_resource
def fixture_db():
    tmp = tempfile.TemporaryDirectory(prefix='ieogage-test-')
    path = Path(tmp.name) / 'listings.csv'
    rows = []
    for brand, model, start in [('호시자키', 'A', 1000000), ('스코츠맨', 'B', 2000000)]:
        for i in range(3):
            rows.append(dict(item='제빙기', manufacturer=brand, model=model, spec='',
                title=f'{brand} 제빙기 {model}', condition='중고', price=start+i*200000,
                currency='KRW', sale_status='for_sale', price_basis='asking', quantity=1,
                source='합성 데이터 · 테스트 전용', url=f'https://example.org/{model}/{i}',
                collected_at=date.today().isoformat(), is_sample='false'))
    # production() 경로 검증을 위한 테스트 전용 스냅샷. 운영 파일에 쓰지 않는다.
    frame = listings.normalize(rows)
    listings.atomic_csv(frame, path)
    import pandas as pd
    checks = [dict(url=r['url'], snapshot_sha256=price_quality.fingerprint(r), status='verified',
        checked_at=listings.today().isoformat(), availability='available', working_status='tested',
        published_at='', verification_note='격리 브라우저 합성 테스트 전용') for r in frame.to_dict('records')]
    listings.atomic_csv(pd.DataFrame(checks, columns=price_quality.COLUMNS), Path(tmp.name)/'checks.csv')
    return tmp, path

_tmp, listings.LISTINGS = fixture_db()
price_quality.LEDGER = listings.LISTINGS.with_name('checks.csv')
st.warning('브라우저 검증용 합성 데이터입니다. 실제 시세가 아닙니다.')
if 'last' not in st.session_state:
    st.session_state.last = {'key': 'synthetic-price-test', 'image': Image.new('RGB', (1000, 600), '#ddd'),
        'det': {'objects': [{'id': i, 'label': 'test', 'box': [i*150, 100, i*150+100, 350], 'task': 'OD'}
                           for i in range(1, 5)], 'seconds': 0, 'truncated': False},
        'kept': [], 'dropped': [], 'result': {'items': [], 'excluded': []}, 'next_id': 5,
        'box_names': {1: '제빙기', 2: '제빙기', 3: '제빙기', 4: '의자'},
        'manufacturers': {1: '호시자키', 2: '호시자키', 3: '스코츠맨'},
        'models': {3: 'B'}, 'selected_box_id': None}
runpy.run_path(str(Path(__file__).resolve().parents[1] / 'app.py'), run_name='__main__')

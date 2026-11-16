"""외부 AI 호출 없이 4단계 흐름을 검증하는 로컬 전용 실행 파일."""
import sys
import types
import runpy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def detect(*args):
    return {'objects': [{'id': 1, 'label': 'chair', 'box': [100, 100, 400, 400], 'task': 'OD'}],
            'seconds': 0, 'truncated': False}

sys.modules['vision'] = types.SimpleNamespace(
    DETECTOR='rfdetr', prepare=lambda image: image.convert('RGB'), load_model=lambda: (None, None),
    detect=detect, code_filter=lambda objects, size: (objects, []))
sys.modules['llm'] = types.SimpleNamespace(
    get_api_key=lambda: 'test',
    COUNT_MODES={'dedupe': '중복 제거 (권장)', 'all': '보이는 대로 모두'},
    detect_all=lambda image, count_mode='dedupe': (detect(), {'items': [{'name': '의자', 'count': 1,
        'source_ids': [1], 'category': '집기', 'in_dictionary': True, 'uncertain': False}], 'excluded': []}),
    classify_items=lambda objects: {'items': [{'name': '의자', 'count': 1, 'source_ids': [1],
                                             'category': '집기', 'in_dictionary': True, 'uncertain': False}], 'excluded': []},
    find_missing=lambda *args: ([], []), verify_ambiguous=lambda image, objects, result: result)
sys.modules['price'] = types.SimpleNamespace(service_estimate=lambda *args: {
    'low': None, 'mid': None, 'high': None, 'n': 0, 'source': '가격 근거 부족'})
runpy.run_path(str(Path(__file__).resolve().parents[1] / 'app.py'), run_name='__main__')

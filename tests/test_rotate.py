"""사진 입력 화면의 90° 회전 버튼."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RotateTest(unittest.TestCase):
    def test_rotate_buttons(self):
        from streamlit.testing.v1 import AppTest
        script = f'''
import runpy, streamlit as st
from PIL import Image
ns = runpy.run_path({str(ROOT / 'app.py')!r})
if 'photo_draft' not in st.session_state:
    st.session_state.photo_draft = {{'image': Image.new('RGB', (400, 300), 'white'), 'file_id': 'x'}}
ns['photo_page']()
'''
        at = AppTest.from_string(script, default_timeout=60).run()
        size = lambda: at.session_state['photo_draft']['image'].size
        self.assertEqual(size(), (400, 300))
        next(b for b in at.button if '오른쪽' in b.label).click().run()
        self.assertEqual(size(), (300, 400))
        next(b for b in at.button if '왼쪽' in b.label).click().run()
        self.assertEqual(size(), (400, 300))


if __name__ == '__main__':
    unittest.main()

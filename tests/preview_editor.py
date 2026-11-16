import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import types, runpy
import streamlit as st
from PIL import Image
sys.modules['llm'] = types.SimpleNamespace(get_api_key=lambda: 'test')
sys.modules['vision'] = types.SimpleNamespace(DETECTOR='rfdetr')
if 'last' not in st.session_state:
    st.session_state.last = {'key':'test-image','image':Image.new('RGB',(1000,600),'#ddd'),
      'det':{'objects':[{'id':1,'box':[100,100,400,400],'task':'OD'}],'seconds':0,'truncated':False},
      'kept':[],'dropped':[],'result':{'items':[{'name':'의자','source_ids':[1]}],'excluded':[]},
      'next_id':2,'box_names':{1:'의자'},'selected_box_id':None}
runpy.run_path(str(Path(__file__).resolve().parents[1]/'app.py'),run_name='__main__')

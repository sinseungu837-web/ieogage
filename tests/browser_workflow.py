from playwright.sync_api import sync_playwright, expect
from PIL import Image
from io import BytesIO
import os
import re
import csv
import io
# Run: streamlit run tests/preview_workflow.py --server.port 8502
# Then: python tests/browser_workflow.py (Playwright + Chromium required)
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
 for mobile in (False,True):
  c=b.new_context(viewport={'width':390 if mobile else 1200,'height':900},is_mobile=mobile,has_touch=mobile)
  page=c.new_page();page.goto(os.environ.get('WORKFLOW_URL', 'http://localhost:8502'))
  page.get_by_role('heading',name='남은 집기, 다음 가게로 이어주세요').wait_for()
  expect(page.locator('[data-testid="stFileUploader"]')).to_have_count(0)
  page.get_by_role('button',name='📸 카메라로 시작하기',exact=True).click()
  page.locator('[data-testid="stCameraInput"]').wait_for()
  page.get_by_role('button',name='← 시작 화면',exact=True).click()
  page.get_by_role('button',name='📷 사진 올려 시작하기',exact=True).click()
  buffer=BytesIO();Image.new('RGB',(1000,600),'#ddd').save(buffer,format='PNG')
  page.locator('input[type=file]').set_input_files({'name':'test.png','mimeType':'image/png','buffer':buffer.getvalue()})
  page.get_by_role('button',name='AI 분석하기 →',exact=True).click()
  page.get_by_text('집기 목록 수정',exact=True).wait_for()
  expect(page).to_have_url(re.compile(r'/edit$'))
  expect(page.locator('[data-testid="stFileUploader"]')).to_have_count(0)
  assert page.get_by_role('heading',name='물품별 가격 · 시세',exact=True).count()==0
  print('camera/upload/analyze->edit PASS',mobile)
  if not mobile:
   editor=page.locator('[data-testid="stDataFrame"]')
   editor.scroll_into_view_if_needed()
   canvas=editor.locator('.dvn-scroller').first
   canvas.dblclick(position={'x':200,'y':52})
   field=page.locator('textarea.gdg-input');field.fill('Acme');field.press('Enter')
   page.wait_for_timeout(800)
   canvas.dblclick(position={'x':80,'y':52})
   field=page.locator('textarea.gdg-input');field.fill('Office chair');field.press('Enter')
   page.wait_for_timeout(800)
  page.get_by_role('button',name='수정 완료 · 최종 견적 보기 →',exact=True).click()
  page.get_by_role('heading',name='물품별 가격 · 시세',exact=True).wait_for()
  expect(page).to_have_url(re.compile(r'/estimate$'))
  page.get_by_role('heading',name='— 원 ~ — 원',exact=True).wait_for()
  assert page.get_by_text('집기 목록 수정',exact=True).count()==0
  with page.expect_download() as download:
   page.get_by_role('button',name='최종 목록 CSV 내려받기',exact=True).click()
  text=download.value.path().read_text(encoding='utf-8-sig')
  row = list(csv.DictReader(io.StringIO(text)))[0]
  expected = ('의자', '미확인') if mobile else ('Office chair', 'Acme')
  assert (row['집기명'], row['제조사']) == expected, text
  assert row['개수'] == '1' and row['개당 대표 가격 (원)'] == '', text
  page.get_by_role('button',name='← 분석 내용 다시 수정',exact=True).click()
  page.get_by_text('집기 목록 수정',exact=True).wait_for()
  page.get_by_role('button',name='← 사진 입력으로',exact=True).click()
  page.get_by_role('button',name='분석 결과 이어보기',exact=True).click()
  page.get_by_text('집기 목록 수정',exact=True).wait_for()
  page.get_by_role('button',name='수정 완료 · 최종 견적 보기 →',exact=True).click()
  with page.expect_download() as download:
   page.get_by_role('button',name='최종 목록 CSV 내려받기',exact=True).click()
  row = list(csv.DictReader(io.StringIO(download.value.path().read_text(encoding='utf-8-sig'))))[0]
  assert (row['집기명'], row['제조사']) == expected, row
  assert page.locator('[data-testid="stException"]').count()==0
  print('final blank prices/download/back/photo retained/reuse PASS',mobile)
  c.close()
 b.close()

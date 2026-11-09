from playwright.sync_api import sync_playwright
# Run: streamlit run tests/preview_editor.py --server.port 8501
# Then: python tests/browser_drag.py (requires Playwright and Chromium)
import os
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
 for mobile in (False,True):
  c=b.new_context(viewport={'width':390 if mobile else 1100,'height':900},is_mobile=mobile,has_touch=mobile)
  page=c.new_page(); page.goto('http://localhost:8501'); page.get_by_text('새 물건 영역 그리기',exact=True).click()
  f=page.frame_locator('iframe[title="box_selector.box_selector"]'); surface=f.locator('#surface'); surface.wait_for()
  surface.scroll_into_view_if_needed(); page.wait_for_timeout(700)
  r=surface.bounding_box(); x=r['x'];y=r['y'];w=r['width'];h=r['height']
  if mobile:
   session=c.new_cdp_session(page)
   for typ,xx,yy in [('touchStart',.8,.8),('touchMove',.2,.2),('touchEnd',.2,.2)]:
    session.send('Input.dispatchTouchEvent',{'type':typ,'touchPoints':[] if typ=='touchEnd' else [{'x':x+w*xx,'y':y+h*yy}]})
  else:
   page.mouse.move(x+w*.2,y+h*.2);page.mouse.down();page.mouse.move(x+w*.8,y+h*.8,steps=8);page.mouse.up()
  coords=f.locator('#box').evaluate("el => [el.style.left, el.style.top, el.style.width, el.style.height].map(parseFloat)")
  assert all(abs(actual-expected)<1 for actual,expected in zip(coords,[20,20,60,60])), coords
  page.get_by_role('tab',name='➕ 감지 못한 물건 직접 추가 (자연어 입력)').click()
  page.get_by_text('영역이 선택되었습니다.',exact=False).wait_for()
  page.get_by_label('추가할 물건을 자연어로 적어주세요').fill('제빙기 추가해줘')
  page.get_by_role('button',name='➕ 이 위치에 추가',exact=True).click()
  page.get_by_text('먼저 사진 위에서 추가할 물건의 영역을 그려 주세요.',exact=True).wait_for()
  page.get_by_role('tab',name='✏️ 잘못 감지된 물건 수정 / 삭제').click()
  page.get_by_text('총 2개 박스',exact=False).wait_for()
  assert not page.locator('[data-testid="stException"]').count()
  print(('mobile touch' if mobile else 'desktop mouse')+': drag, add, reset, count PASS')
  # The added object overlaps the original: selecting mode must still choose the smaller original box.
  page.get_by_text('기존 박스 선택',exact=True).click()
  surface.scroll_into_view_if_needed(); page.wait_for_timeout(300)
  r=surface.bounding_box()
  page.mouse.click(r['x']+r['width']*.25,r['y']+r['height']*.3)
  page.wait_for_timeout(700)
  page.get_by_label('올바른 품목명을 자연어로 입력하세요').fill('스툴')
  page.get_by_role('button',name='이름 수정',exact=True).click()
  page.wait_for_timeout(700)
  page.get_by_text('스툴 × 1',exact=True).wait_for()
  page.get_by_role('button',name='박스 삭제',exact=True).click()
  page.get_by_text('총 1개 박스',exact=False).wait_for()
  print('existing box select, rename, delete PASS')
  c.close()
 b.close()

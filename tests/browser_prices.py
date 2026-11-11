"""streamlit run tests/preview_prices.py --server.port 8504 후 실행."""
import csv
import io
import os
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                                headless=True, args=['--no-sandbox'])
    for mobile in (False, True):
        context = browser.new_context(viewport={'width': 390 if mobile else 1200, 'height': 1000},
                                      is_mobile=mobile, has_touch=mobile)
        page = context.new_page()
        page.goto(os.environ.get('PRICES_URL', 'http://localhost:8504'))
        page.get_by_role('button', name='이전 분석 이어서 수정하기', exact=True).click()
        page.get_by_text('집기 목록 수정', exact=True).wait_for()
        if not mobile:
            editor = page.locator('[data-testid="stDataFrame"]')
            editor.scroll_into_view_if_needed()
            editor.locator('.dvn-scroller').first.dblclick(position={'x': 350, 'y': 52})
            field = page.locator('textarea.gdg-input')
            field.fill('A'); field.press('Enter')
            page.wait_for_timeout(500)
        page.get_by_role('button', name='수정 완료 · 최종 견적 보기 →', exact=True).click()
        page.get_by_role('heading', name='4,300,000원 ~ 4,900,000원', exact=True).wait_for()
        expect(page.get_by_text('부분 견적입니다.', exact=False)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role('button', name='최종 목록 CSV 내려받기', exact=True).click()
        rows = list(csv.DictReader(io.StringIO(download.value.path().read_text(encoding='utf-8-sig'))))
        assert len(rows) == 3, rows
        a, b, chair = rows
        assert a['제조사'] == '호시자키' and a['개수'] == '2' and float(a['개당 대표 가격 (원)']) == 1200000, a
        assert b['제조사'] == '스코츠맨' and float(b['개당 대표 가격 (원)']) == 2200000, b
        assert chair['개당 대표 가격 (원)'] == '' and chair['표본 수'] == '0', chair
        assert a['모델명'] == ('' if mobile else 'A'), a
        assert a['표본 수'] == b['표본 수'] == '3'
        assert '게시 가격' in a['근거'] and a['최근 수집일']
        page.get_by_text('가격 근거 보기 (출처·원본 매물·수집일)', exact=True).click()
        assert page.locator('[data-testid="stException"]').count() == 0
        page.get_by_role('button', name='← 분석 내용 다시 수정', exact=True).click()
        page.get_by_role('button', name='수정 완료 · 최종 견적 보기 →', exact=True).click()
        page.get_by_role('heading', name='4,300,000원 ~ 4,900,000원', exact=True).wait_for()
        print('positive DB: brands/model edit/quantity/partial total/evidence/CSV/persistence PASS', mobile)
        context.close()
    browser.close()

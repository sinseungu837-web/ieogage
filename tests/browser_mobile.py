"""Run preview_mobile.py on 8503 first. Chromium emulation is not physical iOS testing."""
import csv
import io
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    for width in (320, 390, 1280):
        page = browser.new_page(viewport={'width': width, 'height': 844}, is_mobile=width < 640, has_touch=width < 640)
        page.goto('http://localhost:8503')
        page.get_by_role('button', name='이전 분석 이어서 수정하기', exact=True).click()
        page.get_by_role('button', name='수정 완료 · 처분 계획 →', exact=True).click()
        expect(page.get_by_role('heading', name='처분할 물품과 방식을 골라 주세요')).to_be_visible()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        canvas = page.locator('[data-testid="stDataFrame"] .dvn-scroller').first
        # Same-row checkbox and select cell: second item off, first item personal sale.
        checkbox_x = (60 + max(0, canvas.bounding_box()['width'] - 296) / 4) / 2
        canvas.click(position={'x': checkbox_x, 'y': 100})
        page.wait_for_timeout(350)
        canvas.dblclick(position={'x': canvas.bounding_box()['width'] - 60, 'y': 55})
        page.get_by_text('개인 중고거래', exact=True).last.click()
        page.wait_for_timeout(350)
        page.screenshot(path=f'/workspace/work/plan-{width}.png', full_page=True)
        page.get_by_role('button', name='최종 견적 보기 →', exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('button', name='최종 목록 CSV 내려받기', exact=True).click()
        rows = list(csv.DictReader(io.StringIO(download.value.path().read_text(encoding='utf-8-sig'))))
        assert len(rows) == 1 and rows[0]['집기명'] == '의자', rows
        assert rows[0]['처분 방식'] == '개인 중고거래', rows
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.get_by_role('button', name='판매 준비 →', exact=True).click()
        page.get_by_text('의자 · 이케아 · 1개 판매글', exact=True).click()
        page.get_by_role('button', name='판매글 생성', exact=True).click()
        title = page.get_by_role('textbox', name='판매글 제목', exact=True)
        expect(title).to_have_value('의자 1개 판매')
        title.fill('직접 수정한 제목')
        title.press('Enter')
        page.wait_for_timeout(350)
        notes = page.get_by_role('textbox', name='외관 상태·특이사항 (선택)', exact=True)
        notes.fill('실패 테스트'); notes.press('Tab')
        page.wait_for_timeout(350)
        page.get_by_role('button', name='판매글 재생성', exact=True).click()
        expect(page.get_by_text('기존 글은 보존됩니다.', exact=False)).to_be_visible()
        expect(title).to_have_value('직접 수정한 제목')
        assert 'DO_NOT_EXPOSE' not in page.locator('body').inner_text()
        page.get_by_role('button', name='처분 계획 수정', exact=True).click()
        page.get_by_role('button', name='전체 해제', exact=True).click()
        page.get_by_role('button', name='최종 견적 보기 →', exact=True).click()
        expect(page.get_by_role('heading', name='0원', exact=True)).to_be_visible()
        page.get_by_role('button', name='← 처분 계획', exact=True).click()
        page.get_by_role('button', name='전체 선택', exact=True).click()
        page.get_by_role('button', name='최종 견적 보기 →', exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('button', name='최종 목록 CSV 내려받기', exact=True).click()
        rows = list(csv.DictReader(io.StringIO(download.value.path().read_text(encoding='utf-8-sig'))))
        assert len(rows) == 2 and rows[0]['처분 방식'] == '개인 중고거래', rows
        page.get_by_role('button', name='판매 준비 →', exact=True).click()
        page.get_by_text('의자 · 이케아 · 1개 판매글', exact=True).click()
        expect(page.get_by_role('textbox', name='판매글 제목', exact=True)).to_have_value('직접 수정한 제목')
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert page.locator('[data-testid="stException"]').count() == 0
        print('PASS same-row edits, selected CSV, bulk selection, draft failure/roundtrip, overflow:', width, flush=True)
        page.close()
    browser.close()

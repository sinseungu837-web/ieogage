"""Fieldsignal을 참고한 표시 전용 요소. 분석·가격·세션 상태를 변경하지 않는다."""
from html import escape
from pathlib import Path
import streamlit as st


def apply_theme():
    css = (Path(__file__).parent / 'styles' / 'fieldsignal.css').read_text(encoding='utf-8')
    st.html(f'<style>{css}</style>')


def masthead():
    st.html('''<header class="fs-header">
      <h1 class="fs-brand"><span class="fs-mark" aria-hidden="true">&#8635;</span>이어가게</h1>
      <span class="fs-header-note">물품의 가치를 찾고<br>새로운 시작으로.</span>
    </header>''')


def stage_navigation(step, labels):
    items = ''
    for i, label in enumerate(labels, 1):
        current = ' aria-current="step"' if i == step else ''
        items += f'<li class="fs-step"{current}><b>{i:02d}</b><span>{escape(label)}</span></li>'
    st.html(f'<nav aria-label="진행 단계"><ol class="fs-steps">{items}</ol></nav>')


def section_label(number, title):
    st.html(f'<div class="fs-kicker"><span>{number:02d} / {escape(title)}</span><span>가게를 잇는 기록</span></div>')


def service_facts():
    st.html('''<div class="fs-stats" aria-label="서비스 이용 흐름">
      <div class="fs-stat"><strong>사진 1장</strong><span>매장의 물품을 한눈에</span></div>
      <div class="fs-stat"><strong>04 단계</strong><span>사진부터 판매 준비까지</span></div>
    </div>''')


def start_chapter():
    st.html('''<div class="fs-chapter"><span class="fs-chapter-number">01</span>
      <div><strong>사진 한 장으로 시작해 보세요</strong><small>물품을 찾고, 직접 다듬어, 다음 쓰임을 준비합니다.</small></div>
    </div>''')


def footer():
    st.html('<footer class="fs-footer"><span>이어가게 · 남은 물품의 다음 가능성</span></footer>')


def estimate_label():
    st.html('<p class="fs-total-label">선택한 물품의 참고 중고 가치 합계</p>')



def native_photo_inputs():
    """업로드 input 의 속성을 브라우저에서 고친다 (Streamlit 위젯은 accept/capture 를 직접 지정할 수 없음).
    - 사진 올리기: accept=image/* → iOS 사진 선택 창 정상 동작
    - 카메라로 찍기: accept=image/* + capture=environment → 휴대폰 기본 카메라 앱(후면)으로 바로 촬영"""
    import streamlit.components.v1 as components
    components.html("""<script>
    const doc = window.parent.document;
    function fix() {
      doc.querySelectorAll('.st-key-photo_upload input[type=file]').forEach(el => {
        el.setAttribute('accept', 'image/*'); el.removeAttribute('capture'); });
      doc.querySelectorAll('.st-key-camera_upload input[type=file]').forEach(el => {
        el.setAttribute('accept', 'image/*'); el.setAttribute('capture', 'environment'); });
    }
    fix();
    new MutationObserver(fix).observe(doc.body, {childList: true, subtree: true});
    </script>""", height=0)



def step_list(labels):
    items = ''.join(f'<li><b>Step {i}</b><span>{escape(label)}</span></li>' for i, label in enumerate(labels, 1))
    st.html(f'<ol class="fs-step-list">{items}</ol>')

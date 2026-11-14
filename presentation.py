"""Fieldsignal을 참고한 표시 전용 요소. 분석·가격·세션 상태를 변경하지 않는다."""
from html import escape
from pathlib import Path
import streamlit as st


def apply_theme():
    css = (Path(__file__).parent / 'styles' / 'fieldsignal.css').read_text(encoding='utf-8')
    st.html(f'<style>{css}</style>')


def masthead():
    st.html('''<header class="fs-header">
      <h1 class="fs-brand"><span class="fs-mark" aria-hidden="true"><i></i><i></i><i></i></span>이어가게</h1>
      <span class="fs-header-note">남은 집기에서<br>다음 가게의 시작으로.</span>
    </header>''')


def stage_navigation(step, labels):
    items = ''
    for i, label in enumerate(labels, 1):
        current = ' aria-current="step"' if i == step else ''
        items += f'<li class="fs-step"{current}><b>{i:02d}</b><span>{escape(label)}</span></li>'
    st.html(f'<nav aria-label="진행 단계"><ol class="fs-steps">{items}</ol></nav>')


def section_label(number, title):
    st.html(f'<div class="fs-kicker"><span>{number:02d} / {escape(title)}</span><span>가게를 잇는 기록</span></div>')


def hero_visual():
    st.html('''<div class="fs-visual" role="img" aria-label="남은 집기가 새로운 공간으로 이어지는 모습을 표현한 컬러 타일 일러스트">
      <div class="fs-tile fs-tile-main"><span class="fs-tile-label">남은 집기</span></div>
      <div class="fs-tile fs-tile-top"><span class="fs-tile-label">다음 공간</span></div>
      <div class="fs-tile fs-tile-bottom"><span class="fs-tile-label">새로운 쓰임</span></div>
    </div>''')


def service_facts():
    st.html('''<div class="fs-stats" aria-label="서비스 이용 흐름">
      <div class="fs-stat"><strong>사진 1장</strong><span>매장의 집기를 한눈에</span></div>
      <div class="fs-stat"><strong>04 단계</strong><span>촬영부터 최종 목록까지</span></div>
    </div>''')


def start_chapter():
    st.html('''<div class="fs-chapter"><span class="fs-chapter-number">01</span>
      <div><strong>사진 한 장으로 시작해 보세요</strong><small>집기를 찾고, 직접 다듬어, 다음 쓰임을 준비합니다.</small></div>
    </div>''')


def footer():
    st.html('<footer class="fs-footer"><span>이어가게 · 남은 집기의 다음 가능성</span><span>사진에서 목록으로, 목록에서 다음 가게로.</span></footer>')


def estimate_label():
    st.html('<p class="fs-total-label">총 매각 예상 범위</p>')

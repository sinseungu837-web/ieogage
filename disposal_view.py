"""Streamlit 처분 선택 및 판매글 편집. 영구 상태는 last에, 위젯은 임시 키에 둔다."""
import pandas as pd
import streamlit as st
from disposal import METHODS, row_key, sync_states, grouped, fingerprint


def field(state, field_name, widget, label, prefix, **kwargs):
    key = prefix + ':' + field_name
    st.session_state[key] = state[field_name]
    def save():
        state[field_name] = st.session_state[key]
    return widget(label, key=key, on_change=save, **kwargs)


def selection(last, rows):
    states = sync_states(last, rows)
    st.subheader('처분 대상 · 방식 선택')
    st.caption('해제한 물품과 기존 시세는 보존됩니다. 묶음 구성이 바뀌면 공통된 처분 선택은 이어받고 판매글은 새로 작성합니다. 이전 초안은 보관됩니다.')
    left, right = st.columns(2)
    if left.button('전체 선택'):
        for state in states.values():
            state['selected'] = True
    if right.button('전체 해제'):
        for state in states.values():
            state['selected'] = False
    for row in rows:
        key = row_key(row)
        state = states[key]
        prefix = f"disposal:{last.get('key', 'current')}:{key}"
        left, right = st.columns(2)
        with left:
            field(state, 'selected', st.checkbox, f"{row['집기명']} · {row.get('제조사', '')} {row.get('모델명', '')} · {row['개수']}개", prefix)
        with right:
            field(state, 'method', st.selectbox, '처분 방식', prefix, options=METHODS, disabled=not state['selected'])
    return states


def editor(last, row, estimate, state):
    import llm
    prefix = f"sale:{last.get('key', 'current')}:{row_key(row)}"
    if not state['price_initialized']:
        if estimate['mid'] is not None:
            state['price'] = estimate['mid'] * row['개수']
        state['price_initialized'] = True
    st.caption('희망가격은 전체 수량의 합계입니다. 참고 시세와 실제 거래액은 다를 수 있습니다.')
    if estimate['mid'] is None:
        st.info('참고 시세가 없습니다. 희망가격을 직접 입력하거나 미정으로 남겨 주세요.')
    field(state, 'price', st.number_input, '희망 판매가격 (전체 수량 합계·원)', prefix, min_value=0, step=1000)
    field(state, 'condition', st.selectbox, '작동 상태', prefix, options=('작동 여부 미확인', '정상 작동', '일부 수리 필요'))
    field(state, 'notes', st.text_area, '외관 상태·특이사항 (선택)', prefix)
    field(state, 'accessories', st.text_area, '구성품·추가 정보 (선택)', prefix)
    current = fingerprint(row, state, estimate)
    if state['generated_for'] and current != state['generated_for']:
        st.warning('물품 정보 또는 판매 조건이 변경되었습니다. 기존 글을 확인하거나 재생성해 주세요.')
    if st.button('판매글 재생성' if state['generated_for'] else '판매글 생성', key=prefix + ':generate'):
        try:
            with st.spinner('판매글을 작성하고 있습니다...'):
                draft = llm.generate_sale_post(row, state)
            if state['title'] or state['body']:
                state['candidate'] = dict(draft, generated_for=current)
            else:
                state.update(draft, generated_for=current)
        except Exception:
            st.error('판매글을 생성하지 못했습니다. API 설정·사용량을 확인한 뒤 다시 시도해 주세요. 기존 글은 보존됩니다.')
    candidate = state['candidate']
    if candidate:
        st.info('새 초안입니다. 적용할 때만 현재 편집 내용을 바꿉니다.')
        st.text(candidate['title'])
        st.text(candidate['body'])
        if candidate['generated_for'] != current:
            st.warning('이 초안 생성 후 입력이 바뀌었습니다. 다시 생성해 주세요.')
        if st.button('새 초안 적용', key=prefix + ':apply', disabled=candidate['generated_for'] != current):
            state.update(candidate)
            state['candidate'] = None
            st.rerun()
        if st.button('새 초안 버리기', key=prefix + ':discard'):
            state['candidate'] = None
            st.rerun()
    field(state, 'title', st.text_input, '판매글 제목', prefix)
    field(state, 'body', st.text_area, '판매글 본문', prefix, height=240)
    if state['title'] or state['body']:
        st.caption('아래 각 상자 오른쪽 위의 복사 버튼으로 제목과 본문을 복사할 수 있습니다. 게시 전 내용을 확인해 주세요.')
        st.code(state['title'], language=None)
        st.code(state['body'], language=None)
        st.download_button('판매글 TXT 내려받기', state['title'] + '\n\n' + state['body'],
                           file_name='이어가게_판매글.txt', key=prefix + ':download')


def results(last, rows, estimates, states):
    st.subheader('처분 방식별 판매 준비')
    groups = grouped(rows, states)
    lookup = {row_key(r): e for r, e in zip(rows, estimates)}
    if groups['미선택']:
        st.info(f"처분 방식 미선택: {len(groups['미선택'])}개 품목 그룹")
    for tab, method in zip(st.tabs(METHODS[1:]), METHODS[1:]):
        with tab:
            items = groups[method]
            if not items:
                st.caption('선택한 물품이 없습니다.')
                continue
            if method == '전문업체 매각':
                st.info('참고 시세는 전문업체의 실제 매입 견적과 다를 수 있습니다. 아래 목록을 견적 문의에 활용하세요.')
            elif method == '양도·나눔':
                st.info('무료 나눔의 현금 회수액은 0원입니다. 아래 금액은 물품의 참고 가치입니다.')
            table = pd.DataFrame([{**{k: r.get(k, '') for k in ('집기명', '제조사', '모델명', '개수', '연식(년)')},
                '개당 참고 시세 (원)': lookup[row_key(r)]['mid']} for r in items])
            st.dataframe(table, hide_index=True, use_container_width=True)
            st.download_button('물품 목록 CSV 내려받기', table.to_csv(index=False).encode('utf-8-sig'),
                file_name=f'이어가게_{method}.csv', key='export:' + method)
            if method == '개인 중고거래':
                for row in items:
                    with st.expander(f"{row['집기명']} · {row.get('제조사', '')} · {row['개수']}개 판매글"):
                        editor(last, row, lookup[row_key(row)], states[row_key(row)])
    if last.get('disposal_archive'):
        with st.expander('구성이 변경되거나 삭제된 묶음의 이전 초안'):
            for key, state in last['disposal_archive'].items():
                st.text(f'이전 객체 ID: {key}')
                st.code(state['title'] + '\n\n' + state['body'], language=None)

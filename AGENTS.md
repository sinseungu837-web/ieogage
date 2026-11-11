# AGENTS.md — AI 코딩 도구용 안내 (Claude Code, Copilot, Cursor 등)

이 저장소는 11팀 해커톤 서비스 **이어가게**다. 매장 사진 → 비전 모델(RF-DETR nano)이 물체 탐지 → 코드 필터 → LLM(Gemini)이 판매 가능한 집기만 판별 → 품목표·참고 시세·판매처 링크.

## 반드시 지킬 것
1. **API 키를 코드·커밋·로그에 넣지 않는다.** 키는 로컬 `.env`(gitignore 처리) 또는 Streamlit Cloud Secrets 에만. 키가 커밋에 들어가면 즉시 키 폐기·재발급.
2. **main 에 직접 push 해도 된다** (팀 결정, 2026-11-09). 단 main 은 Streamlit Cloud 배포 브랜치라 push 즉시 ieogage.streamlit.app 에 반영되므로:
   - push 전에 `git pull` 로 최신 main 을 받는다 (다른 사람 변경 덮어쓰기 방지)
   - push 전에 최소 `python -m py_compile app.py vision.py llm.py` 와 로컬 실행으로 화면이 뜨는지 확인한다
   - 큰 변경은 기존처럼 브랜치 → PR → 본인 병합도 가능
   - push 후 링크에서 동작을 확인하고, 깨지면 바로 `git revert` 로 되돌린다
   - 강제 push(`--force`)·다른 사람 브랜치 삭제 금지, 충돌 시 다른 사람의 변경을 지우지 말 것
3. **역할 분담 유지:** 숫자(개수·가격)는 코드가 계산하고, LLM 은 판별과 문장만. LLM 이 가격을 만들게 하지 않는다.
4. **탐지 모델 → LLM 순서 유지:** 비전 모델이 이미지를 인식하고, LLM 은 그 결과(라벨 목록)를 받는다. 헷갈리는 장비·고정 설비 의심 품목만 잘라낸 이미지로 재확인한다(`llm.verify_ambiguous`, 최대 8개, 1회 호출).
5. 모델 파일·테스트 사진·`.venv` 는 올리지 않는다 (`.gitignore`).
6. 프롬프트를 바꾸면 `prompts/item_filter_v2.md` 처럼 새 버전 파일을 만들고, 같은 테스트 사진으로 이전 버전과 결과를 비교해 `docs/개발과정_모델설정.md` 에 기록한다.

## 구조
| 파일 | 역할 |
|---|---|
| `app.py` | Streamlit 화면, 사용 횟수 제한(세션 10회·하루 200회) |
| `vision.py` | RF-DETR nano 탐지(COCO 80종), 코드 1차 필터 |
| `llm.py` | Gemini 2차 판별, 출력 검증(근거 박스 없는 품목 제거, 개수는 박스 수) |
| `prompts/item_filter_v1.md` | LLM 사전 프롬프트: 규칙·품목 사전·출력 형식·예시 |
| `data/price_table.csv`, `data/channels.csv` | 시세·판매처 데이터 (작성 예정, 열 이름 고정) |
| `docs/개발과정_모델설정.md` | 발표용 개발 기록. 설정을 바꾸면 이유·측정값과 함께 추가 |
| `test_pipeline.py` | 파이프라인 점검 스크립트 |

## 실행
```bash
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # 각자 발급한 Gemini 키 입력 (팀원별 키 권장)
streamlit run app.py
```
- 첫 실행 때 RF-DETR nano 가중치를 자동으로 내려받는다. (Florence-2 는 2026-11-11 제거)
- CPU 전용. 사진 1장 분석 약 5~10초. 메모리 최대 약 1.8GB.
- Gemini 모델: `gemini-3.1-flash-lite` (속도 비교 결과는 docs 참조)

## 작업 전 확인
- 바꾸려는 동작이 `docs/개발과정_모델설정.md` 에 이유와 함께 기록된 설정인지 먼저 읽는다.
- 변경 후 `python test_pipeline.py [사진]` 으로 확인하고, 결과를 PR 설명에 적는다.

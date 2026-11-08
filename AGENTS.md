# AGENTS.md — AI 코딩 도구용 안내 (Claude Code, Copilot, Cursor 등)

이 저장소는 11팀 해커톤 서비스 **이어가게**다. 매장 사진 → 비전 모델(Florence-2-base)이 물체 탐지 → 코드 필터 → LLM(Gemini)이 판매 가능한 집기만 판별 → 품목표·참고 시세·판매처 링크.

## 반드시 지킬 것
1. **API 키를 코드·커밋·로그에 넣지 않는다.** 키는 로컬 `.env`(gitignore 처리) 또는 Streamlit Cloud Secrets 에만. 키가 커밋에 들어가면 즉시 키 폐기·재발급.
2. **main 에 직접 push 하지 않는다.** 기능별 브랜치 → PR → 팀원 1명 확인 후 병합. main 은 Streamlit Cloud 배포 브랜치다(병합 즉시 재배포).
3. **역할 분담 유지:** 숫자(개수·가격)는 코드가 계산하고, LLM 은 판별과 문장만. LLM 이 가격을 만들게 하지 않는다.
4. **Florence → LLM 순서 유지:** 비전 모델이 이미지를 인식하고, LLM 은 그 결과(라벨 목록)만 받는다.
5. 모델 파일·테스트 사진·`.venv` 는 올리지 않는다 (`.gitignore`).
6. 프롬프트를 바꾸면 `prompts/item_filter_v2.md` 처럼 새 버전 파일을 만들고, 같은 테스트 사진으로 이전 버전과 결과를 비교해 `docs/개발과정_모델설정.md` 에 기록한다.

## 구조
| 파일 | 역할 |
|---|---|
| `app.py` | Streamlit 화면, 사용 횟수 제한(세션 10회·하루 200회) |
| `vision.py` | Florence-2 탐지(OD + 품목 사전 grounding + 캡션 검증), 코드 1차 필터 |
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
- 첫 실행 때 Florence-2-base(약 0.5GB)를 Hugging Face 에서 자동으로 내려받는다(로그인 불필요).
- CPU 전용. 사진 1장 분석 약 5~10초. 메모리 최대 약 1.8GB.
- Gemini 모델: `gemini-3.1-flash-lite` (속도 비교 결과는 docs 참조)

## 작업 전 확인
- 바꾸려는 동작이 `docs/개발과정_모델설정.md` 에 이유와 함께 기록된 설정인지 먼저 읽는다.
- 변경 후 `python test_pipeline.py [사진]` 으로 확인하고, 결과를 PR 설명에 적는다.

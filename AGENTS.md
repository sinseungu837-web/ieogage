# AGENTS.md — AI 코딩 도구용 안내

11팀 해커톤 서비스 **이어가게**: 카페 매장 사진 → Gemini 물품 인식 → 사용자 검수 → 시세표·법정 정률법 가격 → Kakao Local 판매처.

## 반드시 지킬 것
1. **API 키를 코드·커밋·로그에 넣지 않는다.** 로컬 `.env`(gitignore) 또는 Streamlit Cloud Secrets 에만. 노출되면 즉시 폐기·재발급.
2. main 은 Streamlit Cloud 배포 브랜치다. 브랜치 → PR → CI(단위 테스트) 통과 후 병합한다. 강제 push·다른 사람 브랜치 삭제 금지.
3. **AI 는 '무엇인지'만 판단한다.** 개수·가격은 코드가 계산하며 LLM 이 가격을 만들게 하지 않는다.
4. 가격 규칙(`reference.py`, `data/asset_classes.csv`)을 바꾸면 근거 법령·수치를 `docs/개발과정_모델설정.md` 에 기록한다.
5. 프롬프트를 바꾸면 `prompts/item_filter_vN.md` 새 버전으로 만들고 이전 버전과 결과를 비교해 기록한다.
6. 사진·모델 파일·`.venv`·수집 데이터는 올리지 않는다.

## 구조와 실행
README.md 참고. 테스트: `python -m unittest discover -s tests -p "test_*.py"`

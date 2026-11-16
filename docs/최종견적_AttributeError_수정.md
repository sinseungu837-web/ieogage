# 최종 견적 AttributeError 대응 (2026-11-12, 한국 시간)

## 확인한 사실과 미확인 원인

작업 기준은 main `c264b3436043e9036546294a9fe65f9eeed9e8c5`이다. 최신 main을 pull한 뒤 코드를 분석했다.

로컬 Python은 저장소의 `price.py`, `listings.py`를 import한다. `price.estimate`, `price.db_revision`, `price.empty_estimate`는 모두 callable이다. `empty_estimate`는 조건문 내부가 아닌 모듈 최상위에 정의되어 있다. `app.py`가 `price`를 다른 값으로 대입하는 곳은 없다. `price.evidence` 내부의 지역 변수 `price`는 모듈 참조를 바꾸지 않는다.

호출 관계는 `app.final_page → cached_estimate → price.estimate → listings.read/production`이고, 필요한 경우 별도로 온비드를 조회한다. `price`에서 `listings`를 함수 내부에서 import하지만 `listings`는 `price`나 `app`을 import하지 않아 이 경로에 순환 import는 없다. 빈 DB와 키 없음은 로컬에서 정상적인 가격 근거 부족 결과를 반환한다.

**확인·재현한 코드 결함:** 기존 `app.final_page`는 조회 실패를 잡은 `except`에서 다시 `price.empty_estimate`를 호출한다. 실행 중인 가격 모듈에 함수가 없으면 이 예외 처리 자체에서 `AttributeError`가 발생하고 페이지 전체가 중단된다. `db_revision`과 `empty_estimate`가 없는 가상 모듈을 주입해 기존 함수에서 같은 종류의 오류를 재현했다. 이는 실제 배포 서버에 왜 함수가 없는지까지 증명한 것은 아니다.

**배포 환경에서만 확인 가능한 가설:** 이전 Python 모듈이 프로세스에 남아 있는 상황, 배포 파일/리비전 불일치, 다른 배포 저장소·브랜치·진입 파일 설정, 동명 `price` 모듈 import 등. 관리자 로그와 실제 `price.__file__`를 확인하지 않았으므로 어느 가설도 원인으로 확정하지 않는다. Streamlit 데이터 캐시는 함수의 반환값을 저장하는 것이므로, 그 자체가 모듈의 함수 속성을 없애는 원인이라고 단정할 수 없다.

## 변경

- `app.py`: 최종 화면에서 `price.empty_estimate` 직접 호출 제거. 각 품목을 독립적인 `safe_estimate`로 처리한다. 기존 캐시 함수와 가격 DB·온비드 계산 흐름은 유지한다.
- `estimate_view.py` 신규: 가격 모듈·CSV·Streamlit을 import하지 않는 표시 경계. 실패 기본 결과를 직접 만들므로 조회 모듈의 함수 누락에 영향을 받지 않는다. `db_revision`이 없으면 캐시 revision을 None으로 두고 조회를 계속하며, revision 조회 자체가 실패해도 가격 조회를 시도한다.
- 결과가 None/잘못된 형식이거나 일부 가격만 존재, NaN/역전된 범위/표본 부족이면 해당 품목을 가격 근거 부족으로 처리한다. 이전 결과 형식에 표시용 메타데이터가 빠져 있으면 기본값을 채워 화면의 KeyError를 방지한다. 정상 품목의 가격·수량 합계와 부분 견적 표시는 계속 유지한다.
- `price.py`: 품목 열을 `data['item']`으로 명시적으로 접근한다. 이는 열 이름과 속성 접근의 혼동을 피하는 보완이며 보고된 AttributeError의 원인으로 확인된 사항은 아니다.
- `tests/test_final_estimates.py` 신규: 함수 누락, revision 오류, 잘못된 결과, 빈 DB·키 없음, 일부 품목 실패, 화면 렌더링과 정상 가격 보존을 검증한다. 테스트 간 Streamlit 데이터 캐시를 지워 결과가 섞이지 않도록 한다.
- `listings.py`: 읽기·정제·빈 파일 경로를 점검했으며 수정하지 않았다. 사진 업로드, `vision.py`, `llm.py`, 박스·재고 편집, 프롬프트와 배포 설정도 변경하지 않았다.

실패 로그에는 `Price lookup unavailable`, 단계, 예외 클래스 이름, 실제 모듈 파일 경로, `estimate/db_revision/empty_estimate`의 callable 여부만 남긴다. API 키가 포함될 수 있는 예외 메시지·요청 URL·traceback은 기록하지 않는다.

## 로컬 검증

- 기존 25건 + 신규 8건, **총 33건 테스트 통과**.
- Streamlit AppTest에서 가격 모듈에 함수가 전혀 없어도 최종 견적 페이지가 중단되지 않음을 확인.
- 한 품목이 AttributeError로 실패해도 다른 품목의 가격·합계와 부분 견적 안내를 확인.
- 실제 빈 운영 CSV + 온비드 키 없음에서 공란 견적 및 화면 정상 렌더링 확인.
- PC/모바일 Playwright: 사진 입력/가상 AI 분석/품목·제조사 수정/페이지 왕복/CSV 다운로드 테스트 통과.
- PC/모바일 합성 가격 DB: 모델 수정(PC), 제조사별 독립 가격, 수량 계산, 부분 견적, 근거·CSV·왕복 시 결과 유지 테스트 통과.
- `python -m py_compile app.py vision.py llm.py price.py listings.py estimate_view.py`, `git diff --check` 통과.
- `python test_pipeline.py`는 Gemini 키가 없어 중단. 실제 AI 호출·실제 온비드 API·배포 환경의 전체 경로는 검증하지 못했다.

실행 예시:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
streamlit run tests/preview_workflow.py --server.port 8512
WORKFLOW_URL=http://localhost:8512 python tests/browser_workflow.py
streamlit run tests/preview_prices.py --server.port 8514
PRICES_URL=http://localhost:8514 python tests/browser_prices.py
```

운영 CSV에 합성 데이터를 추가하지 않았고, 키나 새 유료 API 설정도 추가하지 않았다.

## 관리자에게 필요한 확인 및 조치

1. Community Cloud 관리 화면에서 연결 저장소 `sinseungu837-web/ieogage`, 브랜치 `main`, 진입 파일 `app.py` 및 최신 변경 반영 여부를 확인한다.
2. 기존 오류 로그에서 `price.empty_estimate` 오류 직전의 최초 예외와 전체 호출 경로를 확인한다. 새 코드의 오류 로그가 있으면 `module=` 경로와 `functions=` 값으로 실제 import된 파일 및 함수 존재 여부를 확인한다. 로그나 키를 공개 게시하지 않는다.
3. 최신 코드 반영 후에도 불일치가 의심되면 관리 화면에서 앱을 Reboot하여 Python 프로세스를 다시 시작한다. 이는 미확인 가설을 검증하기 위한 조치이며 확정된 해결책은 아니다. 데이터 캐시 삭제나 사용자 브라우저 새로고침만으로 Python 모듈이 새로 로드된다고 보장할 수 없다. 재시작 시 기존 사용자 분석 세션이 초기화될 수 있다.
4. 기존 `GEMINI_API_KEY`, 선택적 `DATA_GO_KR_KEY`의 설정 존재 여부를 Secrets에서 확인한다. 가격 키가 없어도 최종 견적 페이지는 가격 근거 부족으로 열려야 한다.
5. 재시작·배포 뒤 실제 사진 분석 → 수정 → 최종 견적 경로를 확인한다. 이번 작업은 관리자 로그를 읽거나 관리 작업을 수행하지 않았으며 **배포 성공 또는 실제 운영 오류의 최종 원인을 확정하지 않는다**.

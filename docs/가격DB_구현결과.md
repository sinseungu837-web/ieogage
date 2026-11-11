# 중고 집기 가격 DB 구현 결과 (2026-11-12, 한국 시간)

## 결과와 검증 범위

CSV 매물 → 정제 → 사용자 입력 매칭 → Q1/중앙값/Q3 → 수량별 합계 → Streamlit 표시를 구현했다. 기존 온비드는 독립적인 보조 가격으로 유지했다.

**실제 중고 매물 수집 성공 건수: 0건.** 허용이 확인된 공개 중고 매물 출처를 이번 조사에서 확보하지 못했다. 운영 `data/listings.csv`와 통계 파일은 헤더만 있다. 가짜 가격이나 과거 개발 문서의 측정값을 실제 수집 데이터로 넣지 않았다. 가격 통계·브라우저 검증은 임시 파일의 합성 데이터로 실행했다. 운영 화면에서는 합성 데이터를 사용하지 않는다.

**실제 AI/API까지 포함한 전체 파이프라인 검증은 완료하지 못했다.** 환경에 `GEMINI_API_KEY`, `DATA_GO_KR_KEY`가 없다. 실제 `python test_pipeline.py`는 Gemini 키 없음으로 중단됐다. RF-DETR 가중치 실행 및 실제 사진의 탐지 정확도도 재검증하지 않았다. 탐지·Gemini·프롬프트 코드는 변경하지 않았다.

## 기존 저장소 분석 및 복구

- 기준 main: `7278cb2b292f6b5ff90c69ea3fdb7dbd26384103` (PR #17 병합). 시작 화면 로컬 브라우저 렌더링, 기존 재고 테스트 2건 및 문법 검사를 확인했다. 이 확인은 실제 Gemini 호출 성공을 의미하지 않는다.
- 작업은 main에서 진행했다. 기존 작업 내용은 없었고 기준 커밋이 이미 Git에 보존되어 있으므로 동일 내용의 백업 커밋은 만들지 않았다. 추가로 작업 환경에 `ieogage-before.bundle`을 만들었다.
- `app.py`: 시작/사진 입력/분석 수정/최종 견적 4페이지. 업로드·카메라·드래그·박스·횟수 제한 구조 유지.
- `vision.py`, `llm.py`: 최신 버전은 Gemini 단독 탐지 기본, `DETECTOR=rfdetr` 대체 모드. 기존 AGENTS 안내의 RF-DETR 기본 설명보다 실제 코드가 우선이다. 두 파일과 프롬프트는 수정하지 않았다.
- `inventory.py`: 수정된 활성 박스로 품목·제조사별 수량 계산. 모델·사양·상태가 다르면 추가 분리하도록 확장.
- `price.py`: 기존 온비드 `estimate`를 `onbid_estimate`로 보존하고 새 `estimate`에서 CSV 우선 조회. 기존 `estimate(name)` 호출도 호환.
- `data/price_table.csv`, `data/channels.csv`: 기존에는 헤더만 존재. channels 파일은 변경하지 않았다. 통계 파일의 기존 열 이름을 유지하고 사양/가격성격 열만 추가했다.

복구 시 변경 커밋에 `git revert <변경 SHA>`를 실행하고 main에 push한다. 과거 커밋으로 강제 push하지 않는다.

## 출처 조사 근거

조사는 공개 robots/약관/홈페이지 요청으로 한정했다. 인증·CAPTCHA·차단을 우회하지 않았다. robots 허용만으로 약관상 데이터 수집 허가가 있다고 판단하지 않았다.

| 출처 | 실제 확인 | 결정 |
|---|---|---|
| [당근 robots](https://www.daangn.com/robots.txt), [약관](https://www.daangn.com/policy/terms/) | 둘 다 HTTP 403. 이번 환경에서 내용 확인 불가 | 허용을 확인할 수 없어 제외 |
| [번개장터 웹 robots](https://m.bunjang.co.kr/robots.txt) | 일반 경로 Allow지만 GPTBot 등 일부 봇은 Disallow. `/terms`는 JS 앱 셸만 반환해 약관 확인 불가 | 미등록 |
| [번개장터 API robots](https://api.bunjang.co.kr/robots.txt) | 검색엔진 일부만 Allow, `User-agent: * / Disallow: /` | 내부 API 수집 제외 |
| [중고나라 robots](https://web.joongna.com/robots.txt) | 일반 경로 Allow. 홈페이지 HTTP 403, 추정 약관 경로 `/policy/terms`는 404 | 약관·본문 접근 확인 불가로 제외 |
| [GovDeals robots](https://www.govdeals.com/robots.txt) | 일부 경로 차단, Crawl-delay 5 | 국내 KRW 카페 매물의 정책·데이터 적합성을 확보하지 못해 미등록 |
| [Public Surplus robots](https://www.publicsurplus.com/robots.txt) | 다수 봇별 차단 규칙 존재 | 국내 KRW 매물/약관 검증 미완료로 미등록 |
| [온비드 공공데이터 설명](https://www.data.go.kr/data/15157252/openapi.do) | 포털 페이지 요청은 HTTP 503. 기존 코드와 개발 기록에 공식 API 사용 근거 존재 | 기존 API 유지. 이번 환경은 키가 없어 실수집 미실행 |

`data/sources.json`은 빈 객체다. 현재 자동 수집이 검증된 일반 중고거래 웹 출처가 있다는 의미로 해석하면 안 된다. 온비드는 키를 보유한 사용자가 기존 공식 API 이용 권한 범위에서 별도 갱신할 수 있다.

## 변경 파일

- `app.py`: 모델명·사양·상태 편집 및 저장, 제조사별 캐시, DB 변경 시 캐시 갱신, 대표 가격·표본 수·최근 수집일·매칭 수준·출처 링크, 부분 견적 및 오류 격리.
- `inventory.py`: 모델/사양/상태별 그룹과 실제 박스 수량.
- `price.py`: CSV 조회/필터/단계별 매칭/독립 온비드 fallback. 온비드도 입력 제조사/모델/사양/상태가 있으면 제목에서 확인 가능한 기록만 사용.
- `listings.py`: 공통 스키마, 정제, 최신 스냅샷 중복 제거, 운영 데이터 필터, 통계 생성, 임시 파일 후 교체 저장.
- `crawler.py`: 별도 CLI. 승인 출처의 공개 Product JSON-LD 파싱, robots 검사, 지연 요청, 공식 온비드 수집, 승인된 CSV 가져오기. 리다이렉트를 자동 추적하지 않는다.
- `data/listings.csv`: 운영 매물 DB(현재 0건).
- `data/price_table.csv`: 통계 CSV(현재 0건, 기존 열 유지).
- `data/sources.json`: 허용 근거가 확인된 웹 출처 등록부(현재 비어 있음).
- `tests/test_prices.py`: 합성 매물의 정제·매칭·가격·실패 처리 테스트.
- `tests/browser_workflow.py`: 추가 열에 맞춘 편집 좌표 및 CSV 열 이름 기반 검사.
- `tests/preview_prices.py`, `tests/browser_prices.py`: 격리된 합성 가격 DB의 PC/모바일 화면 검사.
- `README.md`, 이 문서, `docs/개발과정_모델설정.md`: 실행 방법·근거·검증 한계 기록.

## CSV와 가격 계산

매물 열: `item,manufacturer,model,spec,title,condition,price,currency,sale_status,price_basis,quantity,source,url,collected_at,is_sample`.

- `price_basis`: `asking`(판매 중 게시 가격), `sold_posted`(판매 완료 표시는 있지만 실제 거래액 미확인), `auction`(공매 낙찰가). 각각 `sale_status=for_sale/sold/sold`와 일치해야 한다.
- KRW, 개별 1개, 1천~5천만 원, 공개 HTTPS URL, 유효 출처/수집일, 명시적 `is_sample=true/false`만 통과한다. 품목·제조사 별칭을 정규화한다. 묶음·다수 수량·부품·무관 검색 결과·잘못된 가격/일자를 제외한다.
- 같은 URL은 최신 스냅샷 하나만 사용한다. 예약/품절/가격 협의·다른 통화·실제 거래액을 알 수 없는 경매 진행가는 수집 대상이 아니다. 웹 수집기는 `UsedCondition`, `InStock`, 단일 `Offer`만 지원한다. 매물의 추가 세부 정보가 없으면 추정해서 채우지 않는다.
- 운영 조회는 `is_sample=false`, 최근 180일 데이터만 사용한다. 표본의 고가 제품을 자동으로 깎지 않는다. 별도 온비드 fallback의 기존 최근 2년·상한·IQR 규칙은 유지한다.
- 품목 필터 → 입력 제조사 필터 → 입력 상태 필터 → 동일 모델 우선 → 동일 모델이 없고 사양이 명시된 경우 같은 사양 → 품목/제조사 참고 시세. 확인된 모델·제조사와 맞지 않는 데이터로 억지로 3건을 채우지 않는다. 사양은 문자열 정규화 후 일치만 지원하며 용량 유사도 추정은 구현하지 않았다.
- 모델이 맞는 표본이 1~2건이면 다른 모델을 추가하지 않는다. 정보가 없는 경우 품목 참고 시세가 가능하지만 신뢰도는 낮음으로 표시한다.
- 서로 다른 가격 성격을 합쳐 평균내지 않는다. 충분한 `asking` → `sold_posted` → `auction` 순으로 하나의 성격을 선택한다. 각각 3건 미만이면 가장 많은 군집의 근거만 제시한다. CSV 근거가 부족하면 온비드를 별도로 조회한다.
- pandas 선형 보간 분위수 Q1/Median/Q3, 최소 3건. 품목별 하한/중앙값/상한에 사용자 박스 수량을 곱한다. 합계는 가격이 있는 품목만 포함한다. 품목마다 게시가·공매가가 다르면 참고 합계임을 안내한다.
- 상세 근거는 원본 매물 URL로 연결한다. 온비드 CLI의 링크는 관리번호·제목을 포함한 **검색 링크**이며 검증되지 않은 상세 페이지 URL을 생성하지 않는다.
- 통계 CSV는 출처·모델·사양·상태·가격성격별 별도 그룹이며 상세 조회는 원본 listings에서 계산한다. 표본 부족 시 숫자를 생성하지 않는다.

## 실행과 데이터 갱신

기존 Python/Streamlit 실행 및 Secrets 설정을 유지한다. 운영 라이브러리를 새로 추가하지 않았다.

```bash
pip install -r requirements.txt
streamlit run app.py
# DATA_GO_KR_KEY를 로컬 .env에 설정한 경우 (또는 실행 환경변수)
python crawler.py onbid
```

웹 수집은 **사이트 운영 정책상 자동 수집 허용을 확인한 후** `data/sources.json`에 등록한다. 아래는 형식 예시이며 실서비스 출처가 아니다.

```json
{
  "허용된출처이름": {
    "enabled": true,
    "automation_allowed": true,
    "origin": "https://허용된출처도메인",
    "terms_url": "https://허용된출처도메인/terms",
    "permission_evidence": "자동 수집 허용 약관 조항 또는 운영자의 허가 기록",
    "checked_at": "YYYY-MM-DD",
    "delay_seconds": 3
  }
}
```

```bash
python crawler.py crawl --source 허용된출처이름 --item 제빙기 'https://허용된출처도메인/product/1'
python crawler.py import-csv /path/authorized-listings.csv --source 허용된출처이름
```

허용 정책 확인일은 90일 이내여야 하고 robots도 실행 시 다시 확인한다. 크롤러 이름은 `IeogageResearch/1.0`이며 검색엔진으로 위장하지 않는다. 각 페이지는 최소 2초·설정 지연·robots Crawl-delay 중 큰 값만큼 기다린다. 로그인·JS 렌더링·비공개 API·자동 검색/링크 탐색은 구현하지 않았다. 공개 Product JSON-LD가 없는 사이트는 별도 허용 출처 어댑터가 필요하다.

수집 실패·유효 신규 매물 0건은 기존 DB를 덮어쓰지 않는다. 운영 앱은 CSV 조회만 하고 업로드 시 크롤링하지 않는다(온비드 fallback은 기존처럼 필요 시 API 조회). 갱신 CSV를 검토하고 main에 커밋/push하면 Community Cloud에서 동일 DB를 사용한다. Streamlit Cloud의 임시 디스크를 영구 DB로 쓰지 않는다. 외부 서버·유료 API·새 배포 서비스는 필요 없다.

Secrets는 기존 `GEMINI_API_KEY`와 선택적 `DATA_GO_KR_KEY`를 그대로 사용한다. CLI는 로컬 `.env` 또는 환경변수를 사용한다. 실제 키를 CSV·소스·로그·Git에 넣지 않는다.

## 테스트와 제한

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m py_compile app.py vision.py llm.py price.py inventory.py listings.py crawler.py
# 브라우저 검증에만 playwright와 Chromium 필요 (운영 의존성 추가 없음)
streamlit run tests/preview_workflow.py --server.port 8502
python tests/browser_workflow.py
streamlit run tests/preview_prices.py --server.port 8504
python tests/browser_prices.py
streamlit run tests/preview_editor.py --server.port 8505
EDITOR_URL=http://localhost:8505 python tests/browser_drag.py
```

단위 테스트 25건 통과. 확인 항목: 충분한 표본, 다른 제조사, 정보 없음, 3건 미만, 여러 개 수량, 여러 품목, 같은 품목의 제조사별 가격 분리, 사양/모델/상태 차이, 게시/완료/낙찰 분리, 오래된 데이터·샘플 제외, 중복·묶음·무관/오류 가격, DB 오류·API timeout·미등록 출처·robots 차단·수집 실패 시 DB 보존.

브라우저: PC/모바일 모두 사진 업로드·카메라 입력 화면·가상 AI 분석·품목/제조사 수정·왕복 이동·사진 재사용·CSV 다운로드, 모델명 수정(PC), 가격/수량 계산·부분 합계·근거 표시, 드래그·추가·수정·삭제를 검증했다. 실제 카메라 장치·실제 AI 호출·실제 웹 매물 수집 성공은 검증하지 않았다. 합성 가격 예시는 두 제조사 제빙기 2대/1대와 가격 없는 의자로 총 430만~490만원을 확인했다. 이 금액은 테스트 전용이며 실제 시세가 아니다.

배포 주소는 HTTP 요청과 쿠키 유지로 200 응답을 확인했다. 브라우저는 환경 프록시 CA 신뢰 문제로 `ERR_CERT_AUTHORITY_INVALID`가 발생해 원격 화면 상호작용 검증이 제한된다. TLS 검증을 끄지 않았다. 실제 변경 커밋의 배포 반영은 작업 종료 보고에서 별도로 구분한다.

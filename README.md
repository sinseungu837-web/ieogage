# 이어가게 (MVP)

매장 사진 → RF-DETR nano(물체 탐지) → 코드 필터 → Gemini(집기 판별) → 품목표·참고 시세·판매처 링크

## 실행 (팀 노트북)
```bash
cd hackathon-ieogage/app
.venv/Scripts/python -m streamlit run app.py --server.headless true --server.port 8501
```
## 공개 링크 (링크 가진 누구나, 휴대폰 포함)
다른 터미널에서:
```bash
cd hackathon-ieogage/app
./tools/cloudflared.exe tunnel --no-autoupdate --url http://localhost:8501
```
출력의 `https://○○○.trycloudflare.com` 주소를 공유. 노트북이 켜져 있고 두 프로그램이 실행 중일 때만 접속되며, 다시 켜면 주소가 바뀐다.

## 파일
- `app.py` 화면 / `vision.py` RF-DETR·코드 필터 / `llm.py` Gemini 판별
- `prompts/item_filter_v1.md` LLM 사전 프롬프트 (규칙·품목 사전·출력 형식·예시)
- `data/price_table.csv`, `data/channels.csv` 시세·판매처 (작성 예정)
- `docs/개발과정_모델설정.md` 발표용 개발 기록
- `.env` API 키 (업로드 금지, `.gitignore` 처리)

## 중고가격 DB

사전 수집 CSV를 조회하고, 근거가 부족하면 기존 온비드 API를 별도로 사용합니다.
모델명·사양·상태를 수정하면 가격 매칭에도 반영됩니다. 표본 3건 미만은 견적을 생성하지 않습니다.

```bash
python crawler.py onbid       # 로컬 .env의 DATA_GO_KR_KEY 필요
python -m unittest discover -s tests -p 'test_*.py'
```

현재 원본 가격 DB는 **99건**이며, 이 중 소상공간 원문을 수동 검증한 게시가는 **10건**입니다.
신규 블렌더는 판매 중 게시가 2건과 판매 완료 게시가 1건으로 구분했습니다.
카이저 IMK-3051 50kg는 3개 매물로 통계가 생성됩니다. 샘플 가격은 운영 DB에 넣지 않았습니다.
수동 입력과 진단 방법은 [실데이터 확보 결과](docs/실데이터_확보_진단.md)를 참고하세요.
허용 출처 등록, CSV 스키마, 갱신/배포 방법, 테스트 및 제약은
[가격 DB 구현 결과](docs/가격DB_구현결과.md)를 참고하세요.

추가 데이터와 검수 범위: [카페 DB 확장 보고서](docs/카페_DB_확장_20261112.md).

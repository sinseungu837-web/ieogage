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

원본 CSV는 **99건**(기존 96건 보존 + 신규 블렌더 게시가 3건)입니다.
견적에는 별도 검증 이력과 일치하는 5건만 허용합니다. 현재 동일 모델·사양·가격 성격별
표본이 3건 미만이므로 견적 요약은 비어 있습니다. 재검증 대기 원본을 지우거나 임의 가격으로 채우지 않습니다.
`python cafe_db_report.py`로 검증 표본과 부족량을 확인할 수 있습니다.
수집 범위·검증 이력·갱신 방법은 [카페 DB 확장 보고서](docs/카페_DB_확장_20261112.md)를 참고하세요.
수동 입력과 진단 방법은 [실데이터 확보 결과](docs/실데이터_확보_진단.md)를 참고하세요.
허용 출처 등록, CSV 스키마, 갱신/배포 방법, 테스트 및 제약은
[가격 DB 구현 결과](docs/가격DB_구현결과.md)를 참고하세요.

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

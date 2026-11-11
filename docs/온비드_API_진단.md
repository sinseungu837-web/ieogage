# 온비드 API 최소 요청 진단 (2026-11-12 KST)

정상 API 응답은 확보하지 못했다. 온비드 신규 낙찰 데이터는 0건이다.
이번에는 crawler 실제 수집 명령을 실행하지 않았다. 최소 요청 성공 전에는
수집 요청의 엔드포인트·조회 기간·필터를 추측으로 교체하지 않고 오류 처리만 개선했다.
최신 원격 main(e1f3c85)의 수동 검증 게시가 7건과 통계는 그대로 유지했다.
이 7건은 이번 온비드 수집 성과나 공매 낙찰가가 아니다.

## 공식 문서 확인 범위

[공식 페이지 15157252](https://www.data.go.kr/data/15157252/openapi.do)의 본문과 요청변수 표를
웹 조회로 확인했다(표시 수정일 2026-04-08). 서비스 기본 주소는
`https://apis.data.go.kr/B010003/OnbidCltrBidRsltListSrvc2`다.
필수 항목은 serviceKey/pageNo/numOfRows/resultType/cltrTypeCd/prptDivCd/opbdDtStart/opbdDtEnd다.
물건유형 0001은 부동산, 0002 자동차, 0003 동산이다. 재산유형 0004는 불용품,
0007은 압류재산이며 쉼표 구분 복수 코드도 허용한다. 날짜는 yyyyMMdd,
종료일은 현재 이전이다. 낙찰 상태 0010과 물건명은 선택 조건이다.

공식 HTML 직접 다운로드는 시간 초과/HTTPS 503으로 실패했다. 첨부 활용가이드와
전체 샘플 URL은 확보하지 못했다. 기존 operation `getCltrBidRsltList2`가 최신 가이드와
일치하는지는 독립적으로 재확인하지 못했다. 공식 샘플과 완전히 동일한 요청이라고
주장하지 않으며, 확인 가능한 필수 항목 표를 따른 최소 요청이다.

## 최소 요청 및 비교 결과

기존 operation에 GET으로 다음을 전달했다. 실제 키는 환경변수에서만 읽고,
decoding 후 urlencode를 한 번 적용했다. 키/요청 URL/오류 원문은 보고서에 저장하지 않는다.

```text
pageNo=1
numOfRows=10
resultType=json
cltrTypeCd=0003
prptDivCd=0004
opbdDtStart=20260105
opbdDtEnd=20260106
```

onbidCltrNm/pbctStatCd/dspsMthodCd 등 선택 항목은 제외했다.
HTTP 기준 요청에서 한 항목씩 바꾼 결과다. [전체 안전 기록](onbid_diagnostic_results.json).

| 비교 | 결과 |
|---|---|
| 최소 요청 | HTTP 400 / API 10; 마지막 재확인은 20초 시간 초과 |
| 날짜만 20251001~20251002 | HTTP 400 / API 10 |
| 기간만 약 2년 | HTTP 400 / API 10 |
| 재산유형만 0007 | 20초 시간 초과 |
| 물건유형만 0001 | 20초 시간 초과 |
| 재산유형만 0007,0005 | HTTP 400 / API 10 |
| 페이지 크기만 100 | HTTP 400 / API 10 |
| 낙찰 상태만 추가 | HTTP 400 / API 10 |
| 물건명만 추가 | HTTP 400 / API 10 |
| 키만 무효 대조값 | HTTP 400 / API 10 |
| 동일 최소 조건 HTTPS | HTTP 503 / upstream connection timeout; 재확인도 동일 |

공식 표의 예시 날짜 20241119~20241120/부동산/압류재산과
20251101~20251107/동산/불용품도 시간 초과였다.
타임아웃은 해당 파라미터의 승인/거부 증거가 아니다.

**400:** 게이트웨이가 파라미터 오류 코드 10을 반환한 사실까지 확인했다.
짧은 기간·단일 코드·선택 조건 제거로도 성공하지 않아 날짜 범위, 복수 코드,
물건명/낙찰 필터 중 하나를 원인으로 특정하지 못했다. 복수 코드는 문서상 허용된다.
무효 키도 같은 오류이므로 인증 성공 증거가 아니다. operation 경로/실제 명세 불일치나
서버 검증 문제 등의 가능성은 미확정이다.

**503:** Envoy의 upstream connection timeout은 상위 서버 연결 단계 실패다.
API 파라미터 검증/인증 응답과 구분한다. Cloud 정책은 unrestricted/enforced이고
GitHub 통신은 성공했다. 원격 HTTPS 리스너, 경로/방화벽, Cloud 출구 경로 중
어디인지 현재 증거로 분리할 수 없다. 키 재발급/허용 도메인 추가로 해결된다는 근거는 없다.

## 기존 코드와 수정 내용

| 항목 | 기존 main | 진단/수정 |
|---|---|---|
| 요청 규모 | 최근 730일·100건·전체 재산유형 | 별도 진단 CLI는 과거 1일·10건·단일 유형 |
| 선택 조건 | 물건명·낙찰·매각 | 최소 진단에서는 제외; 수집 요청 자체는 유지 |
| 키 인코딩 | 최신 main은 이미 unquote+urlencode | 진단도 동일; 이중 인코딩으로 확정하지 않음 |
| 오류 처리 | 일반 header JSON 위주, 예외 후 계속 | 게이트웨이 JSON/XML·HTTP·타임아웃·TLS·형식 오류 분류 |
| 수집 실패 | 일부 실패 후 저장 가능 | strict 수집은 첫 오류에서 중단, DB 저장 안 함 |
| 화면 fallback | 근거 부족 표시 | 유지, 안전한 오류 분류 로그 추가 |

- onbid_api.py: URL/키/응답 원문을 담지 않는 구조화된 예외와 응답 검증.
- diagnose_onbid.py: DB를 쓰지 않는 최소 요청 및 한 변수씩 비교하는 CLI.
- price.py: 안전한 오류 처리, 기존 diagnostics 유지, strict 옵션.
- crawler.py: 실패 종료코드 1, 기존 --report에 안전한 오류 분류 기록.
- tests/test_onbid_api.py: 오류를 빈 데이터로 오인하지 않음, 키 로그 차단,
  오류 시 DB 무쓰기, JSON/XML/503 분류, 최소 요청 구성 검증.

```bash
.venv/bin/python diagnose_onbid.py --scheme http
.venv/bin/python diagnose_onbid.py --scheme https
.venv/bin/python diagnose_onbid.py --scheme http --matrix
# 정상 최소 응답 및 명세 확인 후에만 실제 수집
python crawler.py onbid --report work/onbid-collection.json
```

## 필요한 다음 확인

1. 활용신청 상세에서 15157252 승인 상태와 서비스 연결 확인. 실제 키 유효성은 아직 미검증이다.
2. 포털 첨부 활용가이드/승인 계정 샘플에서 최신 operation 경로 확인.
3. 제공기관 디지털시스템실(공식 페이지 연락처 02-3420-5760)에 위 안전 파라미터의
   HTTP 코드 10 원인과 HTTPS 443 상태/지원 프로토콜 확인.
4. Cloud 지원에 apis.data.go.kr:443 상위 연결 시간 초과 전달. 제공기관의 정상 응답과
   비교해야 출구 경로 문제를 더 좁힐 수 있다.

기관에 연락하거나 메시지를 보내지는 않았다. 실제 정상 응답이 없으므로 수집 가능 여부는
미검증이며, 운영 가격 DB에는 실패 응답이나 임의 가격을 추가하지 않았다.

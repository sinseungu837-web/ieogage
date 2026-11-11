"""가격 DB를 변경하지 않는 온비드 최소 요청 진단. 키/URL/본문은 출력하지 않는다."""
from __future__ import annotations

import argparse
import json
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

import price
from onbid_api import OnbidError, request_body


def minimal_params(key, start='20260105', end='20260106', property_code='0004'):
    # 공식 페이지의 필수 항목만 사용. 인코딩 키도 디코딩한 뒤 한 번만 urlencode.
    return {'serviceKey': urllib.parse.unquote(key.strip()), 'pageNo': 1, 'numOfRows': 10,
            'resultType': 'json', 'cltrTypeCd': '0003', 'prptDivCd': property_code,
            'opbdDtStart': start, 'opbdDtEnd': end}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scheme', choices=['http', 'https'], default='https')
    parser.add_argument('--start', default='20260105')
    parser.add_argument('--end', default='20260106')
    parser.add_argument('--property-code', choices=price.ALL_PRPT.split(','), default='0004')
    parser.add_argument('--matrix', action='store_true', help='최소 요청과 한 항목씩 비교 (10회)')
    args = parser.parse_args()
    load_dotenv(Path(__file__).parent / '.env')
    key = price._key()
    if not key:
        print(json.dumps({'category': 'missing_key'}))
        return 1
    try:
        start = datetime.strptime(args.start, '%Y%m%d').date()
        end = datetime.strptime(args.end, '%Y%m%d').date()
        if not start <= end < date.today():
            raise ValueError
    except ValueError:
        parser.error('과거 yyyyMMdd 시작일/종료일이 필요합니다')
    params = minimal_params(key, args.start, args.end, args.property_code)
    cases = [('minimal', {})]
    if args.matrix:
        cases += [('property', {'prptDivCd': '0007'}), ('type', {'cltrTypeCd': '0001'}),
                  ('dates', {'opbdDtStart': '20251001', 'opbdDtEnd': '20251002'}),
                  ('multiple_properties', {'prptDivCd': '0007,0005'}),
                  ('page_size', {'numOfRows': 100}), ('status', {'pbctStatCd': '0010'}),
                  ('keyword', {'onbidCltrNm': '제빙기'}),
                  ('two_year_range', {'opbdDtStart': (end-timedelta(days=730)).strftime('%Y%m%d')}),
                  ('invalid_key_control', {'serviceKey': 'invalid-diagnostic-control'})]
    failed = False
    endpoint = price.ONBID_URL.replace('https:', args.scheme+':', 1)
    for label, changes in cases:
        query = params | changes
        result = {'case': label, 'scheme': args.scheme,
                  'parameters': {k: v for k, v in query.items() if k != 'serviceKey'}}
        try:
            body = request_body(endpoint+'?'+urllib.parse.urlencode(query), timeout=20)
            result.update(category='success', total_count=body.get('totalCount'))
        except OnbidError as exc:
            result.update(category=exc.category, http_status=exc.http_status, api_code=exc.api_code)
            failed = True
        except Exception:
            result.update(category='unexpected')
            failed = True
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())

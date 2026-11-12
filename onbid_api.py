"""온비드 응답 오류 분류. 예외와 로그에는 요청 URL/응답 원문을 보관하지 않는다."""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


class OnbidError(RuntimeError):
    def __init__(self, category, *, http_status=None, api_code=None):
        if category.startswith('API_ERROR_'):  # 기존 예외 생성 경로와 호환
            api_code = category.removeprefix('API_ERROR_')
            category = _api_category(api_code)
        self.category = category
        self.http_status = http_status
        # 서버의 임의 문자열(키/URL 포함 가능)을 오류에 복사하지 않는다.
        self.api_code = str(api_code) if str(api_code) in {
            '01', '02', '03', '04', '05', '10', '11', '12', '20', '21',
            '22', '30', '31', '32', '99',
        } else None
        super().__init__(f'onbid category={category} http={http_status} api_code={self.api_code}')


def _api_category(code):
    if code in {'10', '11'}:
        return 'parameter'
    if code in {'20', '21', '30', '31', '32'}:
        return 'authentication_or_permission'
    if code == '22':
        return 'quota'
    return 'api'


def parse_response(raw, http_status=200):
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeError):
        doc = None
    if isinstance(doc, dict):
        root = doc.get('OpenAPI_ServiceResponse', doc.get('response', doc))
        if not isinstance(root, dict):
            raise OnbidError('response_format', http_status=http_status)
        header = root.get('cmmMsgHeader') or root.get('header') or {}
        if isinstance(header, dict):
            code = header.get('returnReasonCode', header.get('resultCode'))
            if code is not None and str(code) not in {'0', '00', '0000'}:
                raise OnbidError(_api_category(str(code)), http_status=http_status, api_code=code)
    else:
        try:
            root_xml = ET.fromstring(raw)
            code = root_xml.findtext('.//returnReasonCode') or root_xml.findtext('.//resultCode')
        except (ET.ParseError, ValueError):
            code = None
        if code and code not in {'0', '00', '0000'}:
            raise OnbidError(_api_category(code), http_status=http_status, api_code=code)
    if http_status >= 400:
        if http_status == 503 and b'connection timeout' in raw.lower():
            category = 'upstream_connection_timeout'
        elif http_status == 429:
            category = 'quota'
        elif http_status in (401, 403):
            category = 'authentication_or_permission'
        elif http_status >= 500:
            category = 'server_or_gateway'
        else:
            category = 'http'
        raise OnbidError(category, http_status=http_status)
    if not isinstance(doc, dict) or not isinstance(root.get('body'), dict):
        raise OnbidError('response_format', http_status=http_status)
    if not {'items', 'totalCount'} & root['body'].keys():
        raise OnbidError('response_format', http_status=http_status)
    return root['body']


def request_body(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(2_000_001)
            status = getattr(response, 'status', 200)
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(2_000_001), exc.code
    except (TimeoutError, socket.timeout):
        raise OnbidError('network_timeout') from None
    except urllib.error.URLError as exc:
        category = 'tls' if isinstance(exc.reason, ssl.SSLError) else 'network'
        raise OnbidError(category) from None
    except OSError:
        raise OnbidError('network') from None
    if len(raw) > 2_000_000:
        raise OnbidError('response_too_large', http_status=status)
    return parse_response(raw, status)

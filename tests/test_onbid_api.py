import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import crawler
import price
from diagnose_onbid import minimal_params
from onbid_api import OnbidError, parse_response, request_body


class OnbidApiTest(unittest.TestCase):
    def test_minimum_request_uses_required_fields_and_single_encoding(self):
        p = minimal_params('abc%2Bdef%3D')
        self.assertEqual(p['serviceKey'], 'abc+def=')
        self.assertEqual(p['numOfRows'], 10)
        self.assertNotIn(',', p['prptDivCd'])
        self.assertNotIn('pbctStatCd', p)
        self.assertNotIn('onbidCltrNm', p)

    def test_success_and_empty_results_are_distinct_from_api_error(self):
        self.assertEqual(parse_response(b'{"header":{"resultCode":"00"},"body":{"items":null}}'),
                         {'items': None})
        for code, category in [('10', 'parameter'), ('30', 'authentication_or_permission'),
                               ('22', 'quota')]:
            raw = json.dumps({'OpenAPI_ServiceResponse': {'cmmMsgHeader':
                             {'returnReasonCode': code}}}).encode()
            with self.assertRaises(OnbidError) as caught:
                parse_response(raw)  # HTTP 200 오류 본문도 실패다.
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(caught.exception.api_code, code)

    def test_xml_error_and_gateway_timeout(self):
        with self.assertRaises(OnbidError) as caught:
            parse_response(b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>10</returnReasonCode>'
                           b'</cmmMsgHeader></OpenAPI_ServiceResponse>', 400)
        self.assertEqual(caught.exception.category, 'parameter')
        with self.assertRaises(OnbidError) as caught:
            parse_response(b'upstream connect error: connection timeout', 503)
        self.assertEqual(caught.exception.category, 'upstream_connection_timeout')

    def test_malformed_success_is_not_empty_data(self):
        for raw in (b'{}', b'<html>error</html>', b'{"body":[]}', b'{"body":{}}'):
            with self.assertRaises(OnbidError):
                parse_response(raw)

    def test_transport_exceptions_and_logs_do_not_expose_key_or_url(self):
        secret_url = 'https://example.org/?serviceKey=private-test-key'
        error = urllib.error.HTTPError(secret_url, 400, secret_url, {},
            io.BytesIO(b'{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"returnReasonCode":"10"}}}'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(OnbidError) as caught:
                request_body(secret_url)
        self.assertNotIn(secret_url, str(caught.exception))
        with patch('price._key', return_value='private-test-key'), \
             patch('price._query', side_effect=caught.exception), \
             self.assertLogs('price', level='WARNING') as logs:
            self.assertEqual(price.evidence('제빙기'), [])
        self.assertNotIn('private-test-key', '\n'.join(logs.output))
        with patch('urllib.request.urlopen', side_effect=TimeoutError(secret_url)):
            with self.assertRaises(OnbidError) as caught:
                request_body(secret_url)
        self.assertEqual(caught.exception.category, 'network_timeout')
        self.assertNotIn('private-test-key', str(caught.exception))

    def test_crawler_failure_never_saves_partial_or_empty_api_result(self):
        with patch('price._key', return_value='test'), \
             patch('price._query', side_effect=OnbidError('parameter', api_code='10')), \
             patch('listings.save') as save, patch('sys.argv', ['crawler.py', 'onbid']), \
             patch('builtins.print') as output:
            self.assertEqual(crawler.main(), 1)
        save.assert_not_called()
        self.assertIn('parameter', output.call_args.args[0])


if __name__ == '__main__':
    unittest.main()

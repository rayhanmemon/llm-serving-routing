import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).parent))
import unittest

spec = importlib.util.spec_from_file_location('validator', Path(__file__).with_name('validate-transfer.py'))
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def stream():
    values = [
        {'choices': [{'index': 0, 'text': 'answer', 'finish_reason': None}]},
        {'choices': [{'index': 0, 'text': '', 'finish_reason': 'length'}]},
        {'choices': [], 'usage': {'prompt_tokens': 512, 'completion_tokens': 128}},
    ]
    return ''.join('data: ' + json.dumps(x) + '\n\n' for x in values) + 'data: [DONE]\n\n'


def record():
    return {'request': json.dumps({'model': 'test', 'prompt': 'prompt', 'max_tokens': 128, 'stream': True, 'ignore_eos': True}),
            'response': stream(), 'error': None, 'start_time': 10, 'end_time': 12,
            'info': {'response_metrics': {
                'response_chunks': [json.dumps({'choices': [
                    {'index': 0, 'text': 'answer', 'finish_reason': None}]})],
                'chunk_times': [11],
                # This is an estimated per-token series, not 1:1 with SSE chunks.
                'output_token_times': [11, 11.5]}}}


class TransferValidationTest(unittest.TestCase):
    def test_complete_and_broken_streams(self):
        self.assertEqual(len(v.validate_stream(stream(), 512, 128)), 1)
        cases = [stream().replace('data: [DONE]\n\n', ''),
                 stream().replace('"length"', '"stop"'),
                 stream().replace('"completion_tokens": 128', '"completion_tokens": 127'),
                 stream().replace('"prompt_tokens": 512', '"prompt_tokens": 511'),
                 'data: {broken}\n\n' + stream(),
                 'data: {"error":"failed"}\n\n' + stream(),
                 stream() + 'data: {}\n\n',
                 stream().replace('"text": "answer"', '"text": ""')]
        for raw in cases:
            with self.subTest(raw=raw[:80]), self.assertRaises((ValueError, json.JSONDecodeError)):
                v.validate_stream(raw, 512, 128)

    def test_request_errors_counts_and_content_timing(self):
        hashes, times = v.validate_records([record()], 512, 128, 1)
        self.assertEqual(times, [1]); self.assertEqual(len(hashes), 1)
        cases = [[], [dict(record(), error={'error_msg': 'timeout'})]]
        broken = record(); broken['info']['response_metrics']['chunk_times'] = [9]; cases.append([broken])
        broken = record(); broken['info']['response_metrics']['chunk_times'] = [11, 11.5]; cases.append([broken])
        broken = record(); broken['info']['response_metrics']['response_chunks'] = ['{}']; cases.append([broken])
        broken = record(); broken['info']['response_metrics']['output_token_times'] = [float('nan')]; cases.append([broken])
        for records in cases:
            with self.subTest(records=records), self.assertRaises(ValueError):
                v.validate_records(records, 512, 128, 1)

    def test_route_pin_and_missing_records(self):
        expected = base64.b64encode(b'ns/decoder-rank-0').decode()
        good = {'run_id': 'arm-1', 'request_id': 'request-1', 'requested_decoder': expected,
                'selected_decoder': expected, 'status': 200, 'response_flags': '-'}
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'envoy.log'
            file.write_text('2026-09-15T00:00:00Z ' + json.dumps(good) + '\n')
            v.validate_routes([file], 'arm-1', 'decoder', 'ns', 1)
            for bad in [dict(good, selected_decoder='wrong'), dict(good, requested_decoder='wrong'),
                        dict(good, requested_decoder=base64.b64encode(b'ns/decoder').decode()),
                        dict(good, request_id='-'), dict(good, status=503), dict(good, response_flags='UT'),
                        dict(good, run_id='different')]:
                file.write_text(json.dumps(bad) + '\n')
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    v.validate_routes([file], 'arm-1', 'decoder', 'ns', 1)
            file.write_text(json.dumps(good) + '\n' + json.dumps(good) + '\n')
            with self.assertRaises(ValueError):
                v.validate_routes([file], 'arm-1', 'decoder', 'ns', 2)


if __name__ == '__main__':
    unittest.main()

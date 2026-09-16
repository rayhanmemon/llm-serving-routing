import importlib.util
import os
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name('import-image.py')
SPEC = importlib.util.spec_from_file_location('import_image', SCRIPT)
image_import = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image_import)


class ImageImportTransportTest(unittest.TestCase):
    def test_spdy_override_requires_supported_kubectl(self):
        self.assertTrue(image_import.kubectl_supports_spdy_override(
            {'clientVersion': {'major': '1', 'minor': '34'}}))
        self.assertTrue(image_import.kubectl_supports_spdy_override(
            {'clientVersion': {'major': '1', 'minor': '30+'}}))
        self.assertFalse(image_import.kubectl_supports_spdy_override(
            {'clientVersion': {'major': '1', 'minor': '29'}}))
        self.assertFalse(image_import.kubectl_supports_spdy_override({}))

    def test_upload_has_unbounded_api_request_and_bounded_process_contract(self):
        command = image_import.upload_command('ctx', 'ns', 'helper')
        self.assertIn('--request-timeout=0', command)
        self.assertNotIn('--request-timeout=30s', command)
        self.assertEqual(command[-6:], ['-n', 'ns', 'exec', '-i', 'helper', '--'])

        original = {'PATH': os.environ.get('PATH', ''), 'UNCHANGED': 'yes'}
        environment = image_import.upload_environment(original)
        self.assertEqual(environment['KUBECTL_REMOTE_COMMAND_WEBSOCKETS'], 'false')
        self.assertEqual(environment['UNCHANGED'], 'yes')
        self.assertNotIn('KUBECTL_REMOTE_COMMAND_WEBSOCKETS', original)


if __name__ == '__main__':
    unittest.main()

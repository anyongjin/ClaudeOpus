import copy
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('render', ROOT / 'render.py')


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(cls.module)
        cls.example = json.loads((ROOT / 'inputs.example.json').read_text())

    def inputs(self):
        data = copy.deepcopy(self.example)
        data.update(CN_HOST='192.0.2.10', US_HOST='us.example.test',
                    CN_PUBLIC_IPV4='192.0.2.10', US_PUBLIC_IPV4='192.0.2.20',
                    CN_PASSWORD='cn secret " slash \\ :\n中文',
                    US_PASSWORD='us secret " : \\', CN_CERT_SHA256='a' * 64,
                    US_SNI='us.example.test', RES_HOST='res.example.test',
                    RES_USERNAME='user', RES_PASSWORD='res secret : " \\',
                    IKUUU_SUBSCRIPTION_URL='https://example.test/sub?token=private')
        return data

    def test_public_tls_and_typed_ports(self):
        products = self.module.build(self.inputs())
        cn, us = products['cn-mixin.yaml'], products['us-sing-box.json']
        self.assertIsInstance(cn['override']['listeners'][0]['port'], int)
        self.assertFalse(cn['override']['proxies'][0]['skip-cert-verify'])
        self.assertNotIn('fingerprint', cn['override']['proxies'][0])
        self.assertEqual(us['route']['final'], 'upstream-socks')
        self.assertEqual(us['outbounds'][0]['password'], self.inputs()['RES_PASSWORD'])
        self.assertIsInstance(us['outbounds'][0]['server_port'], int)
        self.assertNotIn('enable', cn['override']['tun'])

    def test_self_signed_requires_pin(self):
        data = self.inputs()
        data.update(US_TLS_MODE='self-signed', US_CERT_SHA256='')
        with self.assertRaises(ValueError):
            self.module.build(data)
        data['US_CERT_SHA256'] = 'b' * 64
        node = self.module.build(data)['cn-mixin.yaml']['override']['proxies'][0]
        self.assertTrue(node['skip-cert-verify'])
        self.assertEqual(node['fingerprint'], 'b' * 64)

    def test_missing_and_invalid_inputs(self):
        for patch in ({'CN_HOST': ''}, {'CN_PORT': '443'}, {'CN_PORT': True},
                      {'CN_PORT': 70000}, {'US_TLS_MODE': 'none'},
                      {'CN_CERT_SHA256': 'wrong'}, {'RES_AUTH_MODE': 'guess'},
                      {'IKUUU_SUBSCRIPTION_URL': 'http://example.test/private'},
                      {'CN_PUBLIC_IPV4': 'not-an-ip'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.module.build(dict(self.inputs(), **patch))

    def test_whitelist_auth(self):
        data = self.inputs()
        data.update(RES_AUTH_MODE='whitelist', RES_USERNAME='', RES_PASSWORD='')
        outbound = self.module.build(data)['us-sing-box.json']['outbounds'][0]
        self.assertNotIn('username', outbound)
        self.assertNotIn('password', outbound)

    def test_client_values_are_json_escaped(self):
        result = self.module.build(self.inputs())['client-script.js']
        self.assertIn(json.dumps(self.inputs()['CN_PASSWORD'], ensure_ascii=False), result)
        self.assertNotIn('<CN_PORT>', result)

    def test_output_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder) / 'private'
            products = self.module.build(self.inputs())
            self.module.write_products(products, out)
            if os.name == 'posix':
                self.assertEqual(out.stat().st_mode & 0o777, 0o700)
                for p in out.iterdir():
                    self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.module.write_products(products, out)

    def test_outputs_cannot_be_in_bundle(self):
        with self.assertRaises(ValueError):
            self.module.write_products({}, ROOT / 'private-test')

    def test_server_certificate_paths_use_linux_semantics(self):
        with patch.object(self.module, 'Path', pathlib.PureWindowsPath):
            self.module.build(self.inputs())

    def test_cli_reads_utf8_bom_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            inputs = pathlib.Path(folder) / 'inputs.json'
            inputs.write_text(json.dumps(self.inputs(), ensure_ascii=False), encoding='utf-8-sig')
            output = pathlib.Path(folder) / 'private'
            result = subprocess.run([sys.executable, str(ROOT / 'render.py'),
                                     '--inputs', str(inputs), '--output', str(output)],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            resolved = json.loads((output / 'resolved-inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(resolved['CN_PASSWORD'], self.inputs()['CN_PASSWORD'])

    def test_udp_only_enabled_when_explicitly_confirmed(self):
        data = self.inputs()
        self.assertEqual(self.module.build(data)['us-sing-box.json']['outbounds'][0]['network'], 'tcp')
        data['RES_UDP_SUPPORTED'] = True
        self.assertNotIn('network', self.module.build(data)['us-sing-box.json']['outbounds'][0])


if __name__ == '__main__':
    unittest.main()

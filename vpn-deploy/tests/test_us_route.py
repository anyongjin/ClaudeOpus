import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("us_route", Path(__file__).resolve().parents[1] / "us-route.py")
route = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route)


class UsRouteTests(unittest.TestCase):
    def test_domains_reject_urls_and_rule_injection(self):
        self.assertEqual(route.domain("Example.COM."), "example.com")
        self.assertEqual(route.domain("苹果.cn"), "xn--gtvz22d.cn")
        for value in ["https://example.com", "*.apple.com", "a.com,DIRECT", "a/b", "bad..com", "-bad.com"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                route.domain(value)

    def test_persistent_script_preserves_original_and_is_idempotent(self):
        base = 'function main(c) { c.original = true; return c; }\n'
        rules = ["DOMAIN-SUFFIX,example.com,AI-US-VIA-CN"]
        script = route.script_with_rules(base, rules)
        self.assertEqual(route.split_script(script), (base, rules))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "check.js"
            path.write_text(script + '\nconst c = {rules: ["MATCH,DIRECT"]};\nconsole.log(JSON.stringify(main(main(c))));')
            result = subprocess.run(["node", str(path)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"original": True, "rules": rules + ["MATCH,DIRECT"]})
        with self.assertRaises(ValueError):
            route.split_script(script + "// someone added code\n")

    def test_runtime_reapply_and_remove_preserve_other_config(self):
        original = 'mixed-port: 7897\nsecret: "private"\nrules:\n- DOMAIN-SUFFIX,example.com,DIRECT\n- MATCH,DEFAULT\ndns:\n  enable: true\n'
        first = ["DOMAIN-SUFFIX,example.com,AI-US-VIA-CN"]
        candidate = route.runtime_with_rules(original, first)
        self.assertLess(candidate.index(first[0]), candidate.index("example.com,DIRECT"))
        self.assertEqual(route.runtime_with_rules(candidate, first), candidate)
        empty = route.runtime_with_rules(candidate, [])
        self.assertEqual(empty.replace(route.RULE_BEGIN + route.RULE_END, ""), original)

    def test_apply_reload_failure_restores_both_files_and_core(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "profiles").mkdir()
            script = 'function main(c) { return c; }\n'
            runtime = 'rules:\n- MATCH,DIRECT\n'
            (root / "profiles/Script.js").write_text(script)
            (root / "clash-verge.yaml").write_text(runtime)
            calls = []

            def api(socket, method, endpoint, data=None):
                calls.append((method, endpoint, data))
                if endpoint == "/configs" and method == "GET":
                    return {"mode": "rule"}
                if endpoint.startswith("/proxies/"):
                    return {"all": ["CN-GATEWAY-HY2"], "now": "CN-GATEWAY-HY2"}
                if method == "PUT" and data["payload"] != runtime:
                    raise RuntimeError("Simulated reload failure")

            with patch.object(route, "api", side_effect=api), patch.object(route.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")):
                with self.assertRaisesRegex(RuntimeError, "Simulated"):
                    route.apply(root, Path("socket"), Path("core"), route.script_with_rules(script, []), [])
            self.assertEqual((root / "profiles/Script.js").read_text(), script)
            self.assertEqual((root / "clash-verge.yaml").read_text(), runtime)
            self.assertEqual(calls[-1], ("PUT", "/configs", {"payload": runtime}))

    def test_remove_after_subscription_regenerates_without_markers(self):
        previous = ["DOMAIN-SUFFIX,example.com,AI-US-VIA-CN"]
        for scalar in [previous[0], json.dumps(previous[0]), "'" + previous[0] + "'"]:
            regenerated = 'rules:\n- ' + scalar + '\n- MATCH,DEFAULT\n'
            candidate = route.runtime_with_rules(regenerated, [], previous)
            self.assertNotIn("example.com", candidate)
            self.assertIn("MATCH,DEFAULT", candidate)


if __name__ == "__main__":
    unittest.main()

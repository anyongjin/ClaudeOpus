#!/usr/bin/env python3
"""Persist extra US routes in macOS Clash Verge Rev and reload its running core."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

GROUP = "AI-US-VIA-CN"
BEGIN = "// BEGIN vpn-deploy US routes\n"
END = "// END vpn-deploy US routes"
RULE_BEGIN = "# BEGIN vpn-deploy US routes\n"
RULE_END = "# END vpn-deploy US routes\n"
DEFAULT_ROOT = Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
DEFAULT_CORE = Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo")


def domain(value):
    value = value.strip().rstrip(".").lower().encode("idna").decode("ascii")
    if len(value) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                   for part in value.split(".")):
        raise ValueError("Expected a domain, without https://, paths or wildcards: " + value)
    return value


def split_script(source):
    if BEGIN not in source:
        return source.rstrip() + "\n", []
    base, block = source.split(BEGIN, 1)
    if block.count(END) != 1 or block.split(END, 1)[1].strip():
        raise ValueError("Managed script block was edited; refusing to overwrite")
    line = block.splitlines()[0]
    if not line.startswith("const vpnDeployUsRules = ") or not line.endswith(";"):
        raise ValueError("Invalid managed rules")
    rules = json.loads(line[len("const vpnDeployUsRules = "):-1])
    if not isinstance(rules, list) or not all(isinstance(r, str) and r.endswith("," + GROUP) for r in rules):
        raise ValueError("Invalid managed rules")
    return base, rules


def script_with_rules(base, rules):
    return base + BEGIN + "const vpnDeployUsRules = " + json.dumps(rules, ensure_ascii=False) + ";\n" + """const vpnDeployOriginalMain = main;
main = function(config, profileName) {
  config = vpnDeployOriginalMain(config, profileName);
  const owned = new Set(vpnDeployUsRules);
  config.rules = vpnDeployUsRules.concat((config.rules || []).filter(r => !owned.has(r)));
  return config;
};
""" + END + "\n"


def runtime_with_rules(source, rules, previous_rules=()):
    # Preserve all YAML verbatim, including secrets, quoting and unrelated rules.
    if RULE_BEGIN in source:
        if source.count(RULE_BEGIN) != 1 or source.count(RULE_END) != 1:
            raise ValueError("Invalid managed runtime block")
        before, rest = source.split(RULE_BEGIN, 1)
        source = before + rest.split(RULE_END, 1)[1]
    # Verge regenerates YAML from the global script without retaining comments.
    # Remove the previous owned rules in its plain/quoted scalar formats as well.
    old_lines = {"- " + value for r in previous_rules for value in
                 (r, json.dumps(r, ensure_ascii=False), json.dumps(r), "'" + r.replace("'", "''") + "'")}
    source = "".join(line for line in source.splitlines(keepends=True)
                     if line.strip() not in old_lines)
    matches = list(re.finditer(r"(?m)^rules:[ \t]*\r?\n", source))
    if len(matches) != 1:
        raise ValueError("Expected exactly one block-style rules section")
    block = RULE_BEGIN + "".join("- " + json.dumps(r, ensure_ascii=False) + "\n" for r in rules) + RULE_END
    i = matches[0].end()
    return source[:i] + block + source[i:]


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix=".us-route-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def api(socket, method, endpoint, data=None):
    cmd = ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "30",
           "--unix-socket", str(socket), "-X", method, "http://localhost" + endpoint]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    result = subprocess.run(cmd, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Clash API failed: " + (result.stderr or result.stdout)[:300])
    return json.loads(result.stdout) if result.stdout.strip() else None


def apply(root, socket, core, script, rules):
    script_path = root / "profiles/Script.js"
    runtime_path = root / "clash-verge.yaml"
    old_script, old_runtime = script_path.read_text(), runtime_path.read_text()
    runtime = runtime_with_rules(old_runtime, rules, split_script(old_script)[1])
    state = api(socket, "GET", "/configs")
    if state.get("mode", "").lower() != "rule":
        raise ValueError("Clash must already be in rule mode")
    proxy = api(socket, "GET", "/proxies/" + GROUP)
    if proxy.get("all") != ["CN-GATEWAY-HY2"] or proxy.get("now") != "CN-GATEWAY-HY2":
        raise ValueError("US group must contain and select only CN-GATEWAY-HY2")
    backup = root / "backups" / ("us-route-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700, parents=True)
    atomic_write(backup / "Script.js", old_script)
    atomic_write(backup / "clash-verge.yaml", old_runtime)
    candidate = backup / "candidate.yaml"
    candidate_script = backup / "candidate.js"
    atomic_write(candidate, runtime)
    atomic_write(candidate_script, script)
    log = ""
    for cmd in (["node", "--check", str(candidate_script)],
                [str(core), "-t", "-d", str(root), "-f", str(candidate)]):
        check = subprocess.run(cmd, capture_output=True, text=True)
        log += check.stdout + check.stderr
        atomic_write(backup / "validation.log", log)
        if check.returncode:
            raise RuntimeError("Candidate validation failed; live files unchanged. See " + str(backup / "validation.log"))
    try:
        atomic_write(script_path, script)
        atomic_write(runtime_path, runtime)
        api(socket, "PUT", "/configs", {"payload": runtime})
        live = api(socket, "GET", "/rules")["rules"]
        expected = [{"type": {"DOMAIN-SUFFIX": "DomainSuffix", "GEOSITE": "GeoSite"}[r.split(",")[0]],
                     "payload": r.split(",")[1], "proxy": GROUP} for r in rules]
        if any(any(actual.get(k) != value for k, value in wanted.items())
               for actual, wanted in zip(live, expected)) or len(live) < len(expected):
            raise RuntimeError("Running core rules differ from requested priority rules")
    except Exception:
        atomic_write(script_path, old_script)
        atomic_write(runtime_path, old_runtime)
        try:
            api(socket, "PUT", "/configs", {"payload": old_runtime})
        except Exception as rollback:
            raise RuntimeError("Rollback reload failed; restore from " + str(backup)) from rollback
        raise
    print("Applied %d persistent US rules. Backup: %s" % (len(rules), backup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["add", "remove", "list", "apply"])
    parser.add_argument("domains", nargs="*", help="Root domains; subdomains are included")
    parser.add_argument("--geosite", action="append", default=[], help="Category, e.g. apple; repeatable")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--socket", type=Path, default=Path("/var/run/clash-verge-service/users") / str(os.getuid()) / "verge-mihomo.sock")
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    args = parser.parse_args()
    try:
        base, rules = split_script((args.config_dir / "profiles/Script.js").read_text())
        targets = ["DOMAIN-SUFFIX," + domain(d) + "," + GROUP for d in args.domains]
        for category in args.geosite:
            if not re.fullmatch(r"[a-z0-9@!_-]+", category):
                raise ValueError("Invalid geosite category")
            targets.append("GEOSITE," + category + "," + GROUP)
        if args.action in ("add", "remove") and not targets:
            raise ValueError("Specify domains or --geosite")
        if args.action in ("list", "apply") and targets:
            raise ValueError("Domains/--geosite only work with add/remove")
        if args.action == "list":
            print("\n".join(rules) if rules else "No extra managed US rules")
            return
        if args.action == "add":
            rules = list(dict.fromkeys(rules + targets))
        elif args.action == "remove":
            rules = [r for r in rules if r not in targets]
        apply(args.config_dir, args.socket, args.core, script_with_rules(base, rules), rules)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render portable examples into a new private directory; never deploy them."""

import argparse
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
TOKEN = re.compile(r"<([A-Z][A-Z0-9_]*)>")


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or TOKEN.search(value):
        raise ValueError("Missing or invalid " + key)
    return value


def _pin(data, key):
    value = _text(data, key).replace(":", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("Expected SHA-256 hex in " + key)
    data[key] = value.lower()


def _render(value, data):
    if isinstance(value, dict):
        return {key: _render(item, data) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, data) for item in value]
    if not isinstance(value, str):
        return value
    match = TOKEN.fullmatch(value)
    if match:
        return data[match.group(1)]
    return TOKEN.sub(lambda match: str(data[match.group(1)]), value)


def build(inputs):
    """Validate gathered inputs; return serializable configs and a JS string."""
    if not isinstance(inputs, dict):
        raise ValueError("Inputs must be a JSON object")
    data = json.loads((ROOT / "inputs.example.json").read_text(encoding="utf-8"))
    data.update(inputs)
    for key in ["CN_PASSWORD", "US_PASSWORD", "CONTROLLER_SECRET"]:
        if not data.get(key):
            data[key] = secrets.token_urlsafe(32)
    for key in ["CN_HOST", "US_HOST", "RES_HOST", "CN_SNI"]:
        value = _text(data, key)
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
            raise ValueError("Expected a host name or IP in " + key)
    for key in ["CN_PUBLIC_IPV4", "US_PUBLIC_IPV4"]:
        try:
            ipaddress.IPv4Address(_text(data, key))
        except ipaddress.AddressValueError as exc:
            raise ValueError("Expected IPv4 address in " + key) from exc
    for key in ["CN_PORT", "US_PORT", "RES_PORT"]:
        if type(data[key]) is not int or not 1 <= data[key] <= 65535:
            raise ValueError("Expected integer port in " + key)
    if type(data["ROUTING_MARK"]) is not int or not 1 <= data["ROUTING_MARK"] <= 0xFFFFFFFF:
        raise ValueError("Invalid ROUTING_MARK")
    for key in ["CN_PASSWORD", "US_PASSWORD", "CONTROLLER_SECRET"]:
        _text(data, key)
    _pin(data, "CN_CERT_SHA256")
    url = urlsplit(_text(data, "IKUUU_SUBSCRIPTION_URL"))
    if url.scheme != "https" or not url.hostname:
        raise ValueError("Subscription URL must use HTTPS")
    if data["US_TLS_MODE"] == "public":
        _text(data, "US_SNI")
        default_cert = "/etc/letsencrypt/live/" + data["US_SNI"] + "/fullchain.pem"
        default_key = "/etc/letsencrypt/live/" + data["US_SNI"] + "/privkey.pem"
    elif data["US_TLS_MODE"] == "self-signed":
        _pin(data, "US_CERT_SHA256")
        data["US_SNI"] = data["US_SNI"] or "us-relay.invalid"
        default_cert, default_key = "/etc/sing-box/tls/server.crt", "/etc/sing-box/tls/server.key"
    else:
        raise ValueError("US_TLS_MODE must be public or self-signed")
    data["US_CERT_PATH"] = data["US_CERT_PATH"] or default_cert
    data["US_KEY_PATH"] = data["US_KEY_PATH"] or default_key
    for key in ["US_CERT_PATH", "US_KEY_PATH"]:
        if not PurePosixPath(_text(data, key)).is_absolute():
            raise ValueError("Certificate path must be absolute: " + key)
    if data["RES_AUTH_MODE"] == "password":
        _text(data, "RES_USERNAME")
        _text(data, "RES_PASSWORD")
    elif data["RES_AUTH_MODE"] != "whitelist":
        raise ValueError("RES_AUTH_MODE must be password or whitelist")
    if type(data["RES_UDP_SUPPORTED"]) is not bool:
        raise ValueError("RES_UDP_SUPPORTED must be a JSON boolean")

    cn = _render(json.loads((ROOT / "cn-mixin.template.json").read_text(encoding="utf-8")), data)
    node = cn["override"]["proxies"][0]
    if data["US_TLS_MODE"] == "self-signed":
        node.update({"skip-cert-verify": True, "fingerprint": data["US_CERT_SHA256"]})
    us = _render(json.loads((ROOT / "us-sing-box.template.json").read_text(encoding="utf-8")), data)
    if data["RES_AUTH_MODE"] == "whitelist":
        for key in ["username", "password"]:
            us["outbounds"][0].pop(key, None)
    if not data["RES_UDP_SUPPORTED"]:
        us["outbounds"][0]["network"] = "tcp"

    script = (ROOT / "client-script.js").read_text(encoding="utf-8")
    # Replace complete JS string literals, not raw substrings in secret values.
    script = re.sub(r'"<([A-Z][A-Z0-9_]*)>"',
                    lambda match: json.dumps(data[match.group(1)], ensure_ascii=False), script)
    return {"cn-mixin.yaml": cn, "us-sing-box.json": us,
            "client-script.js": script, "resolved-inputs.json": data}


def write_products(products, output):
    output = Path(output).expanduser().resolve()
    try:
        output.relative_to(ROOT.parent)
    except ValueError:
        pass
    else:
        raise ValueError("Private output must be outside this repository")
    if output.exists():
        raise FileExistsError("Output already exists; choose a new private directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".vpn-render-", dir=output.parent))
    try:
        os.chmod(staging, 0o700)
        for name, value in products.items():
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            path = staging / name
            with path.open("x", encoding="utf-8") as handle:
                os.chmod(path, 0o600)
                handle.write(text)
        if output.exists():
            raise FileExistsError("Output was created concurrently")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        products = build(json.loads(args.inputs.read_text(encoding="utf-8-sig")))
        write_products(products, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, "Render failed: " + str(exc) + "\n")
    print("Private configs generated. Validate against installed stable cores before deployment.")


if __name__ == "__main__":
    main()

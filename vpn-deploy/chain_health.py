#!/usr/bin/env python3
"""Rank airport first hops using isolated, residential-exit HY2 probes."""

import argparse
import concurrent.futures
import copy
from dataclasses import dataclass, field
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import signal
import socket
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


FORBIDDEN = {"DIRECT", "REJECT", "REJECT-DROP", "COMPATIBLE", "PASS"}


@dataclass
class ProbeResult:
    name: str
    latencies_ms: list = field(default_factory=list)
    attempts: int = 2
    stream_ok: object = None
    stream_seconds: float = 0.0
    stream_diagnostic: dict = field(default_factory=dict)
    bulk_ok: object = None
    bulk_seconds: float = 0.0
    bulk_diagnostic: dict = field(default_factory=dict)
    production_ok: object = None
    verified_at: float = 0.0


@dataclass
class Decision:
    target: object = None
    reason: str = "keep-current"


def score(result):
    samples = result.latencies_ms
    if not samples:
        return math.inf
    failure_rate = 1 - len(samples) / max(result.attempts, 1)
    # A complete bulk transfer contributes its actual wall time, in milliseconds.
    return (statistics.median(samples) + (max(samples) - min(samples)) / 2
            + failure_rate * 5000 + result.bulk_seconds * 1000)


def qualified(result):
    return (result.name not in FORBIDDEN and result.attempts >= 2
            and len(result.latencies_ms) == result.attempts and result.stream_ok is True
            and result.bulk_ok is not False)


def choose_candidates(names, current, state, limit=8, now=None):
    now = time.time() if now is None else now
    names = list(dict.fromkeys(n for n in names if n not in FORBIDDEN))
    if limit < 1:
        return []
    selected = [current] if current in names else []
    history = state.get("nodes", {})
    champions = sorted(
        (n for n in names if n != current
         and history.get(n, {}).get("stream_ok") is True
         and history.get(n, {}).get("bulk_ok") is not False
         and 0 <= now - history[n].get("updated_at", 0) <= 1800
         and 0 <= now - history[n].get("stream_at", 0) <= 1800),
        key=lambda n: history[n].get("score_ms", math.inf),
    )
    selected.extend(champions[:min(2, max(0, limit - len(selected)))])
    pool = [n for n in names if n not in selected]
    if pool:
        cursor = state.get("cursor", 0) % len(pool)
        count = min(limit - len(selected), len(pool))
        selected.extend((pool[cursor:] + pool[:cursor])[:count])
        state["cursor"] = (cursor + count) % len(pool)
    return selected[:limit]


def decide_switch(current, results, state, now, cooldown_seconds=600,
                  improvement_ratio=0.25, min_improvement_ms=80, required_wins=2):
    available = [r for r in results if qualified(r) and r.name != current]
    old = next((r for r in results if r.name == current), None)
    if not available or old is None:
        state.pop("pending", None)
        return Decision(reason="no-verified-alternative")
    if old.production_ok is True and not qualified(old):
        state.pop("pending", None)
        return Decision(reason="production-healthy-current-unverified")
    best = min(available, key=score)
    if (old.stream_ok is False or old.bulk_ok is False
            or old.production_ok is False or not old.latencies_ms):
        state.pop("pending", None)
        return Decision(best.name, "failure-recovery")
    gain = score(old) - score(best)
    if gain < min_improvement_ms or gain < score(old) * improvement_ratio:
        state.pop("pending", None)
        return Decision(reason="gain-too-small")
    if now - state.get("last_switch_at", 0) < cooldown_seconds:
        state.pop("pending", None)
        return Decision(reason="cooldown")
    pending = state.get("pending", {})
    consecutive = (pending.get("name") == best.name
                   and now - state.get("last_run_at", now) <= 450)
    wins = pending.get("wins", 0) + 1 if consecutive else 1
    state["pending"] = {"name": best.name, "wins": wins}
    return Decision(best.name if wins >= required_wins else None,
                    "latency-improvement" if wins >= required_wins else "await-second-win")


def long_candidates(results, current, state, now, limit=2):
    history = state.get("nodes", {})

    def rank(result):
        old = history.get(result.name, {})
        recent_failure = ((old.get("stream_ok") is False
                           and 0 <= now - old.get("stream_at", 0) < 600)
                          or (old.get("bulk_ok") is False
                              and 0 <= now - old.get("bulk_at", 0) < 600))
        return recent_failure, score(result)

    return sorted((r for r in results if r.name != current and r.name not in FORBIDDEN and r.attempts >= 2
                   and len(r.latencies_ms) == r.attempts), key=rank)[:limit]


def log(event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=False, allow_nan=False), flush=True)


def load_yaml(path):
    # PyYAML is already supplied by the existing server management installation.
    import yaml
    try:
        return yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError):
        raise RuntimeError("Cannot read or parse runtime YAML") from None


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def curl_probe(proxy, url, deadline, timeout=5, stream=False, expected_bytes=12,
               minimum_seconds=4.5, details=None, minimum_bytes=None):
    remaining = min(timeout, deadline - time.monotonic())
    if remaining < 0.5:
        return False, 0.0
    args = ["curl", "--silent", "--show-error", "--http1.1", "--noproxy", "",
            "--proxy", proxy, "--connect-timeout", str(min(3, remaining)),
            "--max-time", str(remaining), "--output", "/dev/null",
            "--write-out", "%{http_code} %{time_total} %{size_download}"]
    if stream:
        # The origin must pace its response; throttling a local download is not equivalent.
        url += ("&" if "?" in url else "?") + "probe=" + str(time.time_ns())
        args += ["--header", "Cache-Control: no-cache"]
    try:
        result = subprocess.run(args + [url], capture_output=True, text=True,
                                timeout=remaining + 1)
        status, duration, size = result.stdout.split()
        seconds = float(duration)
        if details is not None:
            details.update({"http_status": status, "bytes": int(float(size)),
                            "curl_exit": result.returncode})
        if stream:
            size_ok = (int(float(size)) >= minimum_bytes if minimum_bytes is not None
                       else int(float(size)) == expected_bytes)
            ok = (result.returncode == 0 and status == "200" and size_ok
                  and seconds >= minimum_seconds)
        else:
            ok = result.returncode == 0 and status in {"200", "204", "404"}
        return ok, seconds
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        if details is not None:
            details["error_type"] = type(error).__name__
        return False, 0.0


def validate_bulk_settings(settings):
    if not settings.get("bulk_url"):
        if any(key in settings for key in ("bulk_bytes", "bulk_min_bytes", "bulk_timeout_seconds")):
            raise ValueError("Bulk probe settings require bulk_url")
        return
    url = urllib.parse.urlsplit(settings["bulk_url"])
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None):
        raise ValueError("Bulk probe requires a credential-free HTTPS URL")
    if ("bulk_bytes" in settings) == ("bulk_min_bytes" in settings):
        raise ValueError("Choose exactly one bulk response size mode")
    size = settings.get("bulk_bytes", settings.get("bulk_min_bytes"))
    timeout = settings.get("bulk_timeout_seconds", 10)
    if type(size) is not int or not 1 <= size <= 8 * 1024 * 1024:
        raise ValueError("Bulk response size must be between 1 byte and 8 MiB")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 1 <= timeout <= 15):
        raise ValueError("Bulk timeout must be between 1 and 15 seconds")


def probe_budgets(settings):
    short = 5 * len(settings.get("short_urls", [None, None]))
    full = short + 11 + (settings.get("bulk_timeout_seconds", 10)
                         if settings.get("bulk_url") else 0)
    workers = settings.get("workers", 3)
    isolated = full + 4
    switch = full + 12 + 8  # Full verification, rollback reserve, controller overhead.
    minimum = (full + math.ceil(2 / workers) * (short + 4)
               + math.ceil(3 / workers) * isolated + switch)
    return {"short": short + 4, "full": full, "isolated": isolated,
            "switch": switch, "minimum": minimum}


def validate_run_budget(settings):
    budgets = probe_budgets(settings)
    if settings.get("budget_seconds", 145) < budgets["minimum"]:
        log("budget-incompatible", budget_seconds=settings.get("budget_seconds", 145),
            minimum_seconds=budgets["minimum"], full_probe_seconds=budgets["full"],
            switch_reserve_seconds=budgets["switch"])
        raise ValueError("Run budget is too small for verified recovery; minimum_seconds="
                         + str(budgets["minimum"]))
    return budgets


def bulk_probe(proxy, settings, deadline, details):
    # Reuse the exact-length checker without the paced stream's minimum duration.
    result = curl_probe(proxy, settings["bulk_url"], deadline,
                        timeout=settings.get("bulk_timeout_seconds", 10), stream=True,
                        expected_bytes=settings.get("bulk_bytes"), minimum_seconds=0,
                        minimum_bytes=settings.get("bulk_min_bytes"), details=details)
    details["seconds"] = round(result[1], 3)
    return result


class Controller:
    def __init__(self, runtime, deadline=None):
        address = runtime.get("external-controller", "")
        host, _, port = address.rpartition(":")
        if host not in {"127.0.0.1", "localhost"} or not port.isdigit():
            raise ValueError("A loopback HTTP controller is required")
        self.base = "http://" + address
        self.headers = {"Authorization": "Bearer " + runtime["secret"],
                        "Content-Type": "application/json"}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.deadline = deadline

    def request(self, path, method="GET", data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body,
                                         headers=self.headers, method=method)
        timeout = 4 if self.deadline is None else min(4, self.deadline - time.monotonic())
        if timeout < 0.2:
            raise TimeoutError("Controller deadline exceeded")
        with self.opener.open(request, timeout=timeout) as response:
            payload = response.read()
        return json.loads(payload) if payload else None

    def group(self, name):
        return self.request("/proxies/" + urllib.parse.quote(name, safe=""))

    def select(self, group, name):
        self.request("/proxies/" + urllib.parse.quote(group, safe=""), "PUT", {"name": name})


def transport_ids(connections, group, node, exit_ip, exit_port):
    ids = []
    for connection in connections:
        meta = connection.get("metadata", {})
        chains = connection.get("chains", [])
        if (meta.get("type") == "Inner" and meta.get("network") == "udp"
                and meta.get("destinationIP") == exit_ip
                and str(meta.get("destinationPort")) == str(exit_port)
                and group in chains and node in chains and connection.get("id")):
            ids.append(connection["id"])
    return ids


def matching_transports(api, group, node, settings, exit_port):
    return transport_ids(api.request("/connections").get("connections") or [], group, node,
                         settings["exit_ip"], exit_port)


def reset_transports(api, group, node, settings, exit_port):
    ids = matching_transports(api, group, node, settings, exit_port)
    for tracker in ids:
        try:
            api.request("/connections/" + urllib.parse.quote(tracker, safe=""), "DELETE")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    return ids


def production_check(settings, deadline, with_stream=True, details=None):
    proxy = "http://127.0.0.1:" + str(settings.get("production_port", 7890))
    details = {} if details is None else details
    details["phase"] = "short"
    for index, url in enumerate(settings["short_urls"]):
        measurement = {}
        ok, seconds = curl_probe(proxy, url, deadline, details=measurement)
        measurement.update({"index": index, "seconds": round(seconds, 3)})
        details.setdefault("short", []).append(measurement)
        if not ok:
            return False
    if not with_stream:
        details["phase"] = "complete"
        return True
    details["phase"] = "stream"
    measurement = details.setdefault("stream", {})
    ok, seconds = curl_probe(proxy, settings["stream_url"], deadline, timeout=11, stream=True,
                             expected_bytes=settings.get("stream_bytes", 12),
                             minimum_seconds=settings.get("stream_min_seconds", 4.5),
                             details=measurement)
    measurement["seconds"] = round(seconds, 3)
    if not ok:
        return False
    if settings.get("bulk_url"):
        details["phase"] = "bulk"
        if not bulk_probe(proxy, settings, deadline, details.setdefault("bulk", {}))[0]:
            return False
    details["phase"] = "complete"
    return True


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o600)
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def transaction_path(settings):
    return Path(settings["state_dir"]) / "transaction.json" if settings.get("state_dir") else None


def clear_transaction(settings):
    path = transaction_path(settings)
    if path is not None:
        path.unlink(missing_ok=True)


def recover_transaction(api, settings, exit_port, deadline):
    path = transaction_path(settings)
    if path is None or not path.exists():
        return
    pending = json.loads(path.read_text())
    if (pending["group"] != settings["group"] or pending["exit_ip"] != settings["exit_ip"]
            or str(pending["exit_port"]) != str(exit_port)):
        raise RuntimeError("Pending transaction scope does not match settings")
    current = api.group(settings["group"])["now"]
    if current == pending["new"]:
        api.select(settings["group"], pending["old"])
        reset_transports(api, settings["group"], pending["new"], settings, exit_port)
        production_check(settings, deadline, with_stream=False)
        if api.group(settings["group"])["now"] != pending["old"]:
            raise RuntimeError("Pending transaction recovery not confirmed")
    elif current == pending["old"]:
        reset_transports(api, settings["group"], pending["new"], settings, exit_port)
    log("transaction-recovered", selected=api.group(settings["group"])["now"])
    clear_transaction(settings)


def switch_verified(api, settings, old, new, exit_port, deadline):
    group = settings["group"]
    if deadline - time.monotonic() < probe_budgets(settings)["switch"]:
        log("insufficient-switch-budget", action="skip")
        return False
    if api.group(group)["now"] != old:
        log("selection-changed-externally", action="skip")
        return False
    # Materialize the old transport if idle, so we can close the actual QUIC carrier.
    if not matching_transports(api, group, old, settings, exit_port):
        production_check(settings, deadline, with_stream=False)
    if not matching_transports(api, group, old, settings, exit_port):
        log("reset-scope-unavailable", action="skip")
        return False
    if api.group(group)["now"] != old:
        return False
    if deadline - time.monotonic() < probe_budgets(settings)["full"] + 12:
        log("insufficient-switch-budget", action="skip")
        return False
    path = transaction_path(settings)
    if path is not None:
        atomic_json(path, {"group": group, "old": old, "new": new,
                           "exit_ip": settings["exit_ip"], "exit_port": exit_port})
    try:
        api.select(group, new)
        if api.group(group)["now"] != new:
            raise RuntimeError("Selection confirmation failed")
        closed = reset_transports(api, group, old, settings, exit_port)
        for _ in range(2):
            if production_check(settings, deadline - 12):
                fresh = matching_transports(api, group, new, settings, exit_port)
                if fresh and not set(closed).intersection(fresh) and api.group(group)["now"] == new:
                    log("switched", old=old, new=new, closed_transports=len(closed),
                        verified_transport=True)
                    clear_transaction(settings)
                    return True
            if deadline - time.monotonic() < 30:
                break
            time.sleep(0.5)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError):
        log("switch-verification-error", action="rollback")
    # A lost PUT response does not mean the selection mutation did not happen.
    try:
        actual = api.group(group)["now"]
        if actual == new:
            api.select(group, old)
            reset_transports(api, group, new, settings, exit_port)
            restored = production_check(settings, deadline, with_stream=False)
            if api.group(group)["now"] != old:
                raise RuntimeError("Rollback not confirmed")
            log("rolled-back", restored=old, short_probe_ok=restored)
        clear_transaction(settings)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError):
        log("transaction-indeterminate", action="recover-next-run")
    return False


def isolated_probe(name, node, exit_proxy, runtime, settings, deadline, with_stream=False):
    result = ProbeResult(name, bulk_ok=False if settings.get("bulk_url") else None)
    if deadline - time.monotonic() < 5:
        return result
    with tempfile.TemporaryDirectory(prefix="probe-", dir=settings["probe_dir"]) as scratch:
        path = Path(scratch)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        first = copy.deepcopy(node)
        first.update({"name": "FIRST-HOP", "routing-mark": settings["routing_mark"],
                      "interface-name": settings["interface"]})
        upstream = copy.deepcopy(exit_proxy)
        upstream.update({"name": "EXIT", "server": settings["exit_ip"],
                         "dialer-proxy": "FIRST-HOP"})
        resolvers = runtime.get("dns", {}).get("proxy-server-nameserver") or ["223.5.5.5", "119.29.29.29"]
        config = {"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
                  "mode": "rule", "log-level": "warning", "ipv6": False,
                  "routing-mark": settings["routing_mark"], "interface-name": settings["interface"],
                  "tun": {"enable": False}, "proxies": [first, upstream], "rules": ["MATCH,EXIT"],
                  "dns": {"enable": True, "ipv6": False, "enhanced-mode": "redir-host",
                          "nameserver": resolvers, "proxy-server-nameserver": resolvers,
                          "default-nameserver": ["223.5.5.5", "119.29.29.29"]}}
        config_path = path / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False))
        config_path.chmod(0o600)
        process = None
        try:
            with (path / "core.log").open("w") as output:
                process = subprocess.Popen([settings["core_binary"], "-d", scratch, "-f", str(config_path)],
                                           stdout=output, stderr=output)
                ready_until = min(deadline, time.monotonic() + 4)
                while time.monotonic() < ready_until and process.poll() is None:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    return result
                proxy = "http://127.0.0.1:" + str(port)
                for url in settings["short_urls"]:
                    ok, seconds = curl_probe(proxy, url, deadline)
                    if ok:
                        result.latencies_ms.append(seconds * 1000)
                result.attempts = len(settings["short_urls"])
                if with_stream:
                    if len(result.latencies_ms) == result.attempts:
                        result.stream_ok, result.stream_seconds = curl_probe(
                            proxy, settings["stream_url"], deadline, timeout=11, stream=True,
                            expected_bytes=settings.get("stream_bytes", 12),
                            minimum_seconds=settings.get("stream_min_seconds", 4.5),
                            details=result.stream_diagnostic)
                    else:
                        result.stream_ok = False
                    if result.stream_ok and settings.get("bulk_url"):
                        result.bulk_ok, result.bulk_seconds = bulk_probe(
                            proxy, settings, deadline, result.bulk_diagnostic)
                if with_stream:
                    result.verified_at = time.monotonic()
                return result
        finally:
            stop_process(process)


def record_results(state, results, now):
    history = state.setdefault("nodes", {})
    for result in results:
        entry = history.setdefault(result.name, {})
        value = score(result)
        if math.isfinite(value):
            entry["score_ms"] = round(value * 0.35 + entry.get("score_ms", value) * 0.65, 1)
        else:
            entry["score_ms"] = 10000
        entry.update({"updated_at": now, "successes": len(result.latencies_ms),
                      "attempts": result.attempts})
        if result.stream_ok is not None:
            entry.update({"stream_ok": result.stream_ok, "stream_at": now})
        if result.bulk_ok is not None and result.stream_ok is not None:
            entry.update({"bulk_ok": result.bulk_ok, "bulk_at": now})
        log("probe", node=result.name, score_ms=round(value, 1) if math.isfinite(value) else None,
            successes=len(result.latencies_ms), attempts=result.attempts,
            stream_ok=result.stream_ok, stream_seconds=round(result.stream_seconds, 2),
            stream_diagnostic=result.stream_diagnostic, bulk_ok=result.bulk_ok,
            bulk_seconds=round(result.bulk_seconds, 2), bulk_diagnostic=result.bulk_diagnostic,
            production_ok=result.production_ok)


def run(settings, state, apply=False):
    budgets = validate_run_budget(settings)
    active = subprocess.run(["systemctl", "is-active", "--quiet", settings["production_service"]], timeout=3)
    if active.returncode:
        log("production-inactive", action="skip")
        return
    deadline = time.monotonic() + settings.get("budget_seconds", 145)
    # Scanning must leave time for candidate confirmation and a bounded commit/rollback.
    scan_deadline = deadline - budgets["switch"]
    runtime = load_yaml(Path(settings["runtime_dir"]) / "config.yaml")
    api = Controller(runtime, deadline)
    exit_proxy = next(p for p in runtime["proxies"] if p["name"] == settings["exit_proxy"])
    if exit_proxy.get("type") != "hysteria2" or exit_proxy.get("dialer-proxy") != settings["group"]:
        raise ValueError("Unexpected production exit topology")
    if apply:
        recover_transaction(api, settings, exit_proxy["port"], deadline)
    group = api.group(settings["group"])
    current = group["now"]
    if current in FORBIDDEN:
        log("selection-disabled", node=current, action="skip")
        return
    provider = load_yaml(Path(settings["runtime_dir"]) / settings["provider_path"])
    nodes = {p["name"]: p for p in provider["proxies"]
             if p.get("udp") is not False and not p.get("dialer-proxy")
             and p["name"] in group["all"] and p["name"] not in FORBIDDEN}
    if current not in nodes:
        log("current-not-in-provider", action="skip")
        return
    # Confirmation of a failed production probe overlaps finalist verification below.
    production_diagnostic = {}
    production_ok = production_check(settings, scan_deadline, details=production_diagnostic)
    log("production-health", ok=production_ok, confirmed=production_ok,
        diagnostic=production_diagnostic)
    # Interleave protocols so a large SS section cannot postpone all VMess candidates.
    import itertools
    protocols = {}
    for name, node in nodes.items():
        protocols.setdefault(node["type"], []).append(name)
    names = [n for row in itertools.zip_longest(*protocols.values()) for n in row if n is not None]
    # Keep enough scan time for full finalists; reduce exploration before verification.
    batches = int(max(0, scan_deadline - time.monotonic() - budgets["isolated"])
                  // budgets["short"])
    candidate_limit = min(settings.get("max_candidates", 8),
                          batches * settings.get("workers", 3))
    if candidate_limit < 2:
        log("scan-budget-insufficient", action="skip", production_ok=production_ok)
        state["last_run_at"] = time.time()
        return
    selected = choose_candidates(names, current, state, candidate_limit)

    def probe(name, stream=False, probe_deadline=None):
        return isolated_probe(name, nodes[name], exit_proxy, runtime, settings,
                              scan_deadline if probe_deadline is None else probe_deadline, stream)

    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.get("workers", 3)) as pool:
        results = list(pool.map(probe, selected))
        by_name = {r.name: r for r in results}
        alternatives = long_candidates(results, current, state, time.time(),
                                       limit=1 if not production_ok else 2)
        finalists = [current] + [r.name for r in alternatives]
        production_retry = None
        retry_diagnostic = {}
        if not production_ok:
            production_retry = pool.submit(production_check, settings, scan_deadline,
                                           details=retry_diagnostic)
        verified = list(pool.map(lambda name: probe(name, True), finalists))
        by_name.update((r.name, r) for r in verified)
        if production_retry is not None:
            production_ok = production_retry.result()
            log("production-health", ok=production_ok, confirmed=True,
                diagnostic=retry_diagnostic)
        if (not any(qualified(r) for r in verified if r.name != current)
                and scan_deadline - time.monotonic() >= budgets["isolated"]):
            remaining = [r for r in results if r.name not in finalists]
            extra = long_candidates(remaining, current, state, time.time())
            by_name.update((r.name, r) for r in pool.map(lambda name: probe(name, True),
                                                       [r.name for r in extra]))
    by_name[current].production_ok = production_ok
    results = list(by_name.values())
    now = time.time()
    decision = decide_switch(current, results, state, now)
    record_results(state, results, now)
    log("decision", current=current, target=decision.target, reason=decision.reason, apply=apply)
    if decision.target and apply:
        confirmation = by_name[decision.target]
        # Initial short probes plus a recent full finalist are repeated measurements.
        # Repeat the full check only when it fits without consuming switch/rollback time.
        if deadline - time.monotonic() >= budgets["isolated"] + budgets["switch"]:
            confirmation = probe(decision.target, True, deadline - budgets["switch"])
        age = time.monotonic() - confirmation.verified_at
        fresh = 0 <= age <= 45
        log("candidate-confirmation", target=decision.target, fresh=fresh,
            age_seconds=round(age, 2))
        if qualified(confirmation) and fresh and switch_verified(
                api, settings, current, decision.target, exit_proxy["port"], deadline):
            state["last_switch_at"] = time.time()
            state.pop("pending", None)
        else:
            state.pop("pending", None)
            log("switch-not-committed", target=decision.target)
    state["last_run_at"] = now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Private JSON settings path")
    parser.add_argument("--apply", action="store_true", help="Commit verified selections (default: report only)")
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text())
    ipaddress.ip_address(settings["exit_ip"])
    validate_bulk_settings(settings)
    if not 1 <= settings.get("workers", 3) <= 3 or not 1 <= settings.get("max_candidates", 8) <= 12:
        raise ValueError("Probe concurrency/budget out of bounds")
    if not 60 <= settings.get("budget_seconds", 145) <= 145:
        raise ValueError("Run budget must be between 60 and 145 seconds")
    if len(settings["short_urls"]) < 2:
        raise ValueError("At least two independent HTTPS probes are required")
    validate_run_budget(settings)
    urls = settings["short_urls"] + [settings["stream_url"]]
    if any(urllib.parse.urlsplit(u).scheme != "https" or urllib.parse.urlsplit(u).username for u in urls):
        raise ValueError("Only credential-free HTTPS probe URLs are supported")
    directory = Path(settings["state_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    settings["probe_dir"] = str(directory)
    with (directory / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("already-running", action="skip")
            return
        state_path = directory / "state.json"
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
        except (ValueError, OSError):
            log("invalid-state", action="start-fresh")
            state = {}
        run(settings, state, apply=args.apply)
        atomic_json(state_path, state)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except Exception as error:
        # Config/parser exceptions can include credential-bearing source lines.
        log("health-check-error", error_type=type(error).__name__)
        raise SystemExit(1)

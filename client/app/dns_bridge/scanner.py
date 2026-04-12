from __future__ import annotations

import base64
import os
import random
import socket
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

import dns.exception
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype

from .core import (
    DnsResolverTarget,
    ProgressCallback,
    _effective_query_size,
    _emit_progress,
    _parse_resolver_target,
    _record_resolver_result,
    load_dns_domains,
    load_dns_resolvers,
    probe_dns_domain,
    record_event,
    set_setting,
)

_SCANNER_PUBLIC_POOL = (
    "1.1.1.1",
    "1.0.0.1",
    "8.8.8.8",
    "8.8.4.4",
    "9.9.9.9",
    "149.112.112.112",
    "208.67.222.222",
    "208.67.220.220",
    "94.140.14.14",
    "94.140.15.15",
    "76.76.2.0",
    "76.76.10.0",
    "64.6.64.6",
    "64.6.65.6",
    "185.228.168.9",
    "185.228.169.9",
    "198.101.242.72",
    "23.253.163.53",
)


class ScanAborted(Exception):
    """Raised when scanner receives a stop request."""


class ResolverScanController:
    def __init__(self) -> None:
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self, poll_seconds: float = 0.2) -> None:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(max(0.05, poll_seconds))

    def checkpoint(self) -> None:
        self.wait_if_paused()
        if self._stop_event.is_set():
            raise ScanAborted("scan_stopped_by_user")


@dataclass
class ResolverTunnelChecks:
    ns_to_a: bool = False
    txt: bool = False
    random_sub: bool = False
    tunnel_realism: bool = False
    edns: bool = False
    edns_payload: int = 0
    nxdomain: bool = False

    def score(self) -> int:
        return int(self.ns_to_a) + int(self.txt) + int(self.random_sub) + int(self.tunnel_realism) + int(self.edns) + int(self.nxdomain)

    def details(self) -> str:
        def mark(ok: bool, label: str) -> str:
            return f"{label}{'+' if ok else '-'}"

        edns = mark(self.edns, "EDNS")
        if self.edns and self.edns_payload > 0:
            edns = f"{edns}({self.edns_payload})"
        return " ".join(
            [
                mark(self.ns_to_a, "NS->A"),
                mark(self.txt, "TXT"),
                mark(self.random_sub, "RND"),
                mark(self.tunnel_realism, "DPI"),
                edns,
                mark(self.nxdomain, "NXD"),
            ]
        )


def _scan_random_label(length: int = 8) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    n = max(3, min(30, int(length or 8)))
    return "".join(random.choice(alphabet) for _ in range(n))


def _scan_parent_domain(domain: str) -> str:
    normalized = (domain or "").strip().rstrip(".").lower()
    if not normalized:
        return "example.com"
    parts = normalized.split(".", 1)
    if len(parts) >= 2 and "." in parts[1]:
        return parts[1]
    return normalized


def _scan_timeout_seconds(timeout_ms: int) -> float:
    return max(0.25, min(8.0, float(timeout_ms) / 1000.0))


def _scan_query_udp(
    server: str,
    port: int,
    name: str,
    rdtype: str,
    timeout_seconds: float,
    edns_payload: int = 0,
) -> dns.message.Message:
    use_edns = edns_payload > 0
    if use_edns:
        query = dns.message.make_query(
            qname=name,
            rdtype=rdtype,
            use_edns=True,
            payload=max(512, int(edns_payload or 1232)),
        )
    else:
        query = dns.message.make_query(qname=name, rdtype=rdtype)
    return dns.query.udp(
        q=query,
        where=server,
        port=max(1, min(65535, int(port or 53))),
        timeout=timeout_seconds,
        ignore_unexpected=True,
    )


def _scan_query(target: DnsResolverTarget, name: str, rdtype: str, timeout_seconds: float, edns_payload: int = 0) -> dns.message.Message:
    if target.use_system or not target.server:
        raise RuntimeError("scanner_system_resolver_not_supported")
    return _scan_query_udp(target.server, target.port, name, rdtype, timeout_seconds, edns_payload=edns_payload)


def _scan_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout, dns.exception.Timeout))


def _scan_response_has_opt(response: dns.message.Message) -> bool:
    for rrset in response.additional:
        if int(getattr(rrset, "rdtype", 0) or 0) == int(dns.rdatatype.OPT):
            return True
    return False


def _scan_test_ns_to_a(target: DnsResolverTarget, parent_domain: str, timeout_seconds: float) -> bool:
    try:
        response = _scan_query(target, parent_domain, "NS", timeout_seconds)
        ns_host = ""
        for rrset in response.answer:
            if int(getattr(rrset, "rdtype", 0) or 0) != int(dns.rdatatype.NS):
                continue
            for record in rrset:
                candidate = str(getattr(record, "target", "") or "").strip().rstrip(".")
                if candidate:
                    ns_host = candidate
                    break
            if ns_host:
                break
        if not ns_host:
            return False
        _scan_query(target, ns_host, "A", timeout_seconds)
        return True
    except Exception:
        return False


def _scan_test_txt(target: DnsResolverTarget, parent_domain: str, timeout_seconds: float) -> bool:
    query_name = f"{_scan_random_label(8)}.{parent_domain}"
    try:
        _scan_query(target, query_name, "TXT", timeout_seconds)
        return True
    except Exception:
        return False


def _scan_test_random_subdomain(target: DnsResolverTarget, domain: str, timeout_seconds: float) -> bool:
    for _ in range(2):
        query_name = f"{_scan_random_label(8)}.{_scan_random_label(8)}.{domain}"
        try:
            _scan_query(target, query_name, "A", timeout_seconds)
            return True
        except Exception:
            continue
    return False


def _scan_test_tunnel_realism(target: DnsResolverTarget, domain: str, timeout_seconds: float, query_size: int) -> bool:
    qsize = max(64, min(220, int(query_size or 220)))
    raw_len = max(8, min(100, int(qsize * 0.55)))
    payload = base64.b32encode(os.urandom(raw_len)).decode("ascii").rstrip("=").lower()
    labels: list[str] = []
    while payload:
        labels.append(payload[:57])
        payload = payload[57:]
    labels = labels[:10]
    query_name = ".".join(labels + [domain])
    if len(query_name) > 250:
        query_name = ".".join(labels[:4] + [domain])
    try:
        _scan_query(target, query_name, "TXT", timeout_seconds)
        return True
    except Exception:
        return False


def _scan_test_edns(target: DnsResolverTarget, parent_domain: str, timeout_seconds: float) -> tuple[bool, int]:
    max_payload = 0
    any_ok = False
    for payload in (512, 900, 1232):
        query_name = f"{_scan_random_label(8)}.{parent_domain}"
        try:
            response = _scan_query(target, query_name, "A", timeout_seconds, edns_payload=payload)
        except Exception:
            break
        if int(response.rcode()) == int(dns.rcode.FORMERR):
            break
        if _scan_response_has_opt(response):
            any_ok = True
            max_payload = payload
        else:
            break
    return any_ok, max_payload


def _scan_test_nxdomain(target: DnsResolverTarget, timeout_seconds: float) -> bool:
    good = 0
    for _ in range(3):
        query_name = f"{_scan_random_label(12)}.invalid"
        try:
            response = _scan_query(target, query_name, "A", timeout_seconds)
        except Exception:
            continue
        if int(response.rcode()) == int(dns.rcode.NXDOMAIN):
            good += 1
    return good >= 2


def _scan_resolver_checks(
    target: DnsResolverTarget,
    domain: str,
    timeout_ms: int,
    query_size: int,
    control: ResolverScanController | None = None,
) -> ResolverTunnelChecks:
    if control:
        control.checkpoint()
    timeout_seconds = _scan_timeout_seconds(timeout_ms)
    parent_domain = _scan_parent_domain(domain)
    checks = ResolverTunnelChecks()
    checks.ns_to_a = _scan_test_ns_to_a(target, parent_domain, timeout_seconds)
    if control:
        control.checkpoint()
    checks.txt = _scan_test_txt(target, parent_domain, timeout_seconds)
    if control:
        control.checkpoint()
    checks.random_sub = _scan_test_random_subdomain(target, domain, timeout_seconds)
    if control:
        control.checkpoint()
    checks.tunnel_realism = _scan_test_tunnel_realism(target, domain, timeout_seconds, query_size)
    if control:
        control.checkpoint()
    checks.edns, checks.edns_payload = _scan_test_edns(target, parent_domain, timeout_seconds)
    if control:
        control.checkpoint()
    checks.nxdomain = _scan_test_nxdomain(target, timeout_seconds)
    return checks


def _resolver_display_key(target: DnsResolverTarget) -> str:
    if target.use_system or not target.server:
        return "system"
    if int(target.port or 53) == 53:
        return target.server
    return f"{target.server}:{target.port}"


def _scan_one_resolver(
    target: DnsResolverTarget,
    domain: str,
    timeout_ms: int,
    query_size: int,
    control: ResolverScanController | None = None,
) -> dict[str, object]:
    if control:
        control.checkpoint()
    started = time.perf_counter()
    parent_domain = _scan_parent_domain(domain)
    probe_name = f"{_scan_random_label(8)}.{parent_domain}"
    probe_timeout = _scan_timeout_seconds(min(max(300, int(timeout_ms or 1800)), 1500))

    try:
        _scan_query(target, probe_name, "A", probe_timeout)
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        _record_resolver_result(target, True, float(latency_ms) / 1000.0)
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        _record_resolver_result(target, False, float(latency_ms) / 1000.0)
        status = "timeout" if _scan_timeout_error(exc) else "error"
        return {
            "resolver": _resolver_display_key(target),
            "status": status,
            "latency_ms": latency_ms,
            "error": str(exc),
            "score": 0,
            "details": "",
            "tests": {},
        }

    if control:
        control.checkpoint()
    checks = _scan_resolver_checks(target, domain, timeout_ms, query_size, control=control)
    return {
        "resolver": _resolver_display_key(target),
        "status": "working",
        "latency_ms": latency_ms,
        "error": "",
        "score": checks.score(),
        "details": checks.details(),
        "tests": {
            "ns_to_a": checks.ns_to_a,
            "txt": checks.txt,
            "random_sub": checks.random_sub,
            "tunnel_realism": checks.tunnel_realism,
            "edns": checks.edns,
            "edns_payload": checks.edns_payload,
            "nxdomain": checks.nxdomain,
        },
    }


def _scan_detect_transparent_proxy(domain: str, timeout_ms: int) -> bool:
    timeout_seconds = _scan_timeout_seconds(min(max(500, int(timeout_ms or 1800)), 2000))
    detected = False
    for host in ("192.0.2.1", "198.51.100.1", "203.0.113.1"):
        try:
            _scan_query_udp(
                server=host,
                port=53,
                name=f"{_scan_random_label(8)}.{domain}",
                rdtype="A",
                timeout_seconds=timeout_seconds,
            )
            detected = True
            break
        except Exception:
            continue
    return detected


def _scan_e2e_probe_target(
    target: DnsResolverTarget,
    domain: str,
    password: str,
    control: ResolverScanController | None = None,
) -> dict[str, object]:
    if control:
        control.checkpoint()
    started = time.perf_counter()
    try:
        probe = probe_dns_domain(
            domain=domain,
            password=password,
            resolvers=[target],
            strict_resolvers=True,
            bypass_session_cache=True,
        )
        elapsed_ms = max(1, int(probe.get("elapsed_ms", 0) or ((time.perf_counter() - started) * 1000)))
        ok = bool(probe.get("ok"))
        _record_resolver_result(target, ok, float(elapsed_ms) / 1000.0)
        return {
            "resolver": _resolver_display_key(target),
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "error": str(probe.get("error") or ""),
            "meta": probe,
        }
    except Exception as exc:
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
        _record_resolver_result(target, False, float(elapsed_ms) / 1000.0)
        return {
            "resolver": _resolver_display_key(target),
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "meta": {},
        }


def _resolver_key_from_display(token: str) -> str:
    raw = (token or "").strip()
    if not raw:
        return ""
    if raw == "system":
        return "system"
    parsed = _parse_resolver_target(raw)
    if parsed:
        return parsed.key
    return raw


def _scan_candidate_resolvers(
    resolvers: list[DnsResolverTarget] | None,
    include_public_pool: bool,
) -> list[DnsResolverTarget]:
    dedup: dict[str, DnsResolverTarget] = {}
    source_targets = load_dns_resolvers() if resolvers is None else list(resolvers)
    for target in source_targets:
        if target.use_system:
            continue
        if not target.server:
            continue
        dedup[target.key] = target

    if include_public_pool:
        for line in _SCANNER_PUBLIC_POOL:
            parsed = _parse_resolver_target(line)
            if parsed:
                dedup[parsed.key] = parsed

    if not dedup:
        for line in _SCANNER_PUBLIC_POOL:
            parsed = _parse_resolver_target(line)
            if parsed:
                dedup[parsed.key] = parsed
    return list(dedup.values())


def scan_dns_resolvers(
    *,
    domain: str = "",
    password: str = "",
    resolvers: list[DnsResolverTarget] | None = None,
    include_public_pool: bool = False,
    timeout_ms: int = 1800,
    concurrency: int = 96,
    query_size: int | None = None,
    e2e_enabled: bool = True,
    e2e_threshold: int = 4,
    e2e_max_candidates: int = 48,
    e2e_concurrency: int = 8,
    auto_apply_best: bool = False,
    control: ResolverScanController | None = None,
    progress: ProgressCallback = None,
) -> dict[str, object]:
    requested_domain = (domain or "").strip().rstrip(".").lower()
    scan_domain = requested_domain
    domain_targets = load_dns_domains()
    if not scan_domain:
        scan_domain = domain_targets[0].domain if domain_targets else "example.com"
    scan_password = (password or "").strip()
    if not scan_password and domain_targets:
        for item in domain_targets:
            if item.domain == scan_domain and item.password:
                scan_password = item.password
                break

    timeout_ms = max(300, min(6000, int(timeout_ms or 1800)))
    concurrency = max(4, min(256, int(concurrency or 96)))
    qsize = int(query_size or _effective_query_size())
    e2e_threshold = max(1, min(6, int(e2e_threshold or 4)))
    e2e_max_candidates = max(1, min(300, int(e2e_max_candidates or 48)))
    e2e_concurrency = max(1, min(32, int(e2e_concurrency or 8)))

    targets = _scan_candidate_resolvers(resolvers, include_public_pool=include_public_pool)
    key_to_target = {target.key: target for target in targets}
    total = len(targets)
    started = time.time()
    stopped = False

    _emit_progress(
        progress,
        kind="scan_start",
        domain=scan_domain,
        total=total,
        timeout_ms=timeout_ms,
        concurrency=concurrency,
        include_public_pool=include_public_pool,
    )

    if control:
        control.checkpoint()
    transparent_proxy = _scan_detect_transparent_proxy(scan_domain, timeout_ms)
    _emit_progress(progress, kind="transparent_proxy", detected=transparent_proxy)

    scanned = 0
    working = 0
    timeout_count = 0
    error_count = 0
    items: list[dict[str, object]] = []

    max_workers = min(concurrency, max(1, total))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending: dict[object, DnsResolverTarget] = {}
        index = 0

        def _submit_more() -> None:
            nonlocal index
            while index < total and len(pending) < max_workers:
                if control and control.is_stopped():
                    return
                target = targets[index]
                index += 1
                future = executor.submit(_scan_one_resolver, target, scan_domain, timeout_ms, qsize, control)
                pending[future] = target

        _submit_more()
        while pending:
            if control:
                control.wait_if_paused()
                if control.is_stopped():
                    stopped = True
                    for future in pending:
                        future.cancel()
                    break

            done, _ = wait(set(pending.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
            if not done:
                continue

            for future in done:
                target = pending.pop(future)
                if future.cancelled():
                    continue
                scanned += 1
                try:
                    item = future.result()
                except ScanAborted:
                    stopped = True
                    item = {
                        "resolver": _resolver_display_key(target),
                        "status": "stopped",
                        "latency_ms": 0,
                        "error": "scan_stopped_by_user",
                        "score": 0,
                        "details": "",
                        "tests": {},
                    }
                except Exception as exc:
                    item = {
                        "resolver": _resolver_display_key(target),
                        "status": "error",
                        "latency_ms": 0,
                        "error": str(exc),
                        "score": 0,
                        "details": "",
                        "tests": {},
                    }
                items.append(item)
                status = str(item.get("status") or "")
                if status == "working":
                    working += 1
                elif status == "timeout":
                    timeout_count += 1
                elif status in {"stopped", "cancelled"}:
                    pass
                else:
                    error_count += 1
                if scanned % 8 == 0 or scanned == total or stopped:
                    _emit_progress(
                        progress,
                        kind="scan_progress",
                        scanned=scanned,
                        total=total,
                        working=working,
                        timeout=timeout_count,
                        error=error_count,
                    )
                if stopped:
                    break

            if stopped:
                for future in pending:
                    future.cancel()
                break
            _submit_more()

    compatible = [x for x in items if int(x.get("score", 0) or 0) > 0]
    compatible.sort(key=lambda x: (-int(x.get("score", 0) or 0), int(x.get("latency_ms", 0) or 0)))

    e2e_domain = ""
    if requested_domain and requested_domain != "example.com":
        e2e_domain = requested_domain
    elif domain_targets:
        e2e_domain = domain_targets[0].domain
    e2e_results: list[dict[str, object]] = []
    e2e_candidates = [x for x in compatible if int(x.get("score", 0) or 0) >= e2e_threshold][:e2e_max_candidates]

    if e2e_enabled and not stopped and e2e_domain and e2e_candidates:
        _emit_progress(progress, kind="e2e_start", total=len(e2e_candidates), domain=e2e_domain)
        tested = 0
        passed = 0
        with ThreadPoolExecutor(max_workers=min(e2e_concurrency, len(e2e_candidates))) as executor:
            pending_e2e: dict[object, str] = {}
            candidate_index = 0
            candidate_total = len(e2e_candidates)

            def _submit_more_e2e() -> None:
                nonlocal candidate_index
                while candidate_index < candidate_total and len(pending_e2e) < min(e2e_concurrency, candidate_total):
                    if control and control.is_stopped():
                        return
                    item = e2e_candidates[candidate_index]
                    candidate_index += 1
                    display = str(item.get("resolver") or "").strip()
                    key = _resolver_key_from_display(display)
                    target = key_to_target.get(key)
                    if not target:
                        continue
                    _emit_progress(progress, kind="e2e_testing", resolver=display)
                    future = executor.submit(_scan_e2e_probe_target, target, e2e_domain, scan_password, control)
                    pending_e2e[future] = display

            _submit_more_e2e()
            while pending_e2e:
                if control:
                    control.wait_if_paused()
                    if control.is_stopped():
                        stopped = True
                        for future in pending_e2e:
                            future.cancel()
                        break

                done, _ = wait(set(pending_e2e.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    display = pending_e2e.pop(future)
                    if future.cancelled():
                        continue
                    tested += 1
                    try:
                        probe = future.result()
                    except ScanAborted:
                        stopped = True
                        probe = {
                            "resolver": display,
                            "ok": False,
                            "elapsed_ms": 0,
                            "error": "scan_stopped_by_user",
                            "meta": {},
                        }
                    except Exception as exc:
                        probe = {
                            "resolver": display,
                            "ok": False,
                            "elapsed_ms": 0,
                            "error": str(exc),
                            "meta": {},
                        }
                    if bool(probe.get("ok")):
                        passed += 1
                    e2e_results.append(probe)
                    _emit_progress(
                        progress,
                        kind="e2e_progress",
                        tested=tested,
                        total=candidate_total,
                        passed=passed,
                        resolver=str(probe.get("resolver") or ""),
                        ok=bool(probe.get("ok")),
                    )
                    if stopped:
                        break
                if stopped:
                    break
                _submit_more_e2e()

        e2e_results.sort(key=lambda x: (not bool(x.get("ok")), int(x.get("elapsed_ms", 0) or 0)))

    selected_resolver = ""
    auto_applied = False
    applied_resolvers: list[str] = []
    if auto_apply_best and not stopped:
        passed_items = [x for x in e2e_results if bool(x.get("ok"))]
        if passed_items:
            selected_resolver = str(passed_items[0].get("resolver") or "")
            ordered = [str(x.get("resolver") or "").strip() for x in passed_items]
            selected_set = set(ordered)
            for item in compatible:
                resolver_text = str(item.get("resolver") or "").strip()
                if resolver_text and resolver_text not in selected_set:
                    ordered.append(resolver_text)
            applied_resolvers = [x for x in ordered if x][:8]
            if applied_resolvers:
                set_setting("dns_resolvers", "\n".join(applied_resolvers))
                set_setting("dns_use_system_resolver", "0")
                auto_applied = True

    result: dict[str, object] = {
        "ok": True,
        "mode": "scan",
        "domain": scan_domain,
        "transparent_proxy_detected": transparent_proxy,
        "total": total,
        "scanned": scanned,
        "working": working,
        "timeout": timeout_count,
        "error": error_count,
        "stopped": stopped,
        "elapsed_ms": int((time.time() - started) * 1000),
        "compatible": compatible,
        "e2e": {
            "enabled": bool(e2e_enabled and e2e_domain and not stopped),
            "domain": e2e_domain,
            "threshold": e2e_threshold,
            "tested": len(e2e_results),
            "passed": len([x for x in e2e_results if bool(x.get("ok"))]),
            "results": e2e_results,
        },
        "selected_resolver": selected_resolver,
        "auto_applied": auto_applied,
        "applied_resolvers": applied_resolvers,
        "credit": "Inspired by the SlipNet DNS Scanner design.",
    }
    record_event(
        "dns_resolver_scan",
        total=total,
        scanned=scanned,
        working=working,
        timeout=timeout_count,
        error=error_count,
        stopped=stopped,
        e2e_tested=int(result["e2e"]["tested"]),
        e2e_passed=int(result["e2e"]["passed"]),
        auto_applied=auto_applied,
    )
    _emit_progress(progress, kind="scan_done", result=result)
    return result


def run_e2e_resolver_tests(
    *,
    domain: str = "",
    password: str = "",
    resolvers: list[DnsResolverTarget] | None = None,
    concurrency: int = 8,
    control: ResolverScanController | None = None,
    progress: ProgressCallback = None,
) -> dict[str, object]:
    requested_domain = (domain or "").strip().rstrip(".").lower()
    domain_targets = load_dns_domains()
    scan_domain = requested_domain or (domain_targets[0].domain if domain_targets else "example.com")
    scan_password = (password or "").strip()
    if not scan_password and domain_targets:
        for item in domain_targets:
            if item.domain == scan_domain and item.password:
                scan_password = item.password
                break

    targets = _scan_candidate_resolvers(resolvers, include_public_pool=False)
    targets = [target for target in targets if not target.use_system and target.server]
    total = len(targets)
    concurrency = max(1, min(32, int(concurrency or 8)))
    started = time.time()
    stopped = False
    tested = 0
    passed = 0
    results: list[dict[str, object]] = []

    _emit_progress(progress, kind="e2e_start", total=total, domain=scan_domain)

    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, total))) as executor:
        pending: dict[object, str] = {}
        index = 0

        def _submit_more() -> None:
            nonlocal index
            while index < total and len(pending) < concurrency:
                if control and control.is_stopped():
                    return
                target = targets[index]
                index += 1
                display = _resolver_display_key(target)
                _emit_progress(progress, kind="e2e_testing", resolver=display)
                future = executor.submit(_scan_e2e_probe_target, target, scan_domain, scan_password, control)
                pending[future] = display

        _submit_more()
        while pending:
            if control:
                control.wait_if_paused()
                if control.is_stopped():
                    stopped = True
                    for future in pending:
                        future.cancel()
                    break

            done, _ = wait(set(pending.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                display = pending.pop(future)
                if future.cancelled():
                    continue
                tested += 1
                try:
                    probe = future.result()
                except ScanAborted:
                    stopped = True
                    probe = {
                        "resolver": display,
                        "ok": False,
                        "elapsed_ms": 0,
                        "error": "scan_stopped_by_user",
                        "meta": {},
                    }
                except Exception as exc:
                    probe = {
                        "resolver": display,
                        "ok": False,
                        "elapsed_ms": 0,
                        "error": str(exc),
                        "meta": {},
                    }
                if bool(probe.get("ok")):
                    passed += 1
                results.append(probe)
                _emit_progress(
                    progress,
                    kind="e2e_progress",
                    tested=tested,
                    total=total,
                    passed=passed,
                    resolver=str(probe.get("resolver") or ""),
                    ok=bool(probe.get("ok")),
                )
                if stopped:
                    break
            if stopped:
                break
            _submit_more()

    results.sort(key=lambda x: (not bool(x.get("ok")), int(x.get("elapsed_ms", 0) or 0)))
    passed_items = [x for x in results if bool(x.get("ok"))]
    selected_resolver = str(passed_items[0].get("resolver") or "") if passed_items else ""
    result = {
        "ok": True,
        "mode": "e2e",
        "domain": scan_domain,
        "total": total,
        "tested": tested,
        "passed": passed,
        "stopped": stopped,
        "elapsed_ms": int((time.time() - started) * 1000),
        "results": results,
        "selected_resolver": selected_resolver,
        "credit": "Inspired by the SlipNet DNS Scanner design.",
    }
    _emit_progress(progress, kind="scan_done", result=result)
    return result




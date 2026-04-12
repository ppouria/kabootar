import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from PIL import Image
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.background_sync import background_sync_status, start_background_sync_loop
from app.config import settings
from app.db import SessionLocal, ensure_schema
from app.db.models import Channel, Message
from app.dns_bridge import (
    ResolverScanController,
    export_resolver_health,
    parse_dns_domains_text,
    parse_dns_resolvers_text,
    probe_dns_domain,
    push_channels_to_domains,
    run_e2e_resolver_tests,
    scan_dns_resolvers,
    sync_from_dns_domain,
)
from app.runtime_debug import record_event, runtime_summary, setup_logging, snapshot_events, tail_log_lines
from app.service import sync_once
from app.settings_store import all_settings, apply_sync_cron, get_setting, set_setting, set_settings_bulk
from app.utils import normalize_tg_s_url, parse_csv
from app.versioning import app_meta

logger = logging.getLogger("kabootar.web")
_SYNC_JOBS_LOCK = threading.Lock()
_SYNC_JOBS: dict[str, dict] = {}
_SYNC_JOB_TTL_SECONDS = 1800
_RESOLVER_SCAN_JOBS_LOCK = threading.Lock()
_RESOLVER_SCAN_JOBS: dict[str, dict] = {}
_RESOLVER_SCAN_CONTROLS: dict[str, ResolverScanController] = {}
_RESOLVER_SCAN_JOB_TTL_SECONDS = 1800
_RESOLVER_SCAN_LAST_JOB_KEY = "dns_resolver_last_scan_job"
_APP_AUTH_COOKIE = "kabootar_app_auth"


def _cleanup_sync_jobs_locked(now_ts: float | None = None) -> None:
    now_ts = now_ts if now_ts is not None else time.time()
    stale: list[str] = []
    for job_id, job in _SYNC_JOBS.items():
        finished_at = float(job.get("finished_at") or 0.0)
        if finished_at and now_ts - finished_at > _SYNC_JOB_TTL_SECONDS:
            stale.append(job_id)
    for job_id in stale:
        _SYNC_JOBS.pop(job_id, None)


def _new_sync_job() -> dict:
    now_ts = time.time()
    return {
        "id": secrets.token_hex(8),
        "status": "queued",
        "ok": None,
        "mode": "",
        "phase": "queued",
        "message": "Queued",
        "started_at": now_ts,
        "finished_at": 0.0,
        "elapsed_seconds": 0,
        "eta_seconds": None,
        "progress_percent": 0.0,
        "domains_total": 0,
        "domains_done": 0,
        "channels_total": 0,
        "channels_done": 0,
        "messages_total": 0,
        "messages_done": 0,
        "saved": 0,
        "server_refresh_requested": False,
        "server_refresh_done": False,
        "current_domain": "",
        "current_channel": "",
        "result": None,
        "error": "",
        "_domains": {},
        "_channels": {},
    }


def _cleanup_resolver_scan_jobs_locked(now_ts: float | None = None) -> None:
    now_ts = now_ts if now_ts is not None else time.time()
    stale: list[str] = []
    for job_id, job in _RESOLVER_SCAN_JOBS.items():
        finished_at = float(job.get("finished_at") or 0.0)
        if finished_at and now_ts - finished_at > _RESOLVER_SCAN_JOB_TTL_SECONDS:
            stale.append(job_id)
    for job_id in stale:
        _RESOLVER_SCAN_JOBS.pop(job_id, None)
        _RESOLVER_SCAN_CONTROLS.pop(job_id, None)


def _new_resolver_scan_job(payload: dict[str, object]) -> dict[str, object]:
    now_ts = time.time()
    mode = str(payload.get("scan_mode") or "quick")
    phase = str(payload.get("phase") or "scan")
    domain = str(payload.get("domain") or "")
    return {
        "id": secrets.token_hex(8),
        "status": "queued",
        "phase": "queued",
        "phase_kind": phase,
        "message": "Queued",
        "started_at": now_ts,
        "finished_at": 0.0,
        "elapsed_seconds": 0,
        "progress_percent": 0.0,
        "scan_mode": mode,
        "domain": domain,
        "total": 0,
        "scanned": 0,
        "working": 0,
        "timeout": 0,
        "error_count": 0,
        "e2e_total": 0,
        "e2e_tested": 0,
        "e2e_passed": 0,
        "e2e_current_resolver": "",
        "e2e_current_ok": None,
        "e2e_passed_resolvers": [],
        "transparent_proxy_detected": None,
        "selected_resolver": "",
        "auto_applied": False,
        "applied_resolvers": [],
        "stop_requested": False,
        "stopped": False,
        "control_state": "running",
        "result": None,
        "error": "",
    }


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"", "none"}:
        return default
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def _persist_resolver_scan_snapshot(snapshot: dict[str, object]) -> None:
    if not isinstance(snapshot, dict):
        return
    data = dict(snapshot)
    data["snapshot_saved_at"] = int(time.time())
    try:
        set_setting(_RESOLVER_SCAN_LAST_JOB_KEY, json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        logger.warning("resolver scan snapshot persist failed: %s", exc)


def _load_persisted_resolver_scan_snapshot() -> dict[str, object] | None:
    raw = (get_setting(_RESOLVER_SCAN_LAST_JOB_KEY, "") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _extract_passed_e2e_resolvers(result: object) -> list[str]:
    if not isinstance(result, dict):
        return []
    mode = str(result.get("mode") or "").strip().lower()
    rows: list[object] = []
    if mode == "e2e":
        rows_raw = result.get("results")
        if isinstance(rows_raw, list):
            rows = rows_raw
    else:
        e2e = result.get("e2e")
        if isinstance(e2e, dict):
            rows_raw = e2e.get("results")
            if isinstance(rows_raw, list):
                rows = rows_raw

    out: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("ok")):
            continue
        resolver = str(item.get("resolver") or "").strip()
        if not resolver or resolver in seen:
            continue
        seen.add(resolver)
        out.append(resolver)
    return out


def _resolver_scan_job_public(job: dict[str, object]) -> dict[str, object]:
    elapsed = max(
        0,
        int(
            (float(job.get("finished_at") or 0.0) or time.time())
            - float(job.get("started_at") or time.time())
        ),
    )
    return {
        "id": job.get("id", ""),
        "status": job.get("status", "queued"),
        "phase": job.get("phase", "queued"),
        "message": job.get("message", ""),
        "scan_mode": job.get("scan_mode", "quick"),
        "domain": job.get("domain", ""),
        "phase_kind": job.get("phase_kind", "scan"),
        "progress_percent": float(job.get("progress_percent", 0.0) or 0.0),
        "elapsed_seconds": elapsed,
        "total": int(job.get("total", 0) or 0),
        "scanned": int(job.get("scanned", 0) or 0),
        "working": int(job.get("working", 0) or 0),
        "timeout": int(job.get("timeout", 0) or 0),
        "error_count": int(job.get("error_count", 0) or 0),
        "e2e_total": int(job.get("e2e_total", 0) or 0),
        "e2e_tested": int(job.get("e2e_tested", 0) or 0),
        "e2e_passed": int(job.get("e2e_passed", 0) or 0),
        "e2e_current_resolver": str(job.get("e2e_current_resolver") or ""),
        "e2e_current_ok": job.get("e2e_current_ok"),
        "e2e_passed_resolvers": list(job.get("e2e_passed_resolvers") or []),
        "transparent_proxy_detected": job.get("transparent_proxy_detected"),
        "selected_resolver": job.get("selected_resolver", ""),
        "auto_applied": bool(job.get("auto_applied")),
        "applied_resolvers": list(job.get("applied_resolvers") or []),
        "stop_requested": bool(job.get("stop_requested")),
        "stopped": bool(job.get("stopped")),
        "control_state": str(job.get("control_state") or "running"),
        "result": job.get("result"),
        "error": job.get("error", ""),
    }


def _get_active_resolver_scan_job_locked() -> dict[str, object] | None:
    active: dict[str, object] | None = None
    for job in _RESOLVER_SCAN_JOBS.values():
        if str(job.get("status")) in {"queued", "running", "paused"}:
            if active is None or float(job.get("started_at", 0) or 0) > float(active.get("started_at", 0) or 0):
                active = job
    return active


def _apply_resolver_scan_event_locked(job: dict[str, object], event: dict[str, object]) -> None:
    kind = str(event.get("kind") or "").strip().lower()
    if kind == "scan_start":
        job["status"] = "running"
        job["phase"] = "scan"
        job["phase_kind"] = "scan"
        job["control_state"] = "running"
        job["message"] = "Scanning resolvers"
        job["total"] = int(event.get("total", 0) or 0)
        job["domain"] = str(event.get("domain") or job.get("domain") or "")
        job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), 0.5)
    elif kind == "transparent_proxy":
        job["transparent_proxy_detected"] = bool(event.get("detected"))
    elif kind == "scan_progress":
        if str(job.get("status") or "") == "paused":
            return
        total = max(0, int(event.get("total", 0) or 0))
        scanned = max(0, int(event.get("scanned", 0) or 0))
        working = max(0, int(event.get("working", 0) or 0))
        timeout = max(0, int(event.get("timeout", 0) or 0))
        error_count = max(0, int(event.get("error", 0) or 0))
        job["phase"] = "scan"
        job["message"] = f"Scanning... {scanned}/{total} (working: {working})"
        job["total"] = max(int(job.get("total", 0) or 0), total)
        job["scanned"] = max(int(job.get("scanned", 0) or 0), scanned)
        job["working"] = max(int(job.get("working", 0) or 0), working)
        job["timeout"] = max(int(job.get("timeout", 0) or 0), timeout)
        job["error_count"] = max(int(job.get("error_count", 0) or 0), error_count)
        if total > 0:
            ratio = min(1.0, float(scanned) / float(total))
            job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), round(ratio * 70.0, 1))
    elif kind == "e2e_start":
        total = max(0, int(event.get("total", 0) or 0))
        job["status"] = "running"
        job["phase"] = "e2e"
        job["phase_kind"] = "e2e"
        job["control_state"] = "running"
        job["message"] = "Running E2E checks"
        job["e2e_total"] = total
        job["e2e_tested"] = 0
        job["e2e_passed"] = 0
        job["e2e_current_resolver"] = ""
        job["e2e_current_ok"] = None
        job["e2e_passed_resolvers"] = []
        if total == 0:
            job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), 95.0)
    elif kind == "e2e_testing":
        resolver = str(event.get("resolver") or "").strip()
        if resolver:
            job["phase"] = "e2e"
            job["status"] = "running"
            job["control_state"] = "running"
            job["e2e_current_resolver"] = resolver
            job["e2e_current_ok"] = None
    elif kind == "e2e_progress":
        if str(job.get("status") or "") == "paused":
            return
        tested = max(0, int(event.get("tested", 0) or 0))
        total = max(0, int(event.get("total", 0) or 0))
        passed = max(0, int(event.get("passed", 0) or 0))
        resolver = str(event.get("resolver") or "").strip()
        ok_value: bool | None = None
        if "ok" in event:
            ok_value = bool(event.get("ok"))
        job["phase"] = "e2e"
        job["message"] = f"E2E... {tested}/{total} (passed: {passed})"
        job["e2e_total"] = max(int(job.get("e2e_total", 0) or 0), total)
        job["e2e_tested"] = max(int(job.get("e2e_tested", 0) or 0), tested)
        job["e2e_passed"] = max(int(job.get("e2e_passed", 0) or 0), passed)
        if resolver:
            job["e2e_current_resolver"] = resolver
        if ok_value is not None:
            job["e2e_current_ok"] = ok_value
            if resolver and ok_value:
                passed_resolvers: list[str] = []
                seen: set[str] = set()
                for raw in list(job.get("e2e_passed_resolvers") or []):
                    item = str(raw or "").strip()
                    if not item or item in seen:
                        continue
                    seen.add(item)
                    passed_resolvers.append(item)
                if resolver not in seen:
                    passed_resolvers.append(resolver)
                job["e2e_passed_resolvers"] = passed_resolvers
        if total > 0:
            ratio = min(1.0, float(tested) / float(total))
            job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), round(70.0 + (ratio * 30.0), 1))
    elif kind == "scan_done":
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        mode = str(result.get("mode") or "scan").strip().lower()
        stopped = bool(result.get("stopped"))
        job["phase_kind"] = "e2e" if mode == "e2e" else "scan"
        job["status"] = "stopped" if stopped else "done"
        job["phase"] = "stopped" if stopped else "done"
        if stopped:
            job["message"] = "Scan stopped by user" if mode != "e2e" else "E2E stopped by user"
            job["progress_percent"] = min(99.0, max(float(job.get("progress_percent", 0.0) or 0.0), 1.0))
        else:
            job["message"] = "E2E complete" if mode == "e2e" else "Scan complete"
            job["progress_percent"] = 100.0
        job["finished_at"] = time.time()
        job["stopped"] = stopped
        job["control_state"] = "stopped" if stopped else "completed"
        job["result"] = result
        job["e2e_current_resolver"] = ""
        job["e2e_current_ok"] = None
        job["selected_resolver"] = str(result.get("selected_resolver") or "")
        job["auto_applied"] = bool(result.get("auto_applied"))
        job["applied_resolvers"] = list(result.get("applied_resolvers") or [])
        job["total"] = max(int(job.get("total", 0) or 0), int(result.get("total", 0) or 0))
        job["scanned"] = max(int(job.get("scanned", 0) or 0), int(result.get("scanned", 0) or 0))
        job["working"] = max(int(job.get("working", 0) or 0), int(result.get("working", 0) or 0))
        job["timeout"] = max(int(job.get("timeout", 0) or 0), int(result.get("timeout", 0) or 0))
        job["error_count"] = max(int(job.get("error_count", 0) or 0), int(result.get("error", 0) or 0))
        if mode == "e2e":
            job["e2e_total"] = max(int(job.get("e2e_total", 0) or 0), int(result.get("total", 0) or 0))
            job["e2e_tested"] = max(int(job.get("e2e_tested", 0) or 0), int(result.get("tested", 0) or 0))
            job["e2e_passed"] = max(int(job.get("e2e_passed", 0) or 0), int(result.get("passed", 0) or 0))
        else:
            e2e = result.get("e2e") if isinstance(result.get("e2e"), dict) else {}
            job["e2e_total"] = max(int(job.get("e2e_total", 0) or 0), int(e2e.get("tested", 0) or 0))
            job["e2e_tested"] = max(int(job.get("e2e_tested", 0) or 0), int(e2e.get("tested", 0) or 0))
            job["e2e_passed"] = max(int(job.get("e2e_passed", 0) or 0), int(e2e.get("passed", 0) or 0))
        final_passed = _extract_passed_e2e_resolvers(result)
        if final_passed:
            job["e2e_passed_resolvers"] = final_passed
        else:
            dedup: list[str] = []
            seen: set[str] = set()
            for raw in list(job.get("e2e_passed_resolvers") or []):
                resolver = str(raw or "").strip()
                if not resolver or resolver in seen:
                    continue
                seen.add(resolver)
                dedup.append(resolver)
            job["e2e_passed_resolvers"] = dedup


def _run_resolver_scan_job(job_id: str, options: dict[str, object]) -> None:
    control: ResolverScanController | None = None
    with _RESOLVER_SCAN_JOBS_LOCK:
        control = _RESOLVER_SCAN_CONTROLS.get(job_id)

    def _progress(event: dict[str, object]) -> None:
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            _apply_resolver_scan_event_locked(job, event)

    try:
        result = scan_dns_resolvers(
            domain=str(options.get("domain") or ""),
            password=str(options.get("password") or ""),
            resolvers=parse_dns_resolvers_text(str(options.get("resolvers_raw") or ""), use_system=False),
            include_public_pool=bool(options.get("include_public_pool")),
            timeout_ms=int(options.get("timeout_ms", 1800) or 1800),
            concurrency=int(options.get("concurrency", 96) or 96),
            query_size=int(options.get("query_size", 220) or 220),
            e2e_enabled=bool(options.get("e2e_enabled", True)),
            e2e_threshold=int(options.get("e2e_threshold", 4) or 4),
            e2e_max_candidates=int(options.get("e2e_max_candidates", 48) or 48),
            e2e_concurrency=int(options.get("e2e_concurrency", 8) or 8),
            auto_apply_best=bool(options.get("auto_apply_best", False)),
            control=control,
            progress=_progress,
        )
        snapshot_to_persist: dict[str, object] | None = None
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            _apply_resolver_scan_event_locked(job, {"kind": "scan_done", "result": result})
            snapshot_to_persist = _resolver_scan_job_public(job)
        if snapshot_to_persist:
            _persist_resolver_scan_snapshot(snapshot_to_persist)
    except Exception as exc:
        snapshot_to_persist: dict[str, object] | None = None
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            if control and control.is_stopped():
                job["status"] = "stopped"
                job["phase"] = "stopped"
                job["control_state"] = "stopped"
                job["stopped"] = True
                job["message"] = "Scan stopped by user"
            else:
                job["status"] = "error"
                job["phase"] = "error"
                job["control_state"] = "error"
                job["message"] = f"Scan failed: {exc}"
            job["finished_at"] = time.time()
            job["error"] = str(exc)
            job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), 1.0)
            snapshot_to_persist = _resolver_scan_job_public(job)
        if snapshot_to_persist:
            _persist_resolver_scan_snapshot(snapshot_to_persist)
    finally:
        with _RESOLVER_SCAN_JOBS_LOCK:
            _RESOLVER_SCAN_CONTROLS.pop(job_id, None)


def _run_resolver_e2e_job(job_id: str, options: dict[str, object]) -> None:
    control: ResolverScanController | None = None
    with _RESOLVER_SCAN_JOBS_LOCK:
        control = _RESOLVER_SCAN_CONTROLS.get(job_id)

    def _progress(event: dict[str, object]) -> None:
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            _apply_resolver_scan_event_locked(job, event)

    try:
        result = run_e2e_resolver_tests(
            domain=str(options.get("domain") or ""),
            password=str(options.get("password") or ""),
            resolvers=parse_dns_resolvers_text(str(options.get("resolvers_raw") or ""), use_system=False),
            concurrency=int(options.get("e2e_concurrency", 8) or 8),
            control=control,
            progress=_progress,
        )
        snapshot_to_persist: dict[str, object] | None = None
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            _apply_resolver_scan_event_locked(job, {"kind": "scan_done", "result": result})
            snapshot_to_persist = _resolver_scan_job_public(job)
        if snapshot_to_persist:
            _persist_resolver_scan_snapshot(snapshot_to_persist)
    except Exception as exc:
        snapshot_to_persist: dict[str, object] | None = None
        with _RESOLVER_SCAN_JOBS_LOCK:
            job = _RESOLVER_SCAN_JOBS.get(job_id)
            if not job:
                return
            if control and control.is_stopped():
                job["status"] = "stopped"
                job["phase"] = "stopped"
                job["control_state"] = "stopped"
                job["stopped"] = True
                job["message"] = "E2E stopped by user"
            else:
                job["status"] = "error"
                job["phase"] = "error"
                job["control_state"] = "error"
                job["message"] = f"E2E failed: {exc}"
            job["finished_at"] = time.time()
            job["error"] = str(exc)
            job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), 1.0)
            snapshot_to_persist = _resolver_scan_job_public(job)
        if snapshot_to_persist:
            _persist_resolver_scan_snapshot(snapshot_to_persist)
    finally:
        with _RESOLVER_SCAN_JOBS_LOCK:
            _RESOLVER_SCAN_CONTROLS.pop(job_id, None)


def _channel_label(source_url: str, channel_index: int | None = None) -> str:
    token = (source_url or "").strip().rstrip("/")
    if token:
        return token.rsplit("/", 1)[-1]
    if channel_index and channel_index > 0:
        return f"channel-{channel_index}"
    return ""


def _sync_channel_key(event: dict) -> str:
    domain = str(event.get("domain") or "")
    source_url = str(event.get("source_url") or "")
    channel_index = int(event.get("channel_index") or 0)
    if source_url:
        return f"{domain}|{source_url}"
    return f"{domain}|#{channel_index}"


def _recompute_sync_job_locked(job: dict) -> None:
    domains = job.get("_domains", {})
    channels = job.get("_channels", {})
    job["domains_total"] = max(int(job.get("domains_total", 0) or 0), len(domains))
    job["domains_done"] = sum(1 for item in domains.values() if item.get("done"))
    planned_channels = sum(int((item or {}).get("channels_total", 0) or 0) for item in domains.values())
    job["channels_total"] = max(int(job.get("channels_total", 0) or 0), planned_channels, len(channels))
    job["channels_done"] = sum(1 for item in channels.values() if item.get("done"))
    job["messages_total"] = sum(int((item or {}).get("message_total", 0) or 0) for item in channels.values())
    job["messages_done"] = sum(int((item or {}).get("message_done", 0) or 0) for item in channels.values())
    job["saved"] = sum(int((item or {}).get("channel_saved", 0) or 0) for item in channels.values())

    if job.get("status") == "done":
        job["progress_percent"] = 100.0
        job["eta_seconds"] = 0
    elif job.get("status") == "error":
        job["progress_percent"] = max(float(job.get("progress_percent", 0.0) or 0.0), 1.0)
        job["eta_seconds"] = None
    else:
        refresh_weight = 0.12 if job.get("server_refresh_requested") else 0.0
        refresh_ratio = 1.0 if (not job.get("server_refresh_requested") or job.get("server_refresh_done")) else 0.35
        if int(job.get("messages_total", 0) or 0) > 0:
            content_ratio = min(1.0, float(job.get("messages_done", 0) or 0) / max(1.0, float(job.get("messages_total", 0) or 0)))
        elif int(job.get("channels_total", 0) or 0) > 0:
            content_ratio = min(1.0, float(job.get("channels_done", 0) or 0) / max(1.0, float(job.get("channels_total", 0) or 0)))
        elif int(job.get("domains_total", 0) or 0) > 0:
            content_ratio = min(1.0, float(job.get("domains_done", 0) or 0) / max(1.0, float(job.get("domains_total", 0) or 0)))
        else:
            content_ratio = 0.0
        pct = (refresh_weight * refresh_ratio) + ((1.0 - refresh_weight) * content_ratio)
        job["progress_percent"] = round(max(0.5 if job.get("status") == "running" else 0.0, min(99.0, pct * 100.0)), 1)
        if job["progress_percent"] >= 1.0:
            elapsed = max(1.0, time.time() - float(job.get("started_at") or time.time()))
            total_est = elapsed / (job["progress_percent"] / 100.0)
            job["eta_seconds"] = max(0, int(total_est - elapsed))
        else:
            job["eta_seconds"] = None
    job["elapsed_seconds"] = max(0, int((float(job.get("finished_at") or 0.0) or time.time()) - float(job.get("started_at") or time.time())))


def _apply_sync_event_locked(job: dict, event: dict) -> None:
    kind = str(event.get("kind") or "").strip()
    if not kind:
        return

    if kind == "sync_start":
        job["status"] = "running"
        job["mode"] = str(event.get("mode") or job.get("mode") or "")
        job["phase"] = "starting"
        job["message"] = "Preparing sync"
    elif kind == "sync_plan":
        job["mode"] = str(event.get("mode") or job.get("mode") or "")
        job["domains_total"] = max(int(job.get("domains_total", 0) or 0), int(event.get("domains_total", 0) or 0))
        job["channels_total"] = max(int(job.get("channels_total", 0) or 0), int(event.get("channels_total", 0) or 0))
        job["phase"] = "planning"
        job["message"] = "Planning sync"
    elif kind == "server_refresh_start":
        job["mode"] = str(event.get("mode") or job.get("mode") or "")
        job["server_refresh_requested"] = True
        job["server_refresh_done"] = False
        job["phase"] = "server_refresh"
        job["message"] = "Refreshing server cache"
        job["domains_total"] = max(int(job.get("domains_total", 0) or 0), int(event.get("domains_total", 0) or 0))
        job["channels_total"] = max(int(job.get("channels_total", 0) or 0), int(event.get("channels_total", 0) or 0))
    elif kind == "server_refresh_done":
        job["server_refresh_requested"] = True
        job["server_refresh_done"] = True
        job["phase"] = "server_refresh_done"
        job["message"] = "Server refresh complete"
        job["refresh_result"] = event.get("result")
    elif kind == "domain_start":
        domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        if domain:
            dom = job["_domains"].setdefault(domain, {"channels_total": 0, "done": False, "ok": None, "saved": 0, "error": ""})
            dom["done"] = False
            job["current_domain"] = domain
        job["phase"] = "domain"
        job["message"] = f"Loading domain {job.get('current_domain') or ''}".strip()
    elif kind == "domain_meta":
        domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        channels_total = int(event.get("channels_total", 0) or 0)
        dom = job["_domains"].setdefault(domain, {"channels_total": 0, "done": False, "ok": None, "saved": 0, "error": ""})
        dom["channels_total"] = max(int(dom.get("channels_total", 0) or 0), channels_total)
        job["current_domain"] = domain
        job["phase"] = "domain_meta"
        job["message"] = f"Domain {domain} has {channels_total} channel(s)"
    elif kind in {"channel_start", "channel_plan", "channel_progress", "channel_done", "channel_error"}:
        key = _sync_channel_key(event)
        domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        source_url = str(event.get("source_url") or "").strip()
        channel_index = int(event.get("channel_index", 0) or 0)
        message_total = int(event.get("message_total", 0) or 0)
        message_done = int(event.get("message_done", 0) or 0)
        channel_saved = int(event.get("channel_saved", 0) or 0)
        state = job["_channels"].setdefault(
            key,
            {
                "domain": domain,
                "source_url": source_url,
                "channel_index": channel_index,
                "label": _channel_label(source_url, channel_index),
                "message_total": 0,
                "message_done": 0,
                "channel_saved": 0,
                "done": False,
                "error": "",
            },
        )
        state["domain"] = domain
        state["source_url"] = source_url or state.get("source_url", "")
        state["channel_index"] = channel_index or state.get("channel_index", 0)
        state["label"] = _channel_label(state["source_url"], int(state.get("channel_index", 0) or 0))
        state["message_total"] = max(int(state.get("message_total", 0) or 0), message_total)
        state["message_done"] = max(int(state.get("message_done", 0) or 0), message_done)
        state["channel_saved"] = max(int(state.get("channel_saved", 0) or 0), channel_saved)
        if kind == "channel_done":
            state["done"] = True
            if int(state.get("message_total", 0) or 0) > 0:
                state["message_done"] = max(int(state.get("message_done", 0) or 0), int(state.get("message_total", 0) or 0))
        elif kind == "channel_error":
            state["done"] = True
            state["error"] = str(event.get("error") or "")
        elif kind == "channel_start":
            state["done"] = False
        job["current_domain"] = domain or job.get("current_domain", "")
        job["current_channel"] = state.get("label", "")
        job["phase"] = "channel"
        if state["message_total"] > 0:
            job["message"] = f"Receiving {state['label']} {state['message_done']}/{state['message_total']}"
        else:
            job["message"] = f"Receiving {state['label']}"
    elif kind == "domain_done":
        domain = str(event.get("domain") or "").strip().lower().rstrip(".")
        dom = job["_domains"].setdefault(domain, {"channels_total": 0, "done": False, "ok": None, "saved": 0, "error": ""})
        dom["done"] = True
        dom["ok"] = bool(event.get("ok"))
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        dom["saved"] = int(result.get("saved", 0) or 0)
        dom["error"] = str(event.get("error") or result.get("error") or "")
        job["phase"] = "domain_done"
        job["message"] = f"Finished {domain}"
    elif kind == "sync_finish":
        job["status"] = "done"
        job["ok"] = bool(event.get("ok"))
        job["phase"] = "done"
        job["finished_at"] = time.time()
        job["result"] = event.get("result")
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        saved = int(result.get("saved", 0) or 0)
        job["message"] = f"{saved} message(s) updated" if saved > 0 else "No new messages"
    elif kind == "sync_error":
        job["status"] = "error"
        job["ok"] = False
        job["phase"] = "error"
        job["finished_at"] = time.time()
        job["error"] = str(event.get("error") or "")
        job["message"] = job["error"] or "Sync failed"

    _recompute_sync_job_locked(job)


def _sync_job_public(job: dict) -> dict:
    domains = []
    for domain, state in sorted(job.get("_domains", {}).items()):
        domains.append(
            {
                "domain": domain,
                "channels_total": int((state or {}).get("channels_total", 0) or 0),
                "done": bool((state or {}).get("done")),
                "ok": (state or {}).get("ok"),
                "saved": int((state or {}).get("saved", 0) or 0),
                "error": str((state or {}).get("error") or ""),
            }
        )
    channels = []
    for state in job.get("_channels", {}).values():
        channels.append(
            {
                "domain": str((state or {}).get("domain") or ""),
                "label": str((state or {}).get("label") or ""),
                "source_url": str((state or {}).get("source_url") or ""),
                "channel_index": int((state or {}).get("channel_index", 0) or 0),
                "message_total": int((state or {}).get("message_total", 0) or 0),
                "message_done": int((state or {}).get("message_done", 0) or 0),
                "channel_saved": int((state or {}).get("channel_saved", 0) or 0),
                "done": bool((state or {}).get("done")),
                "error": str((state or {}).get("error") or ""),
            }
        )
    channels.sort(key=lambda item: (item["domain"], item["channel_index"], item["label"]))
    return {
        "id": job["id"],
        "status": job["status"],
        "ok": job.get("ok"),
        "mode": job.get("mode", ""),
        "phase": job.get("phase", ""),
        "message": job.get("message", ""),
        "progress_percent": float(job.get("progress_percent", 0.0) or 0.0),
        "domains_total": int(job.get("domains_total", 0) or 0),
        "domains_done": int(job.get("domains_done", 0) or 0),
        "channels_total": int(job.get("channels_total", 0) or 0),
        "channels_done": int(job.get("channels_done", 0) or 0),
        "messages_total": int(job.get("messages_total", 0) or 0),
        "messages_done": int(job.get("messages_done", 0) or 0),
        "saved": int(job.get("saved", 0) or 0),
        "elapsed_seconds": int(job.get("elapsed_seconds", 0) or 0),
        "eta_seconds": job.get("eta_seconds"),
        "current_domain": job.get("current_domain", ""),
        "current_channel": job.get("current_channel", ""),
        "started_at": int(float(job.get("started_at") or 0)),
        "finished_at": int(float(job.get("finished_at") or 0)) if job.get("finished_at") else 0,
        "error": job.get("error", ""),
        "result": job.get("result"),
        "refresh_result": job.get("refresh_result"),
        "domains": domains,
        "channels": channels[-12:],
    }


def _get_active_sync_job_locked() -> dict | None:
    for job in _SYNC_JOBS.values():
        if job.get("status") in {"queued", "running"}:
            return job
    return None


def _run_sync_job(job_id: str, priority_channel: str = "") -> None:
    def on_progress(event: dict) -> None:
        with _SYNC_JOBS_LOCK:
            _cleanup_sync_jobs_locked()
            job = _SYNC_JOBS.get(job_id)
            if not job:
                return
            _apply_sync_event_locked(job, event)

    try:
        result = sync_once(progress=on_progress, force_server_refresh=True, priority_channel=priority_channel)
        logger.info("manual sync ok result=%s", result)
        record_event("manual_sync", ok=int(result.get("errors", 0) or 0) == 0, result=result)
        with _SYNC_JOBS_LOCK:
            job = _SYNC_JOBS.get(job_id)
            if not job:
                return
            if job.get("status") not in {"done", "error"}:
                _apply_sync_event_locked(
                    job,
                    {
                        "kind": "sync_finish",
                        "mode": job.get("mode", ""),
                        "ok": int(result.get("errors", 0) or 0) == 0,
                        "result": result,
                    },
                )
    except Exception as exc:
        logger.exception("manual sync job failed")
        record_event("manual_sync", level="error", ok=False, error=str(exc))
        with _SYNC_JOBS_LOCK:
            job = _SYNC_JOBS.get(job_id)
            if not job:
                return
            _apply_sync_event_locked(job, {"kind": "sync_error", "mode": job.get("mode", ""), "error": str(exc)})


def _normalize_source_mode(raw: str | None) -> str:
    mode = (raw or "dns").strip().lower()
    if mode == "telegram":
        mode = "direct"
    return mode if mode in {"direct", "dns"} else "dns"


def _request_prefers_channel_picker() -> bool:
    user_agent = (request.headers.get("User-Agent") or "").lower()
    if not user_agent:
        return False
    mobile_tokens = (
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "opera mini",
        "iemobile",
        "windows phone",
    )
    return any(token in user_agent for token in mobile_tokens)


def _normalize_channel_list(raw: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in parse_csv(raw or ""):
        try:
            url = normalize_tg_s_url(item)
        except Exception:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _settings_password_hash() -> str:
    return (get_setting("settings_password_hash", "") or "").strip()


def _app_password_hash() -> str:
    return (get_setting("app_password_hash", "") or "").strip()


def _app_password_enabled() -> bool:
    return bool(_app_password_hash())


def _app_auth_ttl_days() -> int:
    raw = (get_setting("app_auth_ttl_days", "7") or "7").strip()
    try:
        value = int(raw)
    except Exception:
        value = 7
    return max(1, min(365, value))


def _initial_channel_history_count() -> int:
    raw = (get_setting("initial_channel_history_count", "30") or "30").strip()
    try:
        value = int(raw)
    except Exception:
        value = 30
    return max(1, min(200, value))


def _app_password_sig(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:24]


def _jwt_b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt_b64url_decode(raw: str) -> bytes:
    token = (raw or "").strip()
    padding = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode(token + padding)


def _jwt_encode(payload: dict[str, object], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _jwt_b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_part = _jwt_b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_jwt_b64url_encode(signature)}"


def _jwt_decode(token: str, secret: str) -> dict[str, object] | None:
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual_sig = _jwt_b64url_decode(signature_part)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_jwt_b64url_decode(payload_part).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _issue_app_auth_token() -> str:
    password_hash = _app_password_hash()
    now_ts = int(time.time())
    payload = {
        "sub": "kabootar-app",
        "purpose": "app_unlock",
        "iat": now_ts,
        "exp": now_ts + (_app_auth_ttl_days() * 86400),
        "pwd": _app_password_sig(password_hash),
        "jti": secrets.token_hex(8),
    }
    return _jwt_encode(payload, settings.app_secret_key)


def _app_unlocked() -> bool:
    password_hash = _app_password_hash()
    if not password_hash:
        if request.cookies.get(_APP_AUTH_COOKIE):
            g.clear_app_auth_cookie = True
        return True
    token = (request.cookies.get(_APP_AUTH_COOKIE) or "").strip()
    if not token:
        return False
    payload = _jwt_decode(token, settings.app_secret_key)
    if not payload:
        g.clear_app_auth_cookie = True
        _clear_settings_access()
        return False
    if payload.get("purpose") != "app_unlock" or payload.get("sub") != "kabootar-app":
        g.clear_app_auth_cookie = True
        _clear_settings_access()
        return False
    try:
        exp = int(payload.get("exp") or 0)
    except Exception:
        exp = 0
    if exp <= int(time.time()):
        g.clear_app_auth_cookie = True
        _clear_settings_access()
        return False
    if str(payload.get("pwd") or "") != _app_password_sig(password_hash):
        g.clear_app_auth_cookie = True
        _clear_settings_access()
        return False
    return True


def _grant_app_access() -> None:
    if _app_password_enabled():
        g.app_auth_token = _issue_app_auth_token()
        g.clear_app_auth_cookie = False


def _clear_app_access() -> None:
    g.app_auth_token = ""
    g.clear_app_auth_cookie = True


def _settings_password_enabled() -> bool:
    return bool(_settings_password_hash())


def _settings_auth_sig(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:24]


def _settings_unlocked() -> bool:
    password_hash = _settings_password_hash()
    if not password_hash:
        session.pop("settings_auth_sig", None)
        return True
    return (session.get("settings_auth_sig") or "") == _settings_auth_sig(password_hash)


def _grant_settings_access() -> None:
    password_hash = _settings_password_hash()
    if password_hash:
        session["settings_auth_sig"] = _settings_auth_sig(password_hash)


def _clear_settings_access() -> None:
    session.pop("settings_auth_sig", None)


def _request_lang() -> str:
    raw = (request.headers.get("Accept-Language") or "").lower()
    return "fa" if raw.startswith("fa") or ",fa" in raw else "en"


def _app_unlock_copy() -> dict[str, str]:
    if _request_lang() == "fa":
        return {
            "title": "ورود به کبوتر",
            "subtitle": "برای ورود به برنامه، پسورد اپ را وارد کن.",
            "password_label": "پسورد اپ",
            "unlock_button": "ورود",
            "back_label": "بازگشت",
            "wrong_password": "پسورد اشتباه است.",
        }
    return {
        "title": "Unlock Kabootar",
        "subtitle": "Enter the app password to continue.",
        "password_label": "App password",
        "unlock_button": "Unlock",
        "back_label": "Back",
        "wrong_password": "Wrong password.",
    }


def _safe_next_path(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _app_guard_failed(meta: dict[str, object]):
    if request.path.startswith(("/debug", "/dns")) or request.path in {"/app/meta", "/sync-now", "/debug/state"} or request.is_json:
        return jsonify({"ok": False, "error": "app_locked"}), 401
    if request.method == "GET":
        return (
            render_template(
                "app_unlock.html",
                app_meta=meta,
                message=request.args.get("msg", ""),
                unlock_copy=_app_unlock_copy(),
                next_path=_safe_next_path(request.full_path[:-1] if request.full_path.endswith("?") else request.full_path),
            ),
            401,
        )
    return redirect(url_for("index", msg="App is locked. Enter password."))


def _settings_guard_failed():
    if request.path.startswith(("/dns/", "/debug")) or request.path == "/debug/state" or request.is_json:
        return jsonify({"ok": False, "error": "settings_locked"}), 403
    return redirect(url_for("settings_page", msg="Settings are locked. Enter password."))


def _channel_username_from_url(url: str) -> str:
    token = (url or "").strip().rstrip("/")
    if not token:
        return ""
    return token.rsplit("/", 1)[-1].strip().lower()


def _ensure_channel_rows(db, channels: list[str]) -> bool:
    changed = False
    for url in channels:
        username = _channel_username_from_url(url)
        if not username:
            continue

        row = db.scalar(select(Channel).where(Channel.source_url == url))
        if not row:
            # Keep backward compatibility with old rows that may have same username
            # but outdated source_url.
            row = db.scalar(select(Channel).where(Channel.username == username))
            if row:
                if row.source_url != url:
                    row.source_url = url
                    changed = True
                if not row.title:
                    row.title = username
                    changed = True
            else:
                db.add(
                    Channel(
                        username=username,
                        source_url=url,
                        title=username,
                        avatar_url="",
                        avatar_mime="",
                        avatar_b64="",
                    )
                )
                changed = True
    db.flush()
    return changed


def _normalize_resolver_line(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        return ""
    token = token.replace("dns://", "").strip()

    host = token
    port = 53

    if token.startswith("[") and "]" in token:
        # IPv6 style: [2001:4860:4860::8888]:53
        end = token.find("]")
        host = token[1:end].strip()
        rest = token[end + 1 :].strip()
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    else:
        # IPv4/domain: 1.1.1.1:53 or dns.google
        if token.count(":") == 1:
            h, p = token.rsplit(":", 1)
            if p.isdigit():
                host = h.strip()
                port = int(p)

    if not host:
        return ""
    port = max(1, min(65535, int(port)))
    return host if port == 53 else f"{host}:{port}"


def _normalize_dns_route_line(raw: str) -> str:
    line = (raw or "").strip()
    if not line:
        return ""

    parts = [x.strip() for x in line.split("|")]
    channel_part = parts[0] if len(parts) >= 1 else ""
    domain_part = parts[1] if len(parts) >= 2 else ""
    password_part = "|".join(parts[2:]).strip() if len(parts) >= 3 else ""

    channel_norm = ""
    if channel_part:
        try:
            channel_norm = normalize_tg_s_url(channel_part)
        except Exception:
            channel_norm = ""

    domain_norm = domain_part.rstrip(".").lower()
    password_norm = password_part

    # Password is meaningful only when both channel + domain are present.
    if password_norm and (not channel_norm or not domain_norm):
        password_norm = ""

    if not channel_norm and not domain_norm:
        return ""
    if channel_norm and domain_norm and password_norm:
        return f"{channel_norm}|{domain_norm}|{password_norm}"
    if channel_norm and domain_norm:
        return f"{channel_norm}|{domain_norm}"
    if channel_norm:
        return channel_norm
    return f"|{domain_norm}"


def _looks_like_channel_token(token: str) -> bool:
    t = (token or "").strip().lower()
    if not t:
        return False
    return (
        t.startswith("@")
        or t.startswith("http://")
        or t.startswith("https://")
        or t.startswith("tg://")
        or ("t.me" in t)
        or ("telegram.me" in t)
    )


def _normalize_domain_host(value: str) -> str:
    token = (value or "").strip().replace("\\", "/")
    if not token:
        return ""

    if "://" not in token:
        broken_scheme = re.match(r"^(https?|wss?)(/+)(.+)$", token, flags=re.I)
        if broken_scheme:
            token = f"{broken_scheme.group(1)}://{broken_scheme.group(3)}"

    token = re.sub(r"^[a-z][a-z0-9+.-]*://", "", token, count=1, flags=re.I).lstrip("/")
    token = token.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip().rstrip(".").lower()
    if token.startswith("[") and "]" in token:
        token = token[1 : token.find("]")]
    elif token.count(":") == 1:
        host, port = token.rsplit(":", 1)
        if port.isdigit():
            token = host.strip()
    if not token or not re.fullmatch(r"[a-z0-9.-]+", token):
        return ""
    return token


def _normalize_dns_domain_line(raw: str) -> str:
    line = (raw or "").strip()
    if not line:
        return ""

    parts = [x.strip() for x in line.split("|")]
    domain_part = ""
    password_part = ""
    if len(parts) == 1:
        domain_part = parts[0]
    else:
        first = parts[0]
        second = parts[1] if len(parts) >= 2 else ""
        if not first:
            # Legacy: |domain|password
            domain_part = second
            password_part = "|".join(parts[2:]).strip() if len(parts) >= 3 else ""
        elif _looks_like_channel_token(first):
            # Legacy: channel|domain|password
            domain_part = second
            password_part = "|".join(parts[2:]).strip() if len(parts) >= 3 else ""
        else:
            # New: domain|password
            domain_part = first
            password_part = "|".join(parts[1:]).strip() if len(parts) >= 2 else ""

    domain_norm = _normalize_domain_host(domain_part)
    if not domain_norm:
        return ""
    return f"{domain_norm}|{password_part}" if password_part else domain_norm


def _load_dns_domain_lines(raw: str | None = None) -> list[str]:
    source = raw if raw is not None else get_setting("dns_domains", "") or ""
    lines: list[str] = []
    for item in source.splitlines():
        normalized = _normalize_dns_domain_line(item)
        if normalized and normalized not in lines:
            lines.append(normalized)
    return lines


def _load_domain_health_map() -> dict[str, dict]:
    raw = (get_setting("dns_domain_health", "{}") or "{}").strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        out[k.lower().rstrip(".")] = v
    return out


def _save_domain_health(domain: str, status: dict) -> None:
    dom = (domain or "").strip().lower().rstrip(".")
    if not dom:
        return
    data = _load_domain_health_map()
    status_copy = dict(status or {})
    status_copy["last_seen"] = int(time.time())
    data[dom] = status_copy
    # keep map bounded
    if len(data) > 200:
        items = list(data.items())
        items.sort(key=lambda kv: int((kv[1] or {}).get("last_seen", 0)), reverse=True)
        data = dict(items[:200])
    set_setting("dns_domain_health", json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _request_prefers_json() -> bool:
    if (request.headers.get("X-Kabootar-Request") or "").strip().lower() == "fetch":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _dedupe_display_messages(messages: list[Message]) -> list[Message]:
    deduped: list[Message] = []
    prev_text_key = None
    for msg in messages:
        text_key = " ".join((msg.text or "").split())
        if text_key and not msg.has_media and not msg.photo_items and text_key == prev_text_key:
            continue
        deduped.append(msg)
        prev_text_key = text_key if text_key and not msg.has_media and not msg.photo_items else None
    return deduped


def _load_index_state(selected: str | None, prefer_picker: bool, app_settings: dict[str, str]) -> dict[str, object]:
    source_mode = _normalize_source_mode(app_settings.get("source_mode"))
    dns_domain_lines = _load_dns_domain_lines(app_settings.get("dns_domains"))
    configured_channels = _normalize_channel_list(app_settings.get("direct_channels", "") or "")
    configured_set = set(configured_channels)
    configured_order = {url: i for i, url in enumerate(configured_channels)}

    with SessionLocal() as db:
        if configured_channels:
            if _ensure_channel_rows(db, configured_channels):
                db.commit()
            channels = db.scalars(
                select(Channel).where(Channel.source_url.in_(configured_channels))
            ).all()
            channels = sorted(
                channels,
                key=lambda c: (
                    configured_order.get(c.source_url, 10_000),
                    c.username.lower(),
                ),
            )
        else:
            channels = db.scalars(select(Channel).order_by(Channel.username.asc())).all()

        latest_rows = db.execute(
            select(Message.channel_id, func.max(Message.message_id)).group_by(Message.channel_id)
        ).all()
        latest_by_channel = {row[0]: (row[1] or 0) for row in latest_rows}
        channels = sorted(
            channels,
            key=lambda c: (
                -int(latest_by_channel.get(c.id, 0) or 0),
                configured_order.get(c.source_url, 10_000),
                (c.username or "").lower(),
            ),
        )
        latest_by_source = {
            c.source_url: int(latest_by_channel.get(c.id, 0) or 0)
            for c in channels
        }

        selected_url = selected
        if not selected_url and channels and not prefer_picker:
            selected_url = channels[0].source_url
        elif selected_url and configured_channels and selected_url not in configured_set:
            selected_url = None if prefer_picker else (channels[0].source_url if channels else None)
        selected_channel = next((c for c in channels if c.source_url == selected_url), None)

        messages: list[Message] = []
        if selected_channel:
            messages = db.scalars(
                select(Message)
                .where(Message.channel_id == selected_channel.id)
                .order_by(Message.message_id.desc())
                .limit(120)
            ).all()
            messages = list(reversed(messages))
            messages = _dedupe_display_messages(messages)

    return {
        "channels": channels,
        "selected": selected_url,
        "selected_channel": selected_channel,
        "messages": messages,
        "latest_by_channel": latest_by_channel,
        "latest_by_source": latest_by_source,
        "source_mode": source_mode,
        "dns_domains_count": len(dns_domain_lines),
    }


def _resolve_frontend_dirs() -> tuple[Path, Path]:
    override_template = (os.getenv("KABOOTAR_TEMPLATE_DIR", "") or "").strip()
    override_static = (os.getenv("KABOOTAR_STATIC_DIR", "") or "").strip()
    if override_template and override_static:
        tpl = Path(override_template).expanduser().resolve()
        sta = Path(override_static).expanduser().resolve()
        if tpl.exists() and sta.exists():
            return tpl, sta

    override_root = (os.getenv("KABOOTAR_FRONTEND_ROOT", "") or "").strip()
    if override_root:
        root = Path(override_root).expanduser().resolve()
        tpl = root / "templates"
        sta = root / "static"
        if tpl.exists() and sta.exists():
            return tpl, sta

    here = Path(__file__).resolve().parent.parent
    candidates: list[Path] = [here]

    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        candidates.append(Path(str(mei)))

    for base in candidates:
        tpl = base / "frontend" / "templates"
        sta = base / "frontend" / "static"
        if tpl.exists() and sta.exists():
            return tpl, sta

    return here / "frontend" / "templates", here / "frontend" / "static"


@lru_cache(maxsize=1)
def _load_logo_png_master() -> bytes:
    _, static_dir = _resolve_frontend_dirs()
    svg_path = static_dir / "kabootar.svg"
    svg_text = svg_path.read_text(encoding="utf-8")
    match = re.search(r'data:image/png;base64,\s*([^"]+)"', svg_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError(f"embedded PNG data not found in {svg_path}")
    raw = re.sub(r"\s+", "", match.group(1))
    return base64.b64decode(raw)


@lru_cache(maxsize=8)
def _logo_png_bytes(size: int) -> bytes:
    size = max(32, min(1024, int(size or 180)))
    image = Image.open(BytesIO(_load_logo_png_master())).convert("RGBA")
    resized = image.resize((size, size), Image.Resampling.LANCZOS)
    buf = BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue()


def create_app() -> Flask:
    setup_logging()
    ensure_schema()
    meta = app_meta()
    meta_dict = meta.as_dict()
    template_dir, static_dir = _resolve_frontend_dirs()
    app = Flask(__name__, template_folder=str(template_dir), static_folder=str(static_dir))
    app.config["SECRET_KEY"] = settings.app_secret_key
    started_loop = start_background_sync_loop()
    record_event("web_app_ready", host=settings.app_host, port=settings.app_port, version=meta.version_name, version_code=meta.version_code)
    if started_loop:
        record_event("background_sync_ready", interval_minutes=background_sync_status().get("interval_minutes", 1))

    @app.get("/sw.js")
    def sw():
        resp = send_from_directory(app.static_folder, "sw.js")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.before_request
    def enforce_app_auth():
        g.app_auth_token = None
        g.clear_app_auth_cookie = False
        exempt = {
            "static",
            "sw",
            "apple_touch_icon",
            "pwa_icon_192",
            "pwa_icon_512",
            "web_manifest",
            "app_unlock",
        }
        if request.endpoint in exempt or request.path.startswith("/static/"):
            return None
        if _app_unlocked():
            return None
        return _app_guard_failed(meta_dict)

    @app.get("/apple-touch-icon.png")
    def apple_touch_icon():
        return send_file(BytesIO(_logo_png_bytes(180)), mimetype="image/png", max_age=86400)

    @app.get("/pwa/icon-192.png")
    def pwa_icon_192():
        return send_file(BytesIO(_logo_png_bytes(192)), mimetype="image/png", max_age=86400)

    @app.get("/pwa/icon-512.png")
    def pwa_icon_512():
        return send_file(BytesIO(_logo_png_bytes(512)), mimetype="image/png", max_age=86400)

    @app.get("/manifest.webmanifest")
    def web_manifest():
        manifest = {
            "id": "/",
            "name": meta.app_name,
            "short_name": meta.app_name,
            "description": "Kabootar web client",
            "lang": "fa",
            "dir": "auto",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#081018",
            "theme_color": "#081018",
            "icons": [
                {
                    "src": "/pwa/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/pwa/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return app.response_class(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            mimetype="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.after_request
    def add_cache_headers(resp):
        app_auth_token = getattr(g, "app_auth_token", None)
        if getattr(g, "clear_app_auth_cookie", False):
            resp.delete_cookie(_APP_AUTH_COOKIE, path="/", samesite="Lax")
        elif app_auth_token:
            resp.set_cookie(
                _APP_AUTH_COOKIE,
                app_auth_token,
                max_age=_app_auth_ttl_days() * 86400,
                httponly=True,
                secure=request.is_secure,
                samesite="Lax",
                path="/",
            )
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "public, max-age=86400, immutable"
        elif request.path in {"/", "/settings", "/debug", "/debug/state", "/channel/state"}:
            resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Kabootar-Version"] = meta.version_name
        resp.headers["X-Kabootar-Version-Code"] = str(meta.version_code)
        return resp

    @app.get("/app/meta")
    def app_meta_json():
        return jsonify(meta_dict)

    @app.post("/app/unlock")
    def app_unlock():
        password_hash = _app_password_hash()
        next_path = _safe_next_path(request.form.get("next"))
        unlock_copy = _app_unlock_copy()
        if not password_hash:
            _clear_app_access()
            return redirect(next_path or url_for("index"))

        password = (request.form.get("password") or "").strip()
        if not password or not check_password_hash(password_hash, password):
            return (
                render_template(
                    "app_unlock.html",
                    app_meta=meta_dict,
                    message=unlock_copy["wrong_password"],
                    unlock_copy=unlock_copy,
                    next_path=next_path or "/",
                ),
                401,
            )

        _grant_app_access()
        return redirect(next_path or url_for("index"))

    @app.post("/app/lock")
    def app_lock():
        _clear_app_access()
        _clear_settings_access()
        return redirect(url_for("index", msg="App locked."))

    @app.get("/settings")
    def settings_page():
        settings_locked = not _settings_unlocked()
        app_settings = {} if settings_locked else all_settings()
        app_settings.pop("settings_password_hash", None)
        app_settings.pop("app_password_hash", None)
        return render_template(
            "settings.html",
            settings=app_settings,
            message=request.args.get("msg", ""),
            app_meta=meta_dict,
            settings_locked=settings_locked,
            settings_password_enabled=_settings_password_enabled(),
            app_password_enabled=_app_password_enabled(),
            app_auth_ttl_days=_app_auth_ttl_days(),
        )

    @app.post("/settings/unlock")
    def settings_unlock():
        password_hash = _settings_password_hash()
        if not password_hash:
            _clear_settings_access()
            return redirect(url_for("settings_page", msg="Settings password is not set."))

        password = (request.form.get("password") or "").strip()
        if not password or not check_password_hash(password_hash, password):
            _clear_settings_access()
            return redirect(url_for("settings_page", msg="Wrong password."))

        _grant_settings_access()
        return redirect(url_for("settings_page", msg="Settings unlocked."))

    @app.post("/settings/lock")
    def settings_lock():
        _clear_settings_access()
        return redirect(url_for("settings_page", msg="Settings locked."))

    @app.post("/settings/password")
    def settings_password_save():
        if not _app_unlocked():
            return _app_guard_failed(meta_dict)
        password_hash = _settings_password_hash()
        if password_hash and not _settings_unlocked():
            return redirect(url_for("settings_page", msg="Settings are locked. Enter password."))

        current_password = (request.form.get("current_password") or "").strip()
        new_password = (request.form.get("new_password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()
        remove_password = request.form.get("remove_password") == "1"

        if password_hash:
            if not current_password or not check_password_hash(password_hash, current_password):
                return redirect(url_for("settings_page", msg="Current password is wrong."))

        if remove_password:
            set_setting("settings_password_hash", "")
            _clear_settings_access()
            return redirect(url_for("settings_page", msg="Settings password removed."))

        if len(new_password) < 4:
            return redirect(url_for("settings_page", msg="New password must be at least 4 characters."))
        if new_password != confirm_password:
            return redirect(url_for("settings_page", msg="Password confirmation does not match."))

        new_hash = generate_password_hash(new_password)
        set_setting("settings_password_hash", new_hash)
        session["settings_auth_sig"] = _settings_auth_sig(new_hash)
        return redirect(url_for("settings_page", msg="Settings password saved."))

    @app.post("/app/password")
    def app_password_save():
        if not _settings_unlocked():
            return redirect(url_for("settings_page", msg="Settings are locked. Enter password."))

        password_hash = _app_password_hash()
        current_password = (request.form.get("current_password") or "").strip()
        new_password = (request.form.get("new_password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()
        remove_password = request.form.get("remove_password") == "1"

        ttl_raw = (request.form.get("ttl_days") or str(_app_auth_ttl_days())).strip()
        try:
            ttl_days = int(ttl_raw)
        except Exception:
            ttl_days = _app_auth_ttl_days()
        ttl_days = max(1, min(365, ttl_days))
        set_setting("app_auth_ttl_days", ttl_days)

        if password_hash:
            if not current_password or not check_password_hash(password_hash, current_password):
                return redirect(url_for("settings_page", msg="Current app password is wrong."))

        if remove_password:
            set_setting("app_password_hash", "")
            _clear_app_access()
            return redirect(url_for("settings_page", msg="App password removed."))

        if not new_password and not password_hash:
            return redirect(url_for("settings_page", msg="App session duration saved."))

        if not new_password and password_hash:
            _grant_app_access()
            return redirect(url_for("settings_page", msg="App session duration saved."))

        if len(new_password) < 4:
            return redirect(url_for("settings_page", msg="New app password must be at least 4 characters."))
        if new_password != confirm_password:
            return redirect(url_for("settings_page", msg="App password confirmation does not match."))

        new_hash = generate_password_hash(new_password)
        set_setting("app_password_hash", new_hash)
        _grant_app_access()
        return redirect(url_for("settings_page", msg="App password saved."))

    @app.get("/debug")
    def debug_page():
        if not _settings_unlocked():
            return redirect(url_for("settings_page", msg="Settings are locked. Enter password."))
        return render_template("debug.html")

    @app.get("/debug/state")
    def debug_state():
        if not _settings_unlocked():
            return jsonify({"ok": False, "error": "settings_locked"}), 403
        app_settings = all_settings()
        channel_list = _normalize_channel_list(app_settings.get("direct_channels", ""))
        domain_list = [line for line in (app_settings.get("dns_domains", "") or "").splitlines() if line.strip()]
        resolver_list = [line for line in (app_settings.get("dns_resolvers", "") or "").splitlines() if line.strip()]
        payload = {
            "app": meta.as_dict(),
            "runtime": runtime_summary(),
            "background_sync": background_sync_status(),
            "settings": {
                "source_mode": _normalize_source_mode(app_settings.get("source_mode")),
                "sync_interval_minutes": app_settings.get("sync_interval_minutes", ""),
                "channels_count": len(channel_list),
                "domains_count": len(domain_list),
                "resolvers_count": len(resolver_list),
                "use_system_resolver": app_settings.get("dns_use_system_resolver", "0") == "1",
                "channels": channel_list,
                "domains": domain_list,
                "resolvers": resolver_list,
            },
            "events": snapshot_events(80),
            "log_tail": tail_log_lines(160),
            "resolver_health": export_resolver_health(),
            "domain_health": _load_domain_health_map(),
            "now": int(time.time()),
        }
        return jsonify(payload)

    @app.post("/settings")
    def settings_save():
        if not _settings_unlocked():
            message = "Settings are locked. Enter password."
            if _request_prefers_json():
                return jsonify({"ok": False, "message": message}), 403
            return redirect(url_for("settings_page", msg=message))

        source_mode = _normalize_source_mode(request.form.get("source_mode"))
        channels = _normalize_channel_list(request.form.get("direct_channels"))
        channels_csv = ",".join(channels)

        # Canonicalize resolver list (one per line: resolver or resolver:port).
        resolver_lines: list[str] = []
        for raw in (request.form.get("dns_resolvers", "") or "").splitlines():
            normalized = _normalize_resolver_line(raw)
            if normalized:
                resolver_lines.append(normalized)
        resolver_lines = list(dict.fromkeys(resolver_lines))
        resolvers_raw = "\n".join(resolver_lines)

        domain_lines: list[str] = []
        for raw in (request.form.get("dns_domains", "") or "").splitlines():
            normalized = _normalize_dns_domain_line(raw)
            if normalized:
                domain_lines.append(normalized)
        # de-duplicate while preserving order
        domain_lines = list(dict.fromkeys(domain_lines))
        domains_raw = "\n".join(domain_lines)
        sync_interval_raw = request.form.get("sync_interval_minutes", "1") or "1"
        try:
            sync_interval_minutes = int(sync_interval_raw)
        except Exception:
            sync_interval_minutes = 1
        initial_history_raw = request.form.get("initial_channel_history_count", "30") or "30"
        try:
            initial_history_count = int(initial_history_raw)
        except Exception:
            initial_history_count = 30
        initial_history_count = max(1, min(200, initial_history_count))

        set_settings_bulk(
            {
                "source_mode": source_mode,
                "direct_channels": channels_csv,
                "dns_client_channels": channels_csv,
                "direct_proxies": request.form.get("direct_proxies", ""),
                "dns_password": request.form.get("dns_password", ""),
                "dns_client_id": request.form.get("dns_client_id", ""),
                "dns_resolvers": resolvers_raw,
                "dns_domains": domains_raw,
                "dns_query_size": request.form.get("dns_query_size", ""),
                "dns_timeout_seconds": request.form.get("dns_timeout_seconds", ""),
                "sync_interval_minutes": sync_interval_raw,
                "initial_channel_history_count": str(initial_history_count),
                "dns_channel_routes": "",
                "dns_domain": "",
                "dns_sources": "",
                "dns_use_system_resolver": "1" if request.form.get("dns_use_system_resolver") == "1" else "0",
            }
        )

        ok, out = apply_sync_cron(sync_interval_minutes)

        push_queued = source_mode == "dns" and bool(channels) and bool(domains_raw.strip())
        if push_queued:
            def _push_channels_background(channels_copy: list[str], domains_copy: str) -> None:
                try:
                    pushed = push_channels_to_domains(channels_copy, domain_text=domains_copy)
                    bad = [r for r in pushed.get("results", []) if not r.get("ok")]
                    record_event(
                        "settings_dns_push_background",
                        ok=not bad,
                        failed=len(bad),
                        channels=len(channels_copy),
                    )
                except Exception as exc:
                    record_event(
                        "settings_dns_push_background",
                        level="warning",
                        ok=False,
                        channels=len(channels_copy),
                        error=str(exc),
                    )

            threading.Thread(
                target=_push_channels_background,
                args=(list(channels), domains_raw),
                daemon=True,
            ).start()

        msg = "saved" if ok else f"saved (cron warning: {out})"
        if push_queued:
            msg = f"{msg}; dns-push=queued"

        if _request_prefers_json():
            return jsonify({"ok": True, "message": msg, "push_queued": push_queued, "cron_ok": ok, "cron_message": out})
        return redirect(url_for("settings_page", msg=msg))

    @app.post("/dns/domain/check")
    def dns_domain_check():
        if not _settings_unlocked():
            return _settings_guard_failed()
        payload = request.get_json(silent=True) or request.form
        domain = (payload.get("domain") or "").strip().rstrip(".").lower()
        password = (payload.get("password") or "").strip()
        action = (payload.get("action") or "probe").strip().lower()
        if not domain:
            return jsonify({"ok": False, "error": "domain_required"}), 400

        started = time.time()
        status = probe_dns_domain(domain, password)
        status["action"] = action
        if action in {"sync", "fetch"} and status.get("ok"):
            sync_result = sync_from_dns_domain(domain, password)
            status["sync"] = sync_result
            if int(sync_result.get("errors", 0) or 0) > 0:
                status["ok"] = False
        status["elapsed_total_ms"] = int((time.time() - started) * 1000)
        _save_domain_health(domain, status)
        return jsonify(status)

    @app.get("/dns/domain/health")
    def dns_domain_health():
        if not _settings_unlocked():
            return _settings_guard_failed()
        domain = (request.args.get("domain") or "").strip().rstrip(".").lower()
        if not domain:
            return jsonify({"ok": False, "error": "domain_required"}), 400
        data = _load_domain_health_map()
        if domain not in data:
            return jsonify({"ok": False, "domain": domain, "error": "no_history", "checked_at": 0})
        return jsonify(data[domain])

    @app.post("/dns/resolvers/scan/start")
    def dns_resolvers_scan_start():
        if not _settings_unlocked():
            return _settings_guard_failed()

        payload = request.get_json(silent=True) or request.form or {}
        scan_mode = str(payload.get("scan_mode") or payload.get("mode") or "quick").strip().lower()
        include_public_pool = scan_mode in {"deep", "full", "public", "extended"}

        resolver_lines: list[str] = []
        resolvers_raw_input = str(payload.get("dns_resolvers") or get_setting("dns_resolvers", "") or "")
        for raw in resolvers_raw_input.splitlines():
            normalized = _normalize_resolver_line(raw)
            if normalized:
                resolver_lines.append(normalized)
        resolver_lines = list(dict.fromkeys(resolver_lines))
        resolvers_raw = "\n".join(resolver_lines)

        domain_lines: list[str] = []
        domains_raw_input = str(payload.get("dns_domains") or get_setting("dns_domains", "") or "")
        for raw in domains_raw_input.splitlines():
            normalized = _normalize_dns_domain_line(raw)
            if normalized:
                domain_lines.append(normalized)
        domain_lines = list(dict.fromkeys(domain_lines))
        domains_raw = "\n".join(domain_lines)

        requested_domain = _normalize_domain_host(str(payload.get("domain") or ""))
        requested_password = str(payload.get("password") or "").strip()
        domain_targets = parse_dns_domains_text(domains_raw)
        if not requested_domain and domain_targets:
            requested_domain = domain_targets[0].domain
        if requested_domain and not requested_password:
            for item in domain_targets:
                if item.domain == requested_domain and item.password:
                    requested_password = item.password
                    break

        timeout_seconds_raw = str(payload.get("dns_timeout_seconds") or get_setting("dns_timeout_seconds", "3") or "3").strip()
        try:
            timeout_seconds = float(timeout_seconds_raw)
        except Exception:
            timeout_seconds = 3.0
        timeout_ms = int(max(0.5, min(6.0, timeout_seconds)) * 1000.0)

        query_size_raw = str(payload.get("dns_query_size") or get_setting("dns_query_size", "220") or "220").strip()
        try:
            query_size = int(query_size_raw)
        except Exception:
            query_size = 220

        concurrency_raw = str(payload.get("scan_concurrency") or "").strip()
        try:
            concurrency = int(concurrency_raw) if concurrency_raw else (112 if include_public_pool else 64)
        except Exception:
            concurrency = 112 if include_public_pool else 64
        concurrency = max(4, min(256, concurrency))

        e2e_enabled_raw_value = payload.get("e2e_enabled")
        if e2e_enabled_raw_value is None:
            e2e_enabled_raw_value = payload.get("e2e")
        if e2e_enabled_raw_value is None:
            e2e_enabled_raw_value = payload.get("inline_e2e")
        e2e_enabled = _parse_bool(e2e_enabled_raw_value, default=False)

        e2e_threshold_raw = str(payload.get("e2e_threshold") or "4").strip()
        try:
            e2e_threshold = int(e2e_threshold_raw)
        except Exception:
            e2e_threshold = 4
        e2e_max_raw = str(payload.get("e2e_max_candidates") or "").strip()
        try:
            e2e_max_candidates = int(e2e_max_raw) if e2e_max_raw else (80 if include_public_pool else 32)
        except Exception:
            e2e_max_candidates = 80 if include_public_pool else 32
        e2e_concurrency_raw = str(payload.get("e2e_concurrency") or "").strip()
        try:
            e2e_concurrency = int(e2e_concurrency_raw) if e2e_concurrency_raw else 8
        except Exception:
            e2e_concurrency = 8

        auto_apply_raw_value = payload.get("auto_apply_best")
        if auto_apply_raw_value is None:
            auto_apply_raw_value = payload.get("auto_apply")
        auto_apply_best = _parse_bool(auto_apply_raw_value, default=False)

        scan_only_raw_value = payload.get("scan_only")
        if scan_only_raw_value is None:
            scan_only_raw_value = payload.get("no_auto_apply")
        scan_only = _parse_bool(scan_only_raw_value, default=False)
        if scan_only:
            auto_apply_best = False
        if auto_apply_best:
            # Auto-apply relies on verified E2E pass.
            e2e_enabled = True

        options = {
            "phase": "scan",
            "scan_mode": scan_mode,
            "domain": requested_domain,
            "password": requested_password,
            "resolvers_raw": resolvers_raw,
            "include_public_pool": include_public_pool,
            "timeout_ms": timeout_ms,
            "concurrency": concurrency,
            "query_size": query_size,
            "e2e_enabled": e2e_enabled,
            "e2e_threshold": e2e_threshold,
            "e2e_max_candidates": e2e_max_candidates,
            "e2e_concurrency": e2e_concurrency,
            "auto_apply_best": auto_apply_best,
            "scan_only": scan_only,
        }

        with _RESOLVER_SCAN_JOBS_LOCK:
            _cleanup_resolver_scan_jobs_locked()
            active = _get_active_resolver_scan_job_locked()
            if active:
                payload_job = _resolver_scan_job_public(active)
                payload_job["reused"] = True
                return jsonify({"ok": True, "job": payload_job})

            job = _new_resolver_scan_job(options)
            _RESOLVER_SCAN_JOBS[str(job["id"])] = job
            _RESOLVER_SCAN_CONTROLS[str(job["id"])] = ResolverScanController()
            payload_job = _resolver_scan_job_public(job)

        threading.Thread(target=_run_resolver_scan_job, args=(str(job["id"]), options), daemon=True).start()
        record_event(
            "resolver_scan_requested",
            mode=scan_mode,
            include_public=include_public_pool,
            auto_apply=auto_apply_best,
            inline_e2e=e2e_enabled,
            scan_only=scan_only,
            resolver_count=len(resolver_lines),
            domain=requested_domain,
        )
        return jsonify({"ok": True, "job": payload_job})

    @app.post("/dns/resolvers/scan/control")
    def dns_resolvers_scan_control():
        if not _settings_unlocked():
            return _settings_guard_failed()

        payload = request.get_json(silent=True) or request.form or {}
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"pause", "resume", "start", "stop", "end", "cancel"}:
            return jsonify({"ok": False, "error": "invalid_action"}), 400
        requested_id = str(payload.get("id") or payload.get("job_id") or "").strip()

        with _RESOLVER_SCAN_JOBS_LOCK:
            _cleanup_resolver_scan_jobs_locked()
            job = _RESOLVER_SCAN_JOBS.get(requested_id) if requested_id else _get_active_resolver_scan_job_locked()
            if not job:
                return jsonify({"ok": False, "error": "not_found"}), 404

            job_id = str(job.get("id") or "")
            status = str(job.get("status") or "")
            if status in {"done", "error", "stopped"}:
                return jsonify({"ok": False, "error": "job_not_running", "job": _resolver_scan_job_public(job)}), 409

            control = _RESOLVER_SCAN_CONTROLS.get(job_id)
            if not control:
                return jsonify({"ok": False, "error": "job_not_running", "job": _resolver_scan_job_public(job)}), 409

            if action == "pause":
                control.pause()
                job["status"] = "paused"
                job["control_state"] = "paused"
                job["message"] = "Paused by user"
            elif action in {"resume", "start"}:
                control.resume()
                job["status"] = "running"
                job["control_state"] = "running"
                if str(job.get("phase") or "") == "e2e":
                    job["message"] = "E2E resumed"
                else:
                    job["message"] = "Scan resumed"
            else:
                control.stop()
                job["stop_requested"] = True
                job["control_state"] = "stopping"
                if str(job.get("phase") or "") == "e2e":
                    job["message"] = "Stopping E2E..."
                else:
                    job["message"] = "Stopping scan..."

            payload_job = _resolver_scan_job_public(job)

        record_event("resolver_scan_control", action=action, job_id=job_id)
        return jsonify({"ok": True, "job": payload_job})

    @app.post("/dns/resolvers/e2e/start")
    def dns_resolvers_e2e_start():
        if not _settings_unlocked():
            return _settings_guard_failed()

        payload = request.get_json(silent=True) or request.form or {}

        resolver_lines: list[str] = []
        resolver_list = payload.get("resolvers")
        if isinstance(resolver_list, list):
            for token in resolver_list:
                normalized = _normalize_resolver_line(str(token or ""))
                if normalized:
                    resolver_lines.append(normalized)
        else:
            resolvers_raw_input = str(payload.get("dns_resolvers") or payload.get("resolvers") or "")
            for raw in resolvers_raw_input.splitlines():
                normalized = _normalize_resolver_line(raw)
                if normalized:
                    resolver_lines.append(normalized)

        if not resolver_lines:
            for raw in str(get_setting("dns_resolvers", "") or "").splitlines():
                normalized = _normalize_resolver_line(raw)
                if normalized:
                    resolver_lines.append(normalized)
        resolver_lines = list(dict.fromkeys(resolver_lines))
        if not resolver_lines:
            return jsonify({"ok": False, "error": "resolver_required"}), 400
        resolvers_raw = "\n".join(resolver_lines)

        domain_lines: list[str] = []
        domains_raw_input = str(payload.get("dns_domains") or get_setting("dns_domains", "") or "")
        for raw in domains_raw_input.splitlines():
            normalized = _normalize_dns_domain_line(raw)
            if normalized:
                domain_lines.append(normalized)
        domain_lines = list(dict.fromkeys(domain_lines))
        domains_raw = "\n".join(domain_lines)

        requested_domain = _normalize_domain_host(str(payload.get("domain") or ""))
        requested_password = str(payload.get("password") or "").strip()
        domain_targets = parse_dns_domains_text(domains_raw)
        if not requested_domain and domain_targets:
            requested_domain = domain_targets[0].domain
        if requested_domain and not requested_password:
            for item in domain_targets:
                if item.domain == requested_domain and item.password:
                    requested_password = item.password
                    break

        e2e_concurrency_raw = str(payload.get("e2e_concurrency") or "").strip()
        try:
            e2e_concurrency = int(e2e_concurrency_raw) if e2e_concurrency_raw else 8
        except Exception:
            e2e_concurrency = 8
        e2e_concurrency = max(1, min(32, e2e_concurrency))

        options = {
            "phase": "e2e",
            "scan_mode": "e2e",
            "domain": requested_domain,
            "password": requested_password,
            "resolvers_raw": resolvers_raw,
            "e2e_concurrency": e2e_concurrency,
        }

        with _RESOLVER_SCAN_JOBS_LOCK:
            _cleanup_resolver_scan_jobs_locked()
            active = _get_active_resolver_scan_job_locked()
            if active:
                payload_job = _resolver_scan_job_public(active)
                payload_job["reused"] = True
                return jsonify({"ok": True, "job": payload_job})

            job = _new_resolver_scan_job(options)
            _RESOLVER_SCAN_JOBS[str(job["id"])] = job
            _RESOLVER_SCAN_CONTROLS[str(job["id"])] = ResolverScanController()
            payload_job = _resolver_scan_job_public(job)

        threading.Thread(target=_run_resolver_e2e_job, args=(str(job["id"]), options), daemon=True).start()
        record_event(
            "resolver_e2e_requested",
            resolver_count=len(resolver_lines),
            domain=requested_domain,
            concurrency=e2e_concurrency,
        )
        return jsonify({"ok": True, "job": payload_job})

    @app.get("/dns/resolvers/scan/status")
    def dns_resolvers_scan_status():
        if not _settings_unlocked():
            return _settings_guard_failed()
        job_id = (request.args.get("id") or "").strip()
        payload_job: dict[str, object] | None = None
        with _RESOLVER_SCAN_JOBS_LOCK:
            _cleanup_resolver_scan_jobs_locked()
            job = _RESOLVER_SCAN_JOBS.get(job_id) if job_id else _get_active_resolver_scan_job_locked()
            if job:
                payload_job = _resolver_scan_job_public(job)
            elif not job_id and _RESOLVER_SCAN_JOBS:
                latest = max(_RESOLVER_SCAN_JOBS.values(), key=lambda x: float(x.get("started_at", 0) or 0))
                payload_job = _resolver_scan_job_public(latest)
        if payload_job:
            return jsonify({"ok": True, "job": payload_job})
        if job_id:
            return jsonify({"ok": False, "error": "not_found"}), 404
        persisted = _load_persisted_resolver_scan_snapshot()
        if persisted:
            return jsonify({"ok": True, "job": persisted, "persisted": True})
        return jsonify({"ok": False, "error": "not_found"}), 404

    @app.get("/dns/resolvers/scan/latest")
    def dns_resolvers_scan_latest():
        if not _settings_unlocked():
            return _settings_guard_failed()
        persisted = _load_persisted_resolver_scan_snapshot()
        if not persisted:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "job": persisted, "persisted": True})

    @app.post('/sync-now')
    def sync_now():
        payload = request.get_json(silent=True) or request.form or {}
        priority_channel = ""
        if payload:
            try:
                priority_channel = normalize_tg_s_url((payload.get("channel") or "").strip())
            except Exception:
                priority_channel = ""
        with _SYNC_JOBS_LOCK:
            _cleanup_sync_jobs_locked()
            active = _get_active_sync_job_locked()
            if active:
                payload = _sync_job_public(active)
                payload["reused"] = True
                return jsonify({"ok": True, "job": payload})

            job = _new_sync_job()
            _SYNC_JOBS[job["id"]] = job
            payload = _sync_job_public(job)

        threading.Thread(target=_run_sync_job, args=(job["id"], priority_channel), daemon=True).start()
        record_event("manual_sync_requested", job_id=job["id"])
        return jsonify({"ok": True, "job": payload})

    @app.get("/sync-now/status")
    def sync_now_status():
        job_id = (request.args.get("id") or "").strip()
        with _SYNC_JOBS_LOCK:
            _cleanup_sync_jobs_locked()
            job = _SYNC_JOBS.get(job_id) if job_id else _get_active_sync_job_locked()
            if not job:
                return jsonify({"ok": False, "error": "not_found"}), 404
            return jsonify({"ok": True, "job": _sync_job_public(job)})

    @app.get("/channel/state")
    def channel_state():
        selected = (request.args.get("channel") or "").strip() or None
        app_settings = all_settings()
        state = _load_index_state(selected, prefer_picker=False, app_settings=app_settings)
        selected_channel = state["selected_channel"]
        messages = state["messages"]
        latest_by_channel = state["latest_by_channel"]
        latest_by_source = state.get("latest_by_source") if isinstance(state.get("latest_by_source"), dict) else {}
        latest_id = 0
        if selected_channel:
            latest_id = int(latest_by_channel.get(selected_channel.id, 0) or 0)
        return jsonify(
            {
                "ok": True,
                "selected": state["selected"] or "",
                "latest_id": latest_id,
                "latest_by_source": latest_by_source,
                "message_count": len(messages),
                "search_disabled": not selected_channel or not messages,
                "header_html": render_template(
                    "_chat_header_primary.html",
                    selected_channel=selected_channel,
                ),
                "messages_html": render_template(
                    "_messages_panel.html",
                    channels=state["channels"],
                    messages=messages,
                    source_mode=state["source_mode"],
                    dns_domains_count=state["dns_domains_count"],
                ),
            }
        )

    @app.post('/channel/add')
    def add_channel():
        channels = _normalize_channel_list(request.form.get('channel') or '')
        domain = _normalize_domain_host(request.form.get('domain') or '')
        password = (request.form.get('password') or '').strip()
        mode = _normalize_source_mode(get_setting("source_mode", "dns"))
        if not channels:
            return redirect(url_for('index', msg="invalid_input"))
        dns_domain_lines = _load_dns_domain_lines()
        if mode == "dns" and not dns_domain_lines:
            return redirect(url_for('index'))

        if channels:
            current_direct = _normalize_channel_list(
                get_setting('direct_channels', "") or ""
            )
            merged = list(current_direct)
            for url in channels:
                if url not in merged:
                    merged.append(url)
            merged_csv = ",".join(merged)
            set_setting('direct_channels', merged_csv)
            set_setting('dns_client_channels', merged_csv)

            with SessionLocal() as db:
                for url in channels:
                    exists = db.scalar(select(Channel).where(Channel.source_url == url))
                    if exists:
                        continue
                    username = url.rsplit('/', 1)[-1]
                    db.add(
                        Channel(
                            username=username,
                            source_url=url,
                            title=username,
                            avatar_url="",
                            avatar_mime="",
                            avatar_b64="",
                        )
                    )
                db.commit()

        # Backward compatibility: if old UI posts domain/password in add-channel modal,
        # keep that domain in dns_domains.
        if domain:
            lines = _load_dns_domain_lines()
            entry = _normalize_dns_domain_line(f"{domain}|{password}" if password else domain)
            if entry:
                existing_domains = [x.split("|", 1)[0].strip().lower() for x in lines]
                if entry.split("|", 1)[0].strip().lower() in existing_domains:
                    # overwrite existing domain line with latest password if provided
                    lines = [x for x in lines if x.split("|", 1)[0].strip().lower() != entry.split("|", 1)[0].strip().lower()]
                lines.append(entry)
                set_setting("dns_domains", "\n".join(lines))

        # Optional immediate push when in DNS mode
        push_error = ""
        try:
            if mode == "dns" and channels:
                push_channels_to_domains(channels)
        except Exception as exc:
            push_error = f"dns_push_error:{exc}"

        # Run one immediate refresh so newly added channels fetch quickly in both modes.
        if channels or mode == "dns":
            def _run_sync_once() -> None:
                try:
                    sync_once(force_server_refresh=(mode == "dns"), priority_channel=(channels[0] if channels else ""))
                except Exception:
                    pass

            threading.Thread(target=_run_sync_once, daemon=True).start()

        base_msg = f"channels_added:{len(channels)}" if channels else "saved"
        if push_error:
            base_msg = f"{base_msg};{push_error}"
        if channels:
            if _request_prefers_channel_picker():
                return redirect(url_for('index', msg=base_msg))
            return redirect(url_for('index', channel=channels[0], msg=base_msg))
        return redirect(url_for('index', msg=base_msg))

    @app.post("/domain/add")
    def add_domain():
        domain = _normalize_domain_host(request.form.get("domain") or "")
        password = (request.form.get("password") or "").strip()
        entry = _normalize_dns_domain_line(f"{domain}|{password}" if password else domain)
        if not entry:
            return redirect(url_for("index"))

        lines = _load_dns_domain_lines()
        domain_key = entry.split("|", 1)[0].strip().lower()
        lines = [line for line in lines if line.split("|", 1)[0].strip().lower() != domain_key]
        lines.append(entry)
        set_setting("dns_domains", "\n".join(lines))
        return redirect(url_for("index"))

    @app.get("/")
    def index():
        selected = request.args.get("channel")
        prefer_picker = _request_prefers_channel_picker()
        ui_msg = request.args.get("msg", "")
        app_settings = all_settings()
        state = _load_index_state(selected, prefer_picker, app_settings)

        return render_template(
            "index.html",
            channels=state["channels"],
            selected=state["selected"],
            selected_channel=state["selected_channel"],
            messages=state["messages"],
            latest_by_channel=state["latest_by_channel"],
            app_settings=app_settings,
            source_mode=state["source_mode"],
            dns_domains_count=state["dns_domains_count"],
            ui_msg=ui_msg,
        )

    return app

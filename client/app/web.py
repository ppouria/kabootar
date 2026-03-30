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
from app.dns_bridge import export_resolver_health, probe_dns_domain, push_channels_to_domains, sync_from_dns_domain
from app.models import Channel, Message
from app.runtime_debug import record_event, runtime_summary, setup_logging, snapshot_events, tail_log_lines
from app.service import sync_once
from app.settings_store import all_settings, apply_sync_cron, get_setting, set_setting
from app.utils import normalize_tg_s_url, parse_csv
from app.versioning import app_meta

logger = logging.getLogger("kabootar.web")
_SYNC_JOBS_LOCK = threading.Lock()
_SYNC_JOBS: dict[str, dict] = {}
_SYNC_JOB_TTL_SECONDS = 1800
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
    return t.startswith("@") or t.startswith("http://") or t.startswith("https://") or ("t.me" in t)


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

    domain_norm = domain_part.rstrip(".").lower()
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
        elif request.path in {"/", "/settings", "/debug", "/debug/state"}:
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
            return redirect(url_for("settings_page", msg="Settings are locked. Enter password."))

        source_mode = _normalize_source_mode(request.form.get("source_mode"))
        set_setting("source_mode", source_mode)

        channels = _normalize_channel_list(request.form.get("direct_channels"))
        channels_csv = ",".join(channels)
        set_setting("direct_channels", channels_csv)
        # keep legacy key in sync for backward compatibility
        set_setting("dns_client_channels", channels_csv)

        fields = [
            "direct_proxies",
            "dns_password",
            "dns_client_id",
            "dns_resolvers",
            "dns_domains",
            "dns_query_size",
            "dns_timeout_seconds",
            "sync_interval_minutes",
        ]
        for f in fields:
            if f in request.form:
                set_setting(f, request.form.get(f, ""))

        # Canonicalize resolver list (one per line: resolver or resolver:port).
        resolver_lines: list[str] = []
        for raw in (request.form.get("dns_resolvers", "") or "").splitlines():
            normalized = _normalize_resolver_line(raw)
            if normalized:
                resolver_lines.append(normalized)
        resolver_lines = list(dict.fromkeys(resolver_lines))
        set_setting("dns_resolvers", "\n".join(resolver_lines))

        domain_lines: list[str] = []
        for raw in (request.form.get("dns_domains", "") or "").splitlines():
            normalized = _normalize_dns_domain_line(raw)
            if normalized:
                domain_lines.append(normalized)
        # de-duplicate while preserving order
        domain_lines = list(dict.fromkeys(domain_lines))
        domains_raw = "\n".join(domain_lines)
        set_setting("dns_domains", domains_raw)
        # Clear old route-based storage from active flow.
        set_setting("dns_channel_routes", "")
        # "Default domain/source" is removed from UI and active flow.
        set_setting("dns_domain", "")
        set_setting("dns_sources", "")

        set_setting("dns_use_system_resolver", "1" if request.form.get("dns_use_system_resolver") == "1" else "0")

        ok, out = apply_sync_cron(int(request.form.get("sync_interval_minutes", "1") or "1"))

        push_msg = ""
        if source_mode == "dns" and channels and domains_raw.strip():
            try:
                pushed = push_channels_to_domains(channels, domain_text=domains_raw)
                bad = [r for r in pushed.get("results", []) if not r.get("ok")]
                push_msg = "dns-push=ok" if not bad else f"dns-push=partial({len(bad)} failed)"
            except Exception as exc:
                push_msg = f"dns-push=error:{exc}"

        msg = "saved" if ok else f"saved (cron warning: {out})"
        if push_msg:
            msg = f"{msg}; {push_msg}"
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

    @app.post('/channel/add')
    def add_channel():
        channels = _normalize_channel_list(request.form.get('channel') or '')
        domain = (request.form.get('domain') or '').strip().rstrip('.').lower()
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
                    sync_once()
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
        domain = (request.form.get("domain") or "").strip().rstrip(".").lower()
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
        source_mode = _normalize_source_mode(app_settings.get("source_mode"))
        dns_domain_lines = _load_dns_domain_lines(app_settings.get("dns_domains"))
        configured_channels = _normalize_channel_list(app_settings.get("direct_channels", "") or "")
        configured_set = set(configured_channels)
        configured_order = {url: i for i, url in enumerate(configured_channels)}

        with SessionLocal() as db:
            # If channels are configured in settings, they are the source of truth
            # for sidebar visibility/order. If empty (domain-only DNS mode), fall
            # back to DB-discovered channels.
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

            if not selected and channels and not prefer_picker:
                selected = channels[0].source_url
            elif selected and configured_channels and selected not in configured_set:
                selected = None if prefer_picker else (channels[0].source_url if channels else None)

            selected_channel = None
            if selected:
                selected_channel = db.scalar(select(Channel).where(Channel.source_url == selected))

            latest_rows = db.execute(
                select(Message.channel_id, func.max(Message.message_id)).group_by(Message.channel_id)
            ).all()
            latest_by_channel = {row[0]: (row[1] or 0) for row in latest_rows}

            msgs = []
            if selected_channel:
                msgs = db.scalars(
                    select(Message)
                    .where(Message.channel_id == selected_channel.id)
                    .order_by(Message.message_id.desc())
                    .limit(120)
                ).all()
                msgs = list(reversed(msgs))

                # De-duplicate visually repeated posts (same normalized text in sequence)
                deduped = []
                prev_text_key = None
                for m in msgs:
                    text_key = " ".join((m.text or "").split())
                    # Skip only repeated pure-text duplicates; keep media posts.
                    if text_key and not m.has_media and not (m.photo_b64 or "").strip() and text_key == prev_text_key:
                        continue
                    deduped.append(m)
                    prev_text_key = text_key if text_key and not m.has_media and not (m.photo_b64 or "").strip() else None
                msgs = deduped

        return render_template(
            "index.html",
            channels=channels,
            selected=selected,
            selected_channel=selected_channel,
            messages=msgs,
            latest_by_channel=latest_by_channel,
            app_settings=app_settings,
            source_mode=source_mode,
            dns_domains_count=len(dns_domain_lines),
            ui_msg=ui_msg,
        )

    return app

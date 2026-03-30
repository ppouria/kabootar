from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import socket
import subprocess
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import dns.resolver
from dnslib import QTYPE, RCODE, RR, TXT
from dnslib.server import BaseResolver, DNSServer
from sqlalchemy import select

from app.db import SessionLocal, ensure_schema
from app.models import Channel, Message
from app.runtime_debug import record_event, setup_logging
from app.scraper import (
    fetch_html_with_proxies,
    fetch_photo_base64_with_proxies,
    parse_channel_meta,
    parse_recent_messages,
)
from app.settings_store import get_setting, set_setting
from app.text_packer import pack_text, unpack_text
from app.utils import (
    deserialize_photo_items,
    normalize_photo_items,
    normalize_tg_s_url,
    parse_csv,
    primary_photo_fields,
    serialize_photo_items,
)

logger = logging.getLogger("kabootar.dns")
ProgressCallback = Optional[Callable[[dict], None]]


def _emit_progress(progress: ProgressCallback, **payload) -> None:
    if not progress:
        return
    try:
        progress(dict(payload))
    except Exception:
        logger.exception("dns progress callback failed")


def _load_dns_channel_state() -> dict[str, dict[str, object]]:
    raw = (get_setting("dns_channel_state", "{}") or "{}").strip()
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def _save_dns_channel_state(data: dict[str, dict[str, object]]) -> None:
    items = list(data.items())
    if len(items) > 400:
        items.sort(key=lambda kv: int((kv[1] or {}).get("updated_at", 0)), reverse=True)
        items = items[:400]
    set_setting("dns_channel_state", json.dumps(dict(items), ensure_ascii=False, separators=(",", ":")))


def _utf8_len(value: str) -> int:
    return len((value or "").encode("utf-8"))


def _text_message_weight(message: dict) -> int:
    return (
        96
        + _utf8_len(message.get("text", ""))
        + _utf8_len(message.get("media_kind", ""))
        + _utf8_len(message.get("reply_author", ""))
        + _utf8_len(message.get("reply_text", ""))
        + _utf8_len(message.get("forward_source", ""))
    )


def _media_message_weight(message: dict) -> int:
    photos_json = (message.get("photos_json", "") or "").strip()
    return 64 + len(photos_json or (message.get("photo_b64", "") or "")) + _utf8_len(message.get("photo_mime", ""))


def _apply_batch_size(name: str, default: int, lower: int, upper: int) -> int:
    raw = (__import__("os").getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def _text_apply_batch_size() -> int:
    return _apply_batch_size("DNS_TEXT_APPLY_BATCH_SIZE", 5, 1, 20)


def _media_apply_batch_size() -> int:
    return _apply_batch_size("DNS_MEDIA_APPLY_BATCH_SIZE", 2, 1, 10)


def _normalize_photo_payload(message: dict) -> tuple[str, str, str]:
    photo_items = deserialize_photo_items(
        message.get("photos_json", "") or "",
        fallback_mime=message.get("photo_mime", "") or "",
        fallback_b64=message.get("photo_b64", "") or "",
    )
    photos_json = serialize_photo_items(photo_items)
    photo_mime, photo_b64 = primary_photo_fields(photo_items)
    return photos_json, photo_mime, photo_b64


def _fetch_photo_items(
    photo_urls: list[str],
    proxies: list[str],
    media_cache: dict[str, tuple[str, str]],
    *,
    timeout_seconds: int,
    max_photo_bytes: int,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for photo_url in photo_urls:
        if not photo_url:
            continue
        photo_mime = ""
        photo_b64 = ""
        if photo_url in media_cache:
            photo_mime, photo_b64 = media_cache[photo_url]
        else:
            try:
                fetched = fetch_photo_base64_with_proxies(
                    photo_url,
                    proxies,
                    attempts=2,
                    timeout_seconds=timeout_seconds,
                    retry_delay_seconds=5,
                    max_bytes=max_photo_bytes,
                )
            except Exception:
                fetched = None
            if fetched:
                photo_mime, photo_b64 = fetched
            media_cache[photo_url] = (photo_mime, photo_b64)
        if photo_b64:
            items.append({"mime": photo_mime, "b64": photo_b64})
    return normalize_photo_items(items)


def _normalize_avatar_payload(avatar_b64: str, avatar_mime: str) -> tuple[str, str]:
    payload = (avatar_b64 or "").strip()
    mime = (avatar_mime or "").strip()
    if not payload:
        return "", ""
    try:
        avatar_bytes = base64.b64decode(payload, validate=True)
        if not avatar_bytes:
            return "", ""
        if len(avatar_bytes) > 180_000:
            return "", ""
        return base64.b64encode(avatar_bytes).decode("ascii"), mime if mime.startswith("image/") else "image/jpeg"
    except Exception:
        return "", ""


def _ensure_channel_row(
    db,
    source_url: str,
    username: str = "",
    title: str = "",
    avatar_url: str = "",
    avatar_mime: str = "",
    avatar_b64: str = "",
) -> Channel:
    avatar_b64, avatar_mime = _normalize_avatar_payload(avatar_b64, avatar_mime)
    ch = db.scalar(select(Channel).where(Channel.source_url == source_url))
    if not ch:
        ch = Channel(
            username=(username or source_url.rsplit("/", 1)[-1]),
            source_url=source_url,
            title=title or source_url.rsplit("/", 1)[-1],
            avatar_url=avatar_url or "",
            avatar_mime=avatar_mime or "",
            avatar_b64=avatar_b64 or "",
        )
        db.add(ch)
        db.flush()
        return ch

    if title and ch.title != title:
        ch.title = title
    avatar_changed = bool(avatar_url and ch.avatar_url != avatar_url)
    if avatar_changed:
        ch.avatar_url = avatar_url
        if not avatar_b64:
            ch.avatar_mime = ""
            ch.avatar_b64 = ""
    if avatar_mime and ch.avatar_mime != avatar_mime:
        ch.avatar_mime = avatar_mime
    if avatar_b64 and ch.avatar_b64 != avatar_b64:
        ch.avatar_b64 = avatar_b64
    if username and ch.username != username:
        ch.username = username
    db.flush()
    return ch


def _channel_has_cached_avatar(db, source_url: str) -> bool:
    if not source_url:
        return False
    ch = db.scalar(select(Channel).where(Channel.source_url == source_url))
    return bool((getattr(ch, "avatar_b64", "") or "").strip()) if ch else False


def _upsert_text_message(db, channel_id: int, message: dict) -> bool:
    msg_id = int(message.get("message_id") or 0)
    if msg_id <= 0:
        return False

    published_at = message.get("published_at", "")
    text = message.get("text", "")
    has_media = bool(message.get("has_media"))
    media_kind = message.get("media_kind", "") or ""
    reply_to_message_id = int(message.get("reply_to_message_id") or 0) or None
    reply_author = message.get("reply_author", "") or ""
    reply_text = message.get("reply_text", "") or ""
    forward_source = message.get("forward_source", "") or ""

    existing = db.scalar(select(Message).where(Message.channel_id == channel_id, Message.message_id == msg_id))
    if existing:
        changed = False
        if existing.published_at != published_at:
            existing.published_at = published_at
            changed = True
        if existing.text != text:
            existing.text = text
            changed = True
        if bool(existing.has_media) != has_media:
            existing.has_media = has_media
            changed = True
        if existing.media_kind != media_kind:
            existing.media_kind = media_kind
            changed = True
        if existing.reply_to_message_id != reply_to_message_id:
            existing.reply_to_message_id = reply_to_message_id
            changed = True
        if existing.reply_author != reply_author:
            existing.reply_author = reply_author
            changed = True
        if existing.reply_text != reply_text:
            existing.reply_text = reply_text
            changed = True
        if existing.forward_source != forward_source:
            existing.forward_source = forward_source
            changed = True
        return changed

    db.add(
        Message(
            channel_id=channel_id,
            message_id=msg_id,
            published_at=published_at,
            text=text,
            has_media=has_media,
            media_kind=media_kind,
            photo_mime="",
            photo_b64="",
            reply_to_message_id=reply_to_message_id,
            reply_author=reply_author,
            reply_text=reply_text,
            forward_source=forward_source,
        )
    )
    return True


def _upsert_media_message(db, channel_id: int, message: dict) -> bool:
    msg_id = int(message.get("message_id") or 0)
    if msg_id <= 0:
        return False

    photos_json, photo_mime, photo_b64 = _normalize_photo_payload(message)
    has_media = bool(message.get("has_media")) or bool(photo_b64)
    media_kind = message.get("media_kind", "") or ("photo" if photo_b64 else "")

    existing = db.scalar(select(Message).where(Message.channel_id == channel_id, Message.message_id == msg_id))
    if existing:
        changed = False
        if bool(existing.has_media) != has_media:
            existing.has_media = has_media
            changed = True
        if media_kind and existing.media_kind != media_kind:
            existing.media_kind = media_kind
            changed = True
        if existing.photo_mime != photo_mime:
            existing.photo_mime = photo_mime
            changed = True
        if existing.photo_b64 != photo_b64:
            existing.photo_b64 = photo_b64
            changed = True
        if getattr(existing, "photos_json", "") != photos_json:
            existing.photos_json = photos_json
            changed = True
        return changed

    db.add(
        Message(
            channel_id=channel_id,
            message_id=msg_id,
            published_at="",
            text="",
            has_media=has_media,
            media_kind=media_kind,
            photo_mime=photo_mime,
            photo_b64=photo_b64,
            photos_json=photos_json,
            reply_to_message_id=None,
            reply_author="",
            reply_text="",
        )
    )
    return True


@dataclass
class BridgeConfig:
    domain: str
    port: int
    address: str = "0.0.0.0"
    ttl: int = 30
    refresh_seconds: int = 60
    recent_per_channel: int = 50
    cache_db: str = ":memory:"


def _safe_chunk_size(n: int) -> int:
    return max(16, min(220, n))


class BridgeCache:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.channels_override: list[str] = []
        self.payloads: dict[int, tuple[str, str]] = {}
        self.version: str = "0"
        self.count: int = 0

    def set_channels_override(self, channels: list[str]) -> None:
        clean = [normalize_tg_s_url(c) for c in channels if c and c.strip()]
        with self.lock:
            self.channels_override = clean

    def get_channels_override(self) -> list[str]:
        with self.lock:
            return list(self.channels_override)

    def refresh_from_telegram(self) -> None:
        channels_raw = get_setting("direct_channels", "") or ""
        channels = self.get_channels_override() or [normalize_tg_s_url(c) for c in parse_csv(channels_raw)]
        proxies_raw = get_setting("direct_proxies", "") or ""
        proxies = parse_csv(proxies_raw)
        now = int(time.time())
        max_photo_bytes = int(__import__("os").getenv("DNS_MEDIA_MAX_BYTES", "180000"))
        media_cache: dict[str, tuple[str, str]] = {}

        payloads_new: dict[int, tuple[str, str]] = {}
        for i, url in enumerate(channels, start=1):
            html = fetch_html_with_proxies(url, proxies, attempts=3, timeout_seconds=20, retry_delay_seconds=10)
            meta = parse_channel_meta(html)
            items = parse_recent_messages(html, limit=self.cfg.recent_per_channel)

            messages_payload: list[dict] = []
            for m in items:
                if int(m.get("message_id") or 0) <= 0:
                    continue
                photo_urls = [str(url).strip() for url in (m.get("photo_urls") or []) if str(url).strip()]
                if not photo_urls and str(m.get("photo_url") or "").strip():
                    photo_urls = [str(m.get("photo_url") or "").strip()]
                photo_items = _fetch_photo_items(
                    photo_urls,
                    proxies,
                    media_cache,
                    timeout_seconds=20,
                    max_photo_bytes=max_photo_bytes,
                )
                photo_mime, photo_b64 = primary_photo_fields(photo_items)
                photos_json = serialize_photo_items(photo_items)
                messages_payload.append(
                    {
                        "message_id": int(m.get("message_id") or 0),
                        "published_at": m.get("published_at", ""),
                        "text": m.get("text", ""),
                        "has_media": bool(m.get("has_media")) or bool(photo_b64),
                        "media_kind": m.get("media_kind", "") or ("photo" if photo_b64 else ""),
                        "photo_mime": photo_mime,
                        "photo_b64": photo_b64,
                        "photos_json": photos_json,
                        "reply_to_message_id": int(m.get("reply_to_message_id") or 0) or None,
                        "reply_author": m.get("reply_author", "") or "",
                        "reply_text": m.get("reply_text", "") or "",
                        "forward_source": m.get("forward_source", "") or "",
                    }
                )
            payload_obj = {
                "source_url": url,
                "username": url.rsplit("/", 1)[-1],
                "title": meta.get("title", ""),
                "avatar_url": meta.get("avatar_url", ""),
                "messages": messages_payload,
            }
            payload_json = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
            payload = pack_text(payload_json)
            crc = f"{zlib.crc32(payload.encode('utf-8')) & 0xffffffff:08x}"
            payloads_new[i] = (payload, crc)

        with self.lock:
            self.payloads = payloads_new
            self.version = str(now)
            self.count = len(channels)

    def get_meta(self) -> tuple[str, int]:
        with self.lock:
            return self.version, self.count

    def get_payload(self, idx: int) -> tuple[str, str] | None:
        with self.lock:
            return self.payloads.get(idx)


def _refresh_loop(cache: BridgeCache, sec: int):
    while True:
        try:
            cache.refresh_from_telegram()
            print("[dns-bridge] refreshed")
        except Exception as exc:
            print("[dns-bridge] refresh error:", exc)
        time.sleep(max(20, sec))


class BridgeResolver(BaseResolver):
    def __init__(self, cache: BridgeCache, cfg: BridgeConfig):
        self.cache = cache
        self.cfg = cfg
        self.domain = cfg.domain if cfg.domain.endswith(".") else cfg.domain + "."
        self.meta_re = re.compile(r"^meta$")
        self.chan_meta_re = re.compile(r"^chan\.(\d+)\.meta\.sz(\d+)$")
        self.chan_part_re = re.compile(r"^chan\.(\d+)\.part\.(\d+)\.sz(\d+)$")
        self.up_meta_re = re.compile(r"^upmeta\.(\d+)\.([0-9a-f]{8})$")
        self.up_part_re = re.compile(r"^uppart\.(\d+)\.([0-9a-f]+)$")
        self.up_commit_re = re.compile(r"^upcommit$")
        self.pending_total = 0
        self.pending_crc = ""
        self.pending_parts: dict[int, str] = {}

    def resolve(self, request, handler):
        reply = request.reply()
        qname_obj = request.q.qname
        qname = str(qname_obj).lower()
        qtype = QTYPE[request.q.qtype]
        if qtype != "TXT" or not qname.endswith(self.domain):
            return reply
        sub = qname[: -len(self.domain)].rstrip(".")

        try:
            m = self.up_meta_re.match(sub)
            if m:
                self.pending_total = max(1, int(m.group(1)))
                self.pending_crc = m.group(2)
                self.pending_parts = {}
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT("ok=1"), ttl=self.cfg.ttl))
                return reply

            m = self.up_part_re.match(sub)
            if m:
                idx = int(m.group(1))
                data = m.group(2)
                if idx >= 1:
                    self.pending_parts[idx] = data
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT("ok=1"), ttl=self.cfg.ttl))
                return reply

            if self.up_commit_re.match(sub):
                if self.pending_total <= 0:
                    reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT("ok=0;err=no_meta"), ttl=self.cfg.ttl))
                    return reply

                joined = "".join(self.pending_parts.get(i, "") for i in range(1, self.pending_total + 1))
                raw = bytes.fromhex(joined)
                crc = f"{zlib.crc32(raw) & 0xffffffff:08x}"
                if crc != self.pending_crc:
                    reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT("ok=0;err=crc"), ttl=self.cfg.ttl))
                    return reply

                payload = json.loads(raw.decode("utf-8", errors="ignore"))
                channels = payload.get("channels", []) if isinstance(payload, dict) else []
                self.cache.set_channels_override(channels if isinstance(channels, list) else [])
                # immediate refresh after config push
                self.cache.refresh_from_telegram()
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT("ok=1;applied=1"), ttl=self.cfg.ttl))
                return reply

            if self.meta_re.match(sub):
                ver, count = self.cache.get_meta()
                payload = f"v={ver};n={count}"
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT(payload), ttl=self.cfg.ttl))
                return reply

            m = self.chan_meta_re.match(sub)
            if m:
                idx = int(m.group(1))
                sz = _safe_chunk_size(int(m.group(2)))
                row = self.cache.get_payload(idx)
                if not row:
                    reply.header.rcode = RCODE.NXDOMAIN
                    return reply
                payload, crc = row
                b = payload.encode("utf-8")
                parts = (len(b) + sz - 1) // sz
                info = f"i={idx};sz={sz};parts={parts};len={len(b)};crc={crc}"
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT(info), ttl=self.cfg.ttl))
                return reply

            m = self.chan_part_re.match(sub)
            if m:
                idx = int(m.group(1))
                part = int(m.group(2))
                sz = _safe_chunk_size(int(m.group(3)))
                row = self.cache.get_payload(idx)
                if not row:
                    reply.header.rcode = RCODE.NXDOMAIN
                    return reply
                payload, _ = row
                b = payload.encode("utf-8")
                parts = (len(b) + sz - 1) // sz
                if part < 1 or part > parts:
                    reply.header.rcode = RCODE.NXDOMAIN
                    return reply
                chunk = b[(part - 1) * sz : (part * sz)]
                reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT([chunk]), ttl=self.cfg.ttl))
                return reply
        except Exception as exc:
            print("[dns-bridge] resolve error:", exc)
            reply.header.rcode = RCODE.SERVFAIL
            return reply

        return reply


def run_dns_bridge_server() -> None:
    domain = get_setting("dns_domain", "t.example.com") or "t.example.com"
    port = int(get_setting("dns_port", "5533") or 5533)
    cfg = BridgeConfig(domain=domain, port=port)
    cache = BridgeCache(cfg)
    cache.refresh_from_telegram()

    t = threading.Thread(target=_refresh_loop, args=(cache, cfg.refresh_seconds), daemon=True)
    t.start()

    resolver = BridgeResolver(cache, cfg)
    server = DNSServer(resolver, port=cfg.port, address=cfg.address)
    server.start_thread()
    print(f"[dns-bridge] running on {cfg.address}:{cfg.port} domain={cfg.domain}")
    while True:
        time.sleep(3600)


@dataclass
class DnsResolverTarget:
    server: str = ""
    port: int = 53
    use_system: bool = False

    @property
    def key(self) -> str:
        return "system" if self.use_system else f"{self.server}:{self.port}"


@dataclass
class DnsDomainTarget:
    domain: str
    password: str = ""


def _parse_dns_route_line(raw: str) -> tuple[str, str, str]:
    line = (raw or "").strip()
    if not line:
        return "", "", ""

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

    # Password belongs to a concrete channel+domain route.
    if password_norm and (not channel_norm or not domain_norm):
        password_norm = ""

    if not channel_norm and not domain_norm:
        return "", "", ""
    return channel_norm, domain_norm, password_norm


def _route_entries(route_text: str | None = None) -> list[tuple[str, str, str]]:
    raw = route_text if route_text is not None else (get_setting("dns_channel_routes", "") or "")
    out: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parsed = _parse_dns_route_line(line)
        if parsed[0] or parsed[1]:
            out.append(parsed)
    return out


def _looks_like_channel_token(token: str) -> bool:
    t = (token or "").strip().lower()
    if not t:
        return False
    return t.startswith("@") or t.startswith("http://") or t.startswith("https://") or ("t.me" in t)


def _parse_dns_domain_line(raw: str) -> tuple[str, str]:
    line = (raw or "").strip()
    if not line:
        return "", ""

    parts = [x.strip() for x in line.split("|")]
    if len(parts) == 1:
        domain = parts[0]
        password = ""
    else:
        first = parts[0]
        second = parts[1] if len(parts) >= 2 else ""
        if not first:
            # Legacy: |domain|password
            domain = second
            password = "|".join(parts[2:]).strip() if len(parts) >= 3 else ""
        elif _looks_like_channel_token(first):
            # Legacy route: channel|domain|password
            domain = second
            password = "|".join(parts[2:]).strip() if len(parts) >= 3 else ""
        else:
            # New format: domain|password
            domain = first
            password = "|".join(parts[1:]).strip() if len(parts) >= 2 else ""

    domain_norm = domain.rstrip(".").lower().strip()
    if not domain_norm:
        return "", ""
    return domain_norm, password.strip()


def _domain_entries(domain_text: str | None = None) -> list[tuple[str, str]]:
    raw = domain_text if domain_text is not None else (get_setting("dns_domains", "") or "")
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parsed = _parse_dns_domain_line(line)
        if parsed[0]:
            out.append(parsed)
    return out


def load_dns_domains(route_text: str | None = None, domain_text: str | None = None) -> list[DnsDomainTarget]:
    ordered: dict[str, str] = {}

    domain_rows = _domain_entries(domain_text)
    if domain_rows:
        for domain, password in domain_rows:
            if domain not in ordered:
                ordered[domain] = ""
            if password:
                ordered[domain] = password
    else:
        # Backward compatibility with old dns_channel_routes storage.
        for _channel, domain, password in _route_entries(route_text):
            if not domain:
                continue
            if domain not in ordered:
                ordered[domain] = ""
            if password:
                ordered[domain] = password

    return [DnsDomainTarget(domain=d, password=pw) for d, pw in ordered.items()]


def _parse_resolver_target(raw: str) -> DnsResolverTarget | None:
    token = (raw or "").strip()
    if not token:
        return None

    token = token.replace("dns://", "").strip()
    host = token
    port = 53

    if token.startswith("[") and "]" in token:
        end = token.find("]")
        host = token[1:end].strip()
        rest = token[end + 1 :].strip()
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    elif token.count(":") == 1:
        h, p = token.rsplit(":", 1)
        if p.isdigit():
            host = h.strip()
            port = int(p)
    elif "," in token:
        parts = [x.strip() for x in token.split(",")]
        # Legacy format: domain,server,port,use_system
        if len(parts) >= 2:
            host = parts[1] or parts[0]
        if len(parts) >= 3 and parts[2].isdigit():
            port = int(parts[2])

    host = host.strip().strip("[]")
    if not host:
        return None
    port = max(1, min(65535, int(port)))
    return DnsResolverTarget(server=host, port=port, use_system=False)


def _legacy_resolvers() -> list[DnsResolverTarget]:
    out: list[DnsResolverTarget] = []
    raw_sources = (get_setting("dns_sources", "") or "").strip()
    for line in raw_sources.splitlines():
        parsed = _parse_resolver_target(line)
        if parsed:
            out.append(parsed)

    server = (get_setting("dns_server", "") or "").strip()
    port_raw = (get_setting("dns_port", "53") or "53").strip()
    if server:
        try:
            port = int(port_raw)
        except Exception:
            port = 53
        out.append(DnsResolverTarget(server=server, port=max(1, min(65535, port)), use_system=False))

    dedup: dict[str, DnsResolverTarget] = {}
    for item in out:
        dedup[item.key] = item
    return list(dedup.values())


def load_dns_resolvers() -> list[DnsResolverTarget]:
    use_system = (get_setting("dns_use_system_resolver", "1") or "1") == "1"
    if use_system:
        return [DnsResolverTarget(use_system=True)]

    out: list[DnsResolverTarget] = []
    raw = (get_setting("dns_resolvers", "") or "").strip()
    for line in raw.splitlines():
        parsed = _parse_resolver_target(line)
        if parsed:
            out.append(parsed)

    if not out:
        out = _legacy_resolvers()
    if not out:
        out = [DnsResolverTarget(use_system=True)]

    dedup: dict[str, DnsResolverTarget] = {}
    for item in out:
        dedup[item.key] = item
    return list(dedup.values())


_RESOLVER_HEALTH_LOCK = threading.Lock()
_RESOLVER_HEALTH: dict[str, dict[str, float]] = {}


def _resolver_health(target: DnsResolverTarget) -> dict[str, float]:
    key = target.key
    with _RESOLVER_HEALTH_LOCK:
        state = _RESOLVER_HEALTH.get(key)
        if state is None:
            state = {"ok": 0.0, "fail": 0.0, "ema_rtt": 0.45}
            _RESOLVER_HEALTH[key] = state
        return dict(state)


def _record_resolver_result(target: DnsResolverTarget, ok: bool, rtt: float | None = None) -> None:
    key = target.key
    with _RESOLVER_HEALTH_LOCK:
        state = _RESOLVER_HEALTH.setdefault(key, {"ok": 0.0, "fail": 0.0, "ema_rtt": 0.45})
        if ok:
            state["ok"] += 1.0
            state["fail"] = max(0.0, state["fail"] - 0.25)
            if rtt is not None and rtt > 0:
                state["ema_rtt"] = (state["ema_rtt"] * 0.7) + (rtt * 0.3)
        else:
            state["fail"] += 1.0
            if rtt is not None and rtt > 0:
                state["ema_rtt"] = (state["ema_rtt"] * 0.8) + (rtt * 0.2)


def export_resolver_health() -> dict[str, dict[str, float]]:
    with _RESOLVER_HEALTH_LOCK:
        return {k: dict(v) for k, v in _RESOLVER_HEALTH.items()}


def _resolver_score(target: DnsResolverTarget) -> float:
    h = _resolver_health(target)
    fail_penalty = h.get("fail", 0.0) * 1.5
    latency = max(0.05, h.get("ema_rtt", 0.45))
    cold_start_penalty = 0.4 if h.get("ok", 0.0) < 2 else 0.0
    return fail_penalty + latency + cold_start_penalty


def _ordered_resolvers(targets: list[DnsResolverTarget]) -> list[DnsResolverTarget]:
    dedup: dict[str, DnsResolverTarget] = {}
    for t in targets:
        dedup[t.key] = t
    return sorted(dedup.values(), key=_resolver_score)


def _resolver_for_target(target: DnsResolverTarget) -> dns.resolver.Resolver:
    timeout_seconds = _dns_timeout_seconds()
    lifetime_seconds = min(60.0, max(timeout_seconds + 1.6, timeout_seconds * 1.9))
    if target.use_system:
        r = dns.resolver.Resolver(configure=True)
        r.timeout = timeout_seconds
        r.lifetime = lifetime_seconds
        return r

    r = dns.resolver.Resolver(configure=False)
    r.nameservers = [target.server]
    r.nameserver_ports = {target.server: target.port}
    r.timeout = timeout_seconds
    r.lifetime = lifetime_seconds
    return r


def _dns_timeout_seconds() -> float:
    raw = (get_setting("dns_timeout_seconds", "3") or "3").strip()
    try:
        value = float(raw)
    except Exception:
        value = 3.0
    return max(1.0, min(30.0, value))


def _query_retry_count() -> int:
    raw = (get_setting("dns_query_retries", "4") or "4").strip()
    try:
        n = int(raw)
    except Exception:
        n = 4
    return max(1, min(6, n))


def _txt_answer_bytes(ans) -> list[bytes]:
    out: list[bytes] = []
    for rr in ans:
        out.append(b"".join(rr.strings))
    return out


def _is_windows_dns_permission_error(exc: Exception) -> bool:
    if __import__("os").name != "nt":
        return False
    text = str(exc or "").lower()
    return "10013" in text or "access permissions" in text or isinstance(exc, PermissionError)


def _query_txt_via_nslookup(name: str, target: DnsResolverTarget) -> list[bytes]:
    if __import__("os").name != "nt":
        raise RuntimeError("nslookup_fallback_not_supported")
    if not target.use_system and int(target.port or 53) != 53:
        raise RuntimeError("nslookup_fallback_requires_port_53")

    cmd = ["nslookup", "-type=TXT", name]
    if not target.use_system and target.server:
        cmd.append(target.server)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(3.0, min(45.0, _dns_timeout_seconds() + 1.0)),
        check=False,
    )
    output = "\n".join(x for x in [proc.stdout, proc.stderr] if x)
    lowered = output.lower()
    if "non-existent domain" in lowered or "nxdomain" in lowered or "can't find" in lowered:
        raise dns.resolver.NXDOMAIN

    chunks = re.findall(r'"([^"]*)"', output, flags=re.S)
    if chunks:
        logger.info(
            "dns nslookup fallback name=%s target=%s chunks=%s",
            name,
            ("system" if target.use_system else f"{target.server}:{target.port}"),
            len(chunks),
        )
        return ["".join(chunks).encode("utf-8")]

    if "timed out" in lowered or "no servers could be reached" in lowered:
        raise RuntimeError(f"nslookup_timeout:{output.strip()}")
    if not chunks:
        raise RuntimeError(f"nslookup_no_txt:{output.strip()}")
    return ["".join(chunks).encode("utf-8")]


def _supports_windows_nslookup(target: DnsResolverTarget) -> bool:
    return __import__("os").name == "nt" and (target.use_system or int(target.port or 53) == 53)


def _public_fallback_resolvers(targets: list[DnsResolverTarget]) -> list[DnsResolverTarget]:
    extras: list[DnsResolverTarget] = []
    if __import__("os").name == "nt":
        extras.extend(
            [
                DnsResolverTarget(server="1.1.1.1", port=53, use_system=False),
                DnsResolverTarget(server="8.8.8.8", port=53, use_system=False),
                DnsResolverTarget(use_system=True),
            ]
        )
    dedup: dict[str, DnsResolverTarget] = {}
    for item in [*extras, *targets]:
        dedup[item.key] = item
    return list(dedup.values())


def _query_txt_single(name: str, target: DnsResolverTarget, retries: int | None = None) -> list[bytes]:
    tries = retries if retries is not None else _query_retry_count()
    if _supports_windows_nslookup(target):
        tries = 1
    last_exc: Exception | None = None
    for attempt in range(max(1, tries)):
        if _supports_windows_nslookup(target):
            start_lookup = time.perf_counter()
            try:
                out = _query_txt_via_nslookup(name, target)
                _record_resolver_result(target, True, time.perf_counter() - start_lookup)
                return out
            except dns.resolver.NXDOMAIN:
                _record_resolver_result(target, False, time.perf_counter() - start_lookup)
                raise
            except Exception as exc:
                last_exc = exc
                _record_resolver_result(target, False, time.perf_counter() - start_lookup)
                if attempt < tries - 1:
                    time.sleep(0.12 * (attempt + 1))
                continue

        resolver = _resolver_for_target(target)

        start_udp = time.perf_counter()
        try:
            ans = resolver.resolve(name, "TXT")
            out = _txt_answer_bytes(ans)
            _record_resolver_result(target, True, time.perf_counter() - start_udp)
            return out
        except dns.resolver.NXDOMAIN:
            _record_resolver_result(target, False, time.perf_counter() - start_udp)
            raise
        except Exception as exc:
            last_exc = exc
            _record_resolver_result(target, False, time.perf_counter() - start_udp)

        start_tcp = time.perf_counter()
        try:
            ans = resolver.resolve(name, "TXT", tcp=True)
            out = _txt_answer_bytes(ans)
            _record_resolver_result(target, True, time.perf_counter() - start_tcp)
            return out
        except dns.resolver.NXDOMAIN:
            _record_resolver_result(target, False, time.perf_counter() - start_tcp)
            raise
        except Exception as exc:
            last_exc = exc
            _record_resolver_result(target, False, time.perf_counter() - start_tcp)
            if _is_windows_dns_permission_error(exc):
                start_fallback = time.perf_counter()
                try:
                    out = _query_txt_via_nslookup(name, target)
                    _record_resolver_result(target, True, time.perf_counter() - start_fallback)
                    return out
                except dns.resolver.NXDOMAIN:
                    _record_resolver_result(target, False, time.perf_counter() - start_fallback)
                    raise
                except Exception as fallback_exc:
                    last_exc = fallback_exc
                    _record_resolver_result(target, False, time.perf_counter() - start_fallback)
            if attempt < tries - 1:
                time.sleep(0.12 * (attempt + 1))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("dns_query_failed:unknown")


def _query_txt_sequential(name: str, ordered: list[DnsResolverTarget]) -> list[bytes]:
    last_exc: Exception | None = None
    for target in ordered:
        try:
            return _query_txt_single(name, target)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"dns_query_failed:{name}:{last_exc}")


def _query_txt_parallel(name: str, primary: list[DnsResolverTarget], spares: list[DnsResolverTarget]) -> list[bytes]:
    last_exc: Exception | None = None
    with ThreadPoolExecutor(max_workers=len(primary)) as executor:
        futures = {executor.submit(_query_txt_single, name, target): target for target in primary}
        for future in as_completed(futures):
            try:
                return future.result()
            except Exception as exc:
                last_exc = exc

    try:
        return _query_txt_sequential(name, primary)
    except Exception as exc:
        last_exc = exc

    if spares:
        return _query_txt_sequential(name, spares)
    raise RuntimeError(f"dns_query_failed_parallel:{name}:{last_exc}")


def _should_parallel(ordered: list[DnsResolverTarget]) -> bool:
    if __import__("os").name == "nt":
        return False
    if len(ordered) < 2:
        return False
    best = _resolver_health(ordered[0])
    if best.get("ok", 0.0) < 2:
        return True
    if best.get("fail", 0.0) > 0:
        return True
    if best.get("ema_rtt", 0.45) > 0.85:
        return True
    return False


def _query_txt(name: str, resolvers: list[DnsResolverTarget] | None = None) -> list[bytes]:
    ordered = _ordered_resolvers(_public_fallback_resolvers(resolvers or load_dns_resolvers()))
    if not ordered:
        ordered = [DnsResolverTarget(use_system=True)]

    if _should_parallel(ordered):
        fanout = min(3, len(ordered))
        return _query_txt_parallel(name, ordered[:fanout], ordered[fanout:])
    return _query_txt_sequential(name, ordered)


def _effective_query_size() -> int:
    raw = (get_setting("dns_query_size", "220") or "220").strip()
    try:
        requested = int(raw)
    except Exception:
        requested = 220

    # Legacy default was 60 before media transfer; with photo payloads this
    # explodes request count and causes resolver timeouts.
    if requested <= 60:
        requested = 220
    return _safe_chunk_size(requested)


def _meta_retry_count() -> int:
    raw = (get_setting("dns_meta_retries", "2") or "2").strip()
    try:
        n = int(raw)
    except Exception:
        n = 2
    return max(1, min(5, n))


def _query_meta(
    domain: str,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> dict[str, str]:
    last_exc: Exception | None = None
    for attempt in range(_meta_retry_count()):
        try:
            meta_raw = _parse_txt(_query_txt(f"meta.{client_id}.{session}.{domain}", resolver_targets)[0])
            return _ensure_ok(meta_raw, f"meta:{domain}")
        except Exception as exc:
            last_exc = exc
            if "dns_query_failed:" not in str(exc) and "dns_query_failed_parallel:" not in str(exc):
                raise
            if attempt < _meta_retry_count() - 1:
                time.sleep(0.2 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"meta:{domain}:unknown")


def _chunk_fetch_workers(resolvers: list[DnsResolverTarget], n_parts: int) -> int:
    if n_parts < 24:
        return 1
    resolver_count = max(1, len(resolvers))
    # Keep concurrency moderate to avoid recursive resolver throttling.
    return max(2, min(6, resolver_count * 2))


def _query_payload_chunk(
    qname: str,
    context: str,
    resolver_targets: list[DnsResolverTarget],
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            chunk = _query_txt(qname, resolver_targets)[0]
            chunk_text = _parse_txt(chunk)
            if chunk_text.startswith("ok=0;"):
                _ensure_ok(chunk_text, context)
            return chunk
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.18 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{context}:unknown")


def _fetch_payload_parts(
    n_parts: int,
    resolver_targets: list[DnsResolverTarget],
    loader,
    missing_context: str,
) -> bytearray:
    workers = _chunk_fetch_workers(resolver_targets, n_parts)
    if workers <= 1:
        buf = bytearray()
        for p in range(1, n_parts + 1):
            buf.extend(loader(p))
        return buf

    out: list[bytes | None] = [None] * (n_parts + 1)
    batch_size = workers * 4
    for start in range(1, n_parts + 1, batch_size):
        end = min(n_parts, start + batch_size - 1)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(loader, p): p for p in range(start, end + 1)}
            for fut in as_completed(futures):
                p = futures[fut]
                out[p] = fut.result()

    buf = bytearray()
    for p in range(1, n_parts + 1):
        chunk = out[p]
        if chunk is None:
            raise RuntimeError(f"{missing_context}:{p}")
        buf.extend(chunk)
    return buf


def _query_channel_part(
    domain: str,
    channel_idx: int,
    part_idx: int,
    qsz: int,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> bytes:
    qname = f"chan.{channel_idx}.part.{part_idx}.sz{qsz}.{client_id}.{session}.{domain}"
    return _query_payload_chunk(qname, f"chan_part:{domain}:{channel_idx}:{part_idx}", resolver_targets)


def _fetch_channel_payload(
    domain: str,
    channel_idx: int,
    n_parts: int,
    qsz: int,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> bytearray:
    return _fetch_payload_parts(
        n_parts,
        resolver_targets,
        lambda p: _query_channel_part(domain, channel_idx, p, qsz, client_id, session, resolver_targets),
        f"missing_chunk:{domain}:{channel_idx}",
    )


def _query_stage_bundle_meta(
    domain: str,
    channel_idx: int,
    stage: str,
    bundle_idx: int,
    qsz: int,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> dict[str, str]:
    qname = f"chan.{channel_idx}.{stage}.meta.{bundle_idx}.sz{qsz}.{client_id}.{session}.{domain}"
    raw = _parse_txt(_query_txt(qname, resolver_targets)[0])
    return _ensure_ok(raw, f"chan_{stage}_meta:{domain}:{channel_idx}:{bundle_idx}")


def _query_stage_bundle_part(
    domain: str,
    channel_idx: int,
    stage: str,
    bundle_idx: int,
    part_idx: int,
    qsz: int,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> bytes:
    qname = f"chan.{channel_idx}.{stage}.part.{bundle_idx}.{part_idx}.sz{qsz}.{client_id}.{session}.{domain}"
    return _query_payload_chunk(
        qname,
        f"chan_{stage}_part:{domain}:{channel_idx}:{bundle_idx}:{part_idx}",
        resolver_targets,
    )


def _fetch_stage_bundle_payload(
    domain: str,
    channel_idx: int,
    stage: str,
    bundle_idx: int,
    n_parts: int,
    qsz: int,
    client_id: str,
    session: str,
    resolver_targets: list[DnsResolverTarget],
) -> bytearray:
    return _fetch_payload_parts(
        n_parts,
        resolver_targets,
        lambda p: _query_stage_bundle_part(domain, channel_idx, stage, bundle_idx, p, qsz, client_id, session, resolver_targets),
        f"missing_{stage}_chunk:{domain}:{channel_idx}:{bundle_idx}",
    )


def _parse_kv(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in (raw or "").split(";"):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_txt(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore").strip()
    return str(raw).strip()


def _ensure_ok(raw: bytes | str, context: str) -> dict[str, str]:
    text = _parse_txt(raw)
    parts = _parse_kv(text)
    if parts.get("ok") == "0":
        raise RuntimeError(f"{context}:{parts.get('err', 'unknown')}")
    return parts


_SESSION_LOCK = threading.Lock()
_SESSION_CACHE: dict[tuple[str, str], tuple[str, str, float]] = {}


def _sanitize_client_id(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "", (value or "").strip().lower())
    if len(cleaned) < 3:
        return ""
    return cleaned[:32]


def _dns_client_id() -> str:
    current = _sanitize_client_id(get_setting("dns_client_id", "") or "")
    if current:
        return current
    seed = f"{socket.gethostname()}-{time.time_ns()}"
    generated = "c" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:15]
    set_setting("dns_client_id", generated)
    return generated


def _effective_password(password: str | None) -> str:
    candidate = (password or "").strip()
    if candidate:
        return candidate
    return (get_setting("dns_password", "") or "").strip()


def _get_session_for_domain(domain: str, password: str, resolvers: list[DnsResolverTarget]) -> tuple[str, str]:
    domain_norm = domain.rstrip(".").lower()
    password = _effective_password(password)
    password_sig = hashlib.sha1(password.encode("utf-8")).hexdigest()
    cache_key = (domain_norm, password_sig)
    now = time.time()
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(cache_key)
        if cached and cached[2] > now + 10:
            return cached[0], cached[1]

    client_id = _dns_client_id()
    auth_raw = _query_txt(f"auth.{client_id}.{password_sig}.{domain_norm}", resolvers)[0]
    auth_parts = _ensure_ok(auth_raw, f"dns_auth:{domain_norm}")

    session = re.sub(r"[^a-z0-9]+", "", (auth_parts.get("s", "public") or "public").lower())
    if len(session) < 3:
        session = "public"
    ttl_raw = auth_parts.get("ttl", "3600") or "3600"
    try:
        ttl = int(ttl_raw)
    except Exception:
        ttl = 3600
    ttl = max(60, min(86400, ttl))

    with _SESSION_LOCK:
        _SESSION_CACHE[cache_key] = (client_id, session, now + ttl)
    return client_id, session


def _invalidate_session(domain: str, password: str = "") -> None:
    domain_norm = domain.rstrip(".").lower()
    password = _effective_password(password)
    password_sig = hashlib.sha1(password.encode("utf-8")).hexdigest()
    with _SESSION_LOCK:
        _SESSION_CACHE.pop((domain_norm, password_sig), None)


def push_channels_to_dns_server(
    channels: list[str],
    domain: str,
    password: str = "",
    resolvers: list[DnsResolverTarget] | None = None,
) -> dict:
    domain = (domain or "").strip().rstrip(".")
    if not domain:
        return {"ok": False, "response": "missing_domain", "channels": 0, "domain": ""}

    resolver_targets = resolvers or load_dns_resolvers()
    clean = [normalize_tg_s_url(c) for c in channels if c and c.strip()]
    if not clean:
        return {"ok": True, "response": "ok=1;skipped=no_channels", "channels": 0, "domain": domain}

    payload = json.dumps({"channels": clean}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_hex = payload.hex()
    crc = f"{zlib.crc32(payload) & 0xffffffff:08x}"
    chunk_size = 48
    chunks = [payload_hex[i : i + chunk_size] for i in range(0, len(payload_hex), chunk_size)] or [""]
    nonce = f"n{secrets.token_hex(4)}"

    for attempt in range(2):
        client_id, session = _get_session_for_domain(domain, password, resolver_targets)
        try:
            _ensure_ok(_query_txt(f"upmeta.{nonce}.{len(chunks)}.{crc}.{client_id}.{session}.{domain}", resolver_targets)[0], f"upmeta:{domain}")
            for i, ch in enumerate(chunks, start=1):
                _ensure_ok(_query_txt(f"uppart.{nonce}.{i}.{ch}.{client_id}.{session}.{domain}", resolver_targets)[0], f"uppart:{domain}:{i}")
            resp = _parse_txt(_query_txt(f"upcommit.{nonce}.{client_id}.{session}.{domain}", resolver_targets)[0])
            parts = _ensure_ok(resp, f"upcommit:{domain}")
            return {
                "ok": parts.get("ok") == "1",
                "response": resp,
                "channels": len(clean),
                "domain": domain,
                "client_id": client_id,
                "nonce": nonce,
            }
        except RuntimeError as exc:
            if str(exc).endswith(":auth") and attempt == 0:
                _invalidate_session(domain, password)
                continue
            raise

    return {"ok": False, "response": "push_failed", "channels": len(clean), "domain": domain}


def push_channels_to_domains(channels: list[str], domain_text: str | None = None) -> dict:
    domain_targets = load_dns_domains(domain_text=domain_text)
    if not domain_targets:
        return {"results": [], "domains": 0, "skipped": "no_domains"}

    results = []
    resolvers = load_dns_resolvers()
    for target in domain_targets:
        try:
            results.append(
                push_channels_to_dns_server(
                    channels,
                    domain=target.domain,
                    password=target.password,
                    resolvers=resolvers,
                )
            )
        except Exception as exc:
            results.append({"ok": False, "domain": target.domain, "response": str(exc)})
    return {"results": results, "domains": len(domain_targets)}


def push_channel_routes(route_text: str) -> dict:
    grouped: dict[str, dict[str, object]] = {}
    for channel, domain, password in _route_entries(route_text):
        if not domain:
            continue
        state = grouped.setdefault(domain, {"channels": [], "password": ""})
        if password:
            state["password"] = password
        if channel and channel not in state["channels"]:
            state["channels"].append(channel)

    results = []
    resolvers = load_dns_resolvers()
    for domain, state in grouped.items():
        channels = list(state.get("channels", []))
        password = str(state.get("password", "") or "")
        if not channels:
            results.append({"ok": True, "domain": domain, "response": "skipped_no_channels", "channels": 0})
            continue
        try:
            results.append(push_channels_to_dns_server(channels, domain=domain, password=password, resolvers=resolvers))
        except Exception as exc:
            results.append({"ok": False, "domain": domain, "response": str(exc)})
    return {"results": results}


def _sync_staged_channel(
    db,
    *,
    domain: str,
    channel_index: int,
    channels_total: int,
    client_id: str,
    session: str,
    qsz: int,
    resolver_targets: list[DnsResolverTarget],
    info: dict[str, str],
    cached: dict[str, object],
    channel_state: dict[str, dict[str, object]] | None,
    state_key: str,
    progress: ProgressCallback,
) -> tuple[int, int, str]:
    cached_source = normalize_tg_s_url(cached.get("source_url") or "") if (cached.get("source_url") or "") else ""
    text_bundle_total = int(info.get("tb", "0") or 0)
    media_bundle_total = int(info.get("mb", "0") or 0)
    text_total = int(info.get("tm", "0") or 0)
    media_total = int(info.get("mm", "0") or 0)
    text_crc = str(info.get("tc", "") or "")
    media_crc = str(info.get("mc", "") or "")
    work_total = text_total + media_total
    cached_text_crc = str(cached.get("text_crc", "") or "")
    cached_media_crc = str(cached.get("media_crc", "") or "")
    cached_work_total = int(cached.get("message_total", 0) or 0) + int(cached.get("media_total", 0) or 0)

    has_cached_avatar = _channel_has_cached_avatar(db, cached_source)
    if cached_source and cached_text_crc == text_crc and cached_media_crc == media_crc and has_cached_avatar:
        _emit_progress(
            progress,
            kind="channel_plan",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            source_url=cached_source,
            message_total=cached_work_total or work_total,
        )
        _emit_progress(
            progress,
            kind="channel_done",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            source_url=cached_source,
            message_total=cached_work_total or work_total,
            message_done=cached_work_total or work_total,
            channel_saved=0,
        )
        return 0, cached_work_total or work_total, cached_source

    source_url = cached_source
    text_done = 0
    media_done = 0
    channel_saved = 0
    ch = None
    need_text = not (cached_source and cached_text_crc == text_crc)
    need_media = not (cached_source and cached_media_crc == media_crc)
    if cached_source and not has_cached_avatar and text_bundle_total > 0:
        need_text = True
    if not need_text and cached_source:
        ch = db.scalar(select(Channel).where(Channel.source_url == cached_source))
        if ch is None and text_bundle_total > 0:
            need_text = True

    if source_url:
        _emit_progress(
            progress,
            kind="channel_plan",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            source_url=source_url,
            message_total=work_total,
        )

    if need_text:
        for bundle_idx in range(1, text_bundle_total + 1):
            bundle_info = _query_stage_bundle_meta(domain, channel_index, "text", bundle_idx, qsz, client_id, session, resolver_targets)
            n_parts = int(bundle_info.get("parts", "0") or 0)
            if n_parts <= 0:
                continue
            buf = _fetch_stage_bundle_payload(domain, channel_index, "text", bundle_idx, n_parts, qsz, client_id, session, resolver_targets)
            bundle_payload = json.loads(unpack_text(buf.decode("utf-8", errors="ignore")))
            source_url = normalize_tg_s_url(bundle_payload.get("source_url") or bundle_payload.get("username") or "")
            if not source_url:
                continue
            ch = _ensure_channel_row(
                db,
                source_url=source_url,
                username=(bundle_payload.get("username") or source_url.rsplit("/", 1)[-1]),
                title=(bundle_payload.get("title", "") or source_url.rsplit("/", 1)[-1]),
                avatar_url=bundle_payload.get("avatar_url", ""),
                avatar_mime=bundle_payload.get("avatar_mime", ""),
                avatar_b64=bundle_payload.get("avatar_b64", ""),
            )
            _emit_progress(
                progress,
                kind="channel_plan",
                mode="dns",
                domain=domain,
                channel_index=channel_index,
                channels_total=channels_total,
                source_url=source_url,
                message_total=work_total,
            )
            bundle_messages = sorted(
                [m for m in (bundle_payload.get("messages", []) or []) if int(m.get("message_id") or 0) > 0],
                key=lambda item: (_text_message_weight(item), int(item.get("message_id") or 0)),
            )
            dirty = 0
            for local_index, message in enumerate(bundle_messages, start=1):
                if _upsert_text_message(db, ch.id, message):
                    channel_saved += 1
                dirty += 1
                done_count = text_done + local_index
                if done_count == text_total or done_count % 5 == 0:
                    _emit_progress(
                        progress,
                        kind="channel_progress",
                        mode="dns",
                        domain=domain,
                        channel_index=channel_index,
                        channels_total=channels_total,
                        source_url=source_url,
                        message_total=work_total,
                        message_done=done_count,
                        channel_saved=channel_saved,
                    )
                if dirty >= _text_apply_batch_size():
                    db.commit()
                    dirty = 0
            if dirty or not bundle_messages:
                db.commit()
            text_done += len(bundle_messages)

        if channel_state is not None and source_url:
            entry = channel_state.setdefault(state_key, {})
            entry.update(
                {
                    "text_crc": text_crc,
                    "source_url": source_url,
                    "message_total": text_total,
                    "media_total": int(entry.get("media_total", 0) or media_total),
                    "updated_at": int(time.time()),
                }
            )
    else:
        text_done = text_total

    if source_url and ch is None:
        ch = db.scalar(select(Channel).where(Channel.source_url == source_url))

    if need_media and media_bundle_total > 0:
        if not source_url and cached_source:
            source_url = cached_source
        if ch is None and source_url:
            ch = _ensure_channel_row(db, source_url=source_url)
        for bundle_idx in range(1, media_bundle_total + 1):
            bundle_info = _query_stage_bundle_meta(domain, channel_index, "media", bundle_idx, qsz, client_id, session, resolver_targets)
            n_parts = int(bundle_info.get("parts", "0") or 0)
            if n_parts <= 0:
                continue
            buf = _fetch_stage_bundle_payload(domain, channel_index, "media", bundle_idx, n_parts, qsz, client_id, session, resolver_targets)
            bundle_payload = json.loads(unpack_text(buf.decode("utf-8", errors="ignore")))
            if not source_url:
                source_url = normalize_tg_s_url(bundle_payload.get("source_url") or bundle_payload.get("username") or "")
            if ch is None and source_url:
                ch = _ensure_channel_row(db, source_url=source_url)
            if ch is None:
                continue
            bundle_messages = sorted(
                [m for m in (bundle_payload.get("messages", []) or []) if int(m.get("message_id") or 0) > 0],
                key=lambda item: (_media_message_weight(item), int(item.get("message_id") or 0)),
            )
            dirty = 0
            for local_index, message in enumerate(bundle_messages, start=1):
                if _upsert_media_message(db, ch.id, message):
                    channel_saved += 1
                dirty += 1
                done_count = text_total + media_done + local_index
                if done_count == work_total or done_count % 5 == 0:
                    _emit_progress(
                        progress,
                        kind="channel_progress",
                        mode="dns",
                        domain=domain,
                        channel_index=channel_index,
                        channels_total=channels_total,
                        source_url=source_url,
                        message_total=work_total,
                        message_done=done_count,
                        channel_saved=channel_saved,
                    )
                if dirty >= _media_apply_batch_size():
                    db.commit()
                    dirty = 0
            if dirty or not bundle_messages:
                db.commit()
            media_done += len(bundle_messages)

    if channel_state is not None and source_url:
        entry = channel_state.setdefault(state_key, {})
        entry.update(
            {
                "text_crc": text_crc,
                "media_crc": media_crc,
                "source_url": source_url,
                "message_total": text_total,
                "media_total": media_total,
                "updated_at": int(time.time()),
            }
        )

    _emit_progress(
        progress,
        kind="channel_done",
        mode="dns",
        domain=domain,
        channel_index=channel_index,
        channels_total=channels_total,
        source_url=source_url,
        message_total=work_total,
        message_done=work_total,
        channel_saved=channel_saved,
    )
    return channel_saved, work_total, source_url


def _sync_legacy_channel(
    db,
    *,
    domain: str,
    channel_index: int,
    channels_total: int,
    client_id: str,
    session: str,
    qsz: int,
    resolver_targets: list[DnsResolverTarget],
    info: dict[str, str],
    cached: dict[str, object],
    channel_state: dict[str, dict[str, object]] | None,
    state_key: str,
    progress: ProgressCallback,
) -> tuple[int, int, str]:
    n_parts = int(info.get("parts", "0") or 0)
    crc = str(info.get("crc", "") or "")
    cached_source = normalize_tg_s_url(cached.get("source_url") or "") if (cached.get("source_url") or "") else ""
    cached_crc = str(cached.get("crc", "") or "")
    cached_messages = int(cached.get("message_total", 0) or 0)
    cached_media_total = int(cached.get("media_total", 0) or 0)

    if n_parts <= 0:
        _emit_progress(
            progress,
            kind="channel_done",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            message_total=0,
            message_done=0,
            channel_saved=0,
        )
        return 0, 0, ""

    has_cached_avatar = _channel_has_cached_avatar(db, cached_source)
    if cached_source and cached_crc and cached_crc == crc and has_cached_avatar:
        cached_total = cached_messages + cached_media_total
        _emit_progress(
            progress,
            kind="channel_plan",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            source_url=cached_source,
            message_total=cached_total or cached_messages,
        )
        _emit_progress(
            progress,
            kind="channel_done",
            mode="dns",
            domain=domain,
            channel_index=channel_index,
            channels_total=channels_total,
            source_url=cached_source,
            message_total=cached_total or cached_messages,
            message_done=cached_total or cached_messages,
            channel_saved=0,
        )
        return 0, cached_total or cached_messages, cached_source

    buf = _fetch_channel_payload(domain=domain, channel_idx=channel_index, n_parts=n_parts, qsz=qsz, client_id=client_id, session=session, resolver_targets=resolver_targets)
    payload = json.loads(unpack_text(buf.decode("utf-8", errors="ignore")))
    source_url = normalize_tg_s_url(payload.get("source_url") or payload.get("username") or "")
    if not source_url:
        return 0, 0, ""

    raw_messages = [m for m in (payload.get("messages", []) or []) if int(m.get("message_id") or 0) > 0]
    text_messages = sorted(
        [
            {
                "message_id": int(m.get("message_id") or 0),
                "published_at": m.get("published_at", ""),
                "text": m.get("text", ""),
                "has_media": bool(m.get("has_media")),
                "media_kind": m.get("media_kind", "") or "",
                "reply_to_message_id": int(m.get("reply_to_message_id") or 0) or None,
                "reply_author": m.get("reply_author", "") or "",
                "reply_text": m.get("reply_text", "") or "",
                "forward_source": m.get("forward_source", "") or "",
            }
            for m in raw_messages
        ],
        key=lambda item: (_text_message_weight(item), int(item.get("message_id") or 0)),
    )
    media_messages = sorted(
        [
            {
                "message_id": int(m.get("message_id") or 0),
                "has_media": bool(m.get("has_media")),
                "media_kind": m.get("media_kind", "") or "",
                "photo_mime": m.get("photo_mime", "") or "",
                "photo_b64": m.get("photo_b64", "") or "",
                "photos_json": m.get("photos_json", "") or "",
            }
            for m in raw_messages
            if str(m.get("photos_json", "") or "").strip() or str(m.get("photo_b64", "") or "").strip()
        ],
        key=lambda item: (_media_message_weight(item), int(item.get("message_id") or 0)),
    )
    work_total = len(text_messages) + len(media_messages)
    _emit_progress(
        progress,
        kind="channel_plan",
        mode="dns",
        domain=domain,
        channel_index=channel_index,
        channels_total=channels_total,
        source_url=source_url,
        message_total=work_total,
    )
    if channel_state is not None:
        channel_state[state_key] = {
            "crc": crc,
            "source_url": source_url,
            "message_total": len(text_messages),
            "media_total": len(media_messages),
            "updated_at": int(time.time()),
        }

    ch = _ensure_channel_row(
        db,
        source_url=source_url,
        username=(payload.get("username") or source_url.rsplit("/", 1)[-1]),
        title=(payload.get("title", "") or source_url.rsplit("/", 1)[-1]),
        avatar_url=payload.get("avatar_url", ""),
        avatar_mime=payload.get("avatar_mime", ""),
        avatar_b64=payload.get("avatar_b64", ""),
    )

    channel_saved = 0
    dirty = 0
    for text_index, message in enumerate(text_messages, start=1):
        if _upsert_text_message(db, ch.id, message):
            channel_saved += 1
        dirty += 1
        if text_index == len(text_messages) or text_index % 5 == 0:
            _emit_progress(
                progress,
                kind="channel_progress",
                mode="dns",
                domain=domain,
                channel_index=channel_index,
                channels_total=channels_total,
                source_url=source_url,
                message_total=work_total,
                message_done=text_index,
                channel_saved=channel_saved,
            )
        if dirty >= _text_apply_batch_size():
            db.commit()
            dirty = 0
    if dirty or not text_messages:
        db.commit()

    dirty = 0
    for media_index, message in enumerate(media_messages, start=1):
        if _upsert_media_message(db, ch.id, message):
            channel_saved += 1
        dirty += 1
        done_count = len(text_messages) + media_index
        if done_count == work_total or done_count % 5 == 0:
            _emit_progress(
                progress,
                kind="channel_progress",
                mode="dns",
                domain=domain,
                channel_index=channel_index,
                channels_total=channels_total,
                source_url=source_url,
                message_total=work_total,
                message_done=done_count,
                channel_saved=channel_saved,
            )
        if dirty >= _media_apply_batch_size():
            db.commit()
            dirty = 0
    if dirty or not media_messages:
        db.commit()

    _emit_progress(
        progress,
        kind="channel_done",
        mode="dns",
        domain=domain,
        channel_index=channel_index,
        channels_total=channels_total,
        source_url=source_url,
        message_total=work_total,
        message_done=work_total,
        channel_saved=channel_saved,
    )
    return channel_saved, work_total, source_url


def _sync_domain_target(
    db,
    target: DnsDomainTarget,
    resolver_targets: list[DnsResolverTarget],
    qsz: int,
    channel_state: dict[str, dict[str, object]] | None = None,
    progress: ProgressCallback = None,
) -> dict[str, object]:
    domain = target.domain.rstrip(".")
    password = target.password
    _emit_progress(progress, kind="domain_start", mode="dns", domain=domain)

    for attempt in range(2):
        client_id, session = _get_session_for_domain(domain, password, resolver_targets)
        try:
            parts = _query_meta(domain, client_id, session, resolver_targets)
            total = int(parts.get("n", "0") or 0)
            saved = 0
            channel_errors: list[str] = []
            _emit_progress(progress, kind="domain_meta", mode="dns", domain=domain, channels_total=total)

            for i in range(1, total + 1):
                source_url = ""
                channel_saved = 0
                message_total = 0
                try:
                    _emit_progress(progress, kind="channel_start", mode="dns", domain=domain, channel_index=i, channels_total=total)
                    info_raw = _parse_txt(_query_txt(f"chan.{i}.meta.sz{qsz}.{client_id}.{session}.{domain}", resolver_targets)[0])
                    info = _ensure_ok(info_raw, f"chan_meta:{domain}:{i}")
                    state_key = f"{domain}|{i}"
                    cached = (channel_state or {}).get(state_key, {})
                    if info.get("v") == "2" or "tb" in info or "mb" in info:
                        channel_saved, message_total, source_url = _sync_staged_channel(
                            db,
                            domain=domain,
                            channel_index=i,
                            channels_total=total,
                            client_id=client_id,
                            session=session,
                            qsz=qsz,
                            resolver_targets=resolver_targets,
                            info=info,
                            cached=cached,
                            channel_state=channel_state,
                            state_key=state_key,
                            progress=progress,
                        )
                    else:
                        channel_saved, message_total, source_url = _sync_legacy_channel(
                            db,
                            domain=domain,
                            channel_index=i,
                            channels_total=total,
                            client_id=client_id,
                            session=session,
                            qsz=qsz,
                            resolver_targets=resolver_targets,
                            info=info,
                            cached=cached,
                            channel_state=channel_state,
                            state_key=state_key,
                            progress=progress,
                        )
                    saved += channel_saved
                except Exception as channel_exc:
                    db.rollback()
                    channel_errors.append(f"chan.{i}:{channel_exc}")
                    _emit_progress(
                        progress,
                        kind="channel_error",
                        mode="dns",
                        domain=domain,
                        channel_index=i,
                        channels_total=total,
                        source_url=source_url,
                        message_total=message_total,
                        message_done=message_total,
                        channel_saved=channel_saved,
                        error=str(channel_exc),
                    )
                    continue

            result = {
                "ok": (saved > 0) or (total == 0) or (len(channel_errors) < total),
                "domain": domain,
                "saved": saved,
                "channels": total,
                "client_id": client_id,
                "session": session,
                "channel_errors": channel_errors[:5],
                "channel_errors_count": len(channel_errors),
            }
            _emit_progress(progress, kind="domain_done", mode="dns", domain=domain, result=result, ok=bool(result.get("ok")))
            return result
        except RuntimeError as exc:
            if str(exc).endswith(":auth") and attempt == 0:
                db.rollback()
                _invalidate_session(domain, password)
                continue
            db.rollback()
            result = {"ok": False, "domain": domain, "saved": 0, "channels": 0, "error": str(exc)}
            _emit_progress(progress, kind="domain_done", mode="dns", domain=domain, result=result, ok=False, error=str(exc))
            return result
        except Exception as exc:
            db.rollback()
            result = {"ok": False, "domain": domain, "saved": 0, "channels": 0, "error": str(exc)}
            _emit_progress(progress, kind="domain_done", mode="dns", domain=domain, result=result, ok=False, error=str(exc))
            return result

    result = {"ok": False, "domain": domain, "saved": 0, "channels": 0, "error": "unknown"}
    _emit_progress(progress, kind="domain_done", mode="dns", domain=domain, result=result, ok=False, error="unknown")
    return result


def sync_from_dns_domain(domain: str, password: str = "", progress: ProgressCallback = None) -> dict:
    setup_logging()
    ensure_schema()
    started = time.time()
    qsz = _effective_query_size()
    resolver_targets = load_dns_resolvers()
    target = DnsDomainTarget(domain=(domain or "").strip().rstrip(".").lower(), password=(password or "").strip())
    if not target.domain:
        return {"saved": 0, "channels": 0, "domains": 0, "errors": 1, "mode": "dns", "error": "missing_domain"}

    channel_state = _load_dns_channel_state()
    with SessionLocal() as db:
        _emit_progress(progress, kind="sync_plan", mode="dns", domains_total=1)
        one = _sync_domain_target(db, target, resolver_targets, qsz, channel_state=channel_state, progress=progress)
        if one.get("ok"):
            db.commit()
        else:
            db.rollback()
        _save_dns_channel_state(channel_state)
        result = {
            "saved": int(one.get("saved", 0) or 0),
            "channels": int(one.get("channels", 0) or 0),
            "domains": 1,
            "errors": 0 if one.get("ok") else 1,
            "mode": "dns",
            "domain": target.domain,
            "detail": one,
        }
        record_event("dns_domain_sync", domain=target.domain, ok=bool(one.get("ok")), elapsed_ms=int((time.time() - started) * 1000), result=result)
        _emit_progress(progress, kind="sync_finish", mode="dns", ok=bool(one.get("ok")), result=result, elapsed_ms=int((time.time() - started) * 1000))
        return result


def probe_dns_domain(domain: str, password: str = "", resolvers: list[DnsResolverTarget] | None = None) -> dict:
    setup_logging()
    domain = (domain or "").strip().rstrip(".").lower()
    if not domain:
        return {"ok": False, "domain": "", "error": "missing_domain"}

    resolver_targets = resolvers or load_dns_resolvers()
    resolver_keys = [("system" if r.use_system else f"{r.server}:{r.port}") for r in resolver_targets]
    started = time.time()
    try:
        client_id, session = _get_session_for_domain(domain, password, resolver_targets)
        parts = _query_meta(domain, client_id, session, resolver_targets)
        result = {
            "ok": True,
            "domain": domain,
            "client_id": client_id,
            "session": session,
            "meta": f"v={parts.get('v', '')};n={parts.get('n', '0')}",
            "channels": int(parts.get("n", "0") or 0),
            "version": parts.get("v", ""),
            "elapsed_ms": int((time.time() - started) * 1000),
            "resolvers": resolver_keys,
            "checked_at": int(time.time()),
        }
        record_event("dns_probe", domain=domain, ok=True, elapsed_ms=result["elapsed_ms"], channels=result["channels"], resolvers=resolver_keys)
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "domain": domain,
            "error": str(exc),
            "elapsed_ms": int((time.time() - started) * 1000),
            "resolvers": resolver_keys,
            "checked_at": int(time.time()),
        }
        logger.warning("dns probe failed domain=%s error=%s", domain, exc)
        record_event("dns_probe", level="warning", domain=domain, ok=False, elapsed_ms=result["elapsed_ms"], error=str(exc), resolvers=resolver_keys)
        return result


def sync_from_dns_to_main_db(progress: ProgressCallback = None) -> dict:
    setup_logging()
    ensure_schema()
    started = time.time()
    qsz = _effective_query_size()
    domain_targets = load_dns_domains()
    resolver_targets = load_dns_resolvers()

    if not domain_targets:
        result = {"saved": 0, "channels": 0, "mode": "dns", "domains": 0, "errors": 0}
        record_event("dns_sync", domain_count=0, ok=True, elapsed_ms=int((time.time() - started) * 1000), result=result)
        _emit_progress(progress, kind="sync_finish", mode="dns", ok=True, result=result, elapsed_ms=int((time.time() - started) * 1000))
        return result

    saved = 0
    total_channels = 0
    domain_errors = 0
    details: list[dict[str, object]] = []
    _emit_progress(progress, kind="sync_plan", mode="dns", domains_total=len(domain_targets))
    channel_state = _load_dns_channel_state()

    with SessionLocal() as db:
        for target in domain_targets:
            one = _sync_domain_target(db, target, resolver_targets, qsz, channel_state=channel_state, progress=progress)
            details.append(one)
            if one.get("ok"):
                db.commit()
                saved += int(one.get("saved", 0) or 0)
                total_channels += int(one.get("channels", 0) or 0)
            else:
                db.rollback()
                logger.warning("dns sync domain failed domain=%s error=%s", one.get("domain"), one.get("error", "unknown"))
                domain_errors += 1
    _save_dns_channel_state(channel_state)

    result = {
        "saved": saved,
        "channels": total_channels,
        "domains": len(domain_targets),
        "errors": domain_errors,
        "mode": "dns",
        "details": details,
    }
    record_event("dns_sync", ok=domain_errors == 0, elapsed_ms=int((time.time() - started) * 1000), result=result)
    _emit_progress(progress, kind="sync_finish", mode="dns", ok=domain_errors == 0, result=result, elapsed_ms=int((time.time() - started) * 1000))
    return result

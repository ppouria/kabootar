from __future__ import annotations

import hashlib
import json
import re
import secrets
import socket
import threading
import time
import zlib
from dataclasses import dataclass

from dnslib import NS, QTYPE, RCODE, RR, SOA, TXT, DNSRecord
from dnslib.server import BaseResolver, DNSServer

from app.scraper import (
    fetch_html_with_proxies,
    fetch_photo_base64_with_proxies,
    parse_channel_meta,
    parse_recent_messages,
)
from app.settings_store import get_setting, set_setting
from app.text_packer import pack_text
from app.utils import normalize_photo_items, normalize_tg_s_url, parse_csv, primary_photo_fields, serialize_photo_items
from app.versioning import app_meta


@dataclass
class BridgeConfig:
    domain: str
    port: int
    address: str = "0.0.0.0"
    ttl: int = 30
    refresh_seconds: int = 60
    recent_per_channel: int = 50


def _safe_chunk_size(n: int) -> int:
    return max(16, min(220, n))


def _utf8_len(value: str) -> int:
    return len((value or "").encode("utf-8"))


def _text_bundle_target_bytes() -> int:
    raw = (__import__("os").getenv("DNS_TEXT_BUNDLE_TARGET_BYTES", "5000") or "5000").strip()
    try:
        value = int(raw)
    except Exception:
        value = 5000
    return max(1200, min(24000, value))


def _media_bundle_target_bytes() -> int:
    raw = (__import__("os").getenv("DNS_MEDIA_BUNDLE_TARGET_BYTES", "48000") or "48000").strip()
    try:
        value = int(raw)
    except Exception:
        value = 48000
    return max(4000, min(180000, value))


def _avatar_max_bytes() -> int:
    raw = (__import__("os").getenv("DNS_AVATAR_MAX_BYTES", "60000") or "60000").strip()
    try:
        value = int(raw)
    except Exception:
        value = 60000
    return max(4000, min(120000, value))


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


def _payload_crc_bytes(raw: bytes) -> str:
    return f"{zlib.crc32(raw) & 0xffffffff:08x}"


def _payload_crc_text(value: str) -> str:
    return _payload_crc_bytes(value.encode("utf-8"))


def _combined_crc(values: list[str]) -> str:
    return _payload_crc_bytes("|".join(values).encode("ascii")) if values else "00000000"


@dataclass
class PackedBundle:
    payload: str
    crc: str
    message_count: int
    byte_len: int


@dataclass
class ChannelPayload:
    text_bundles: list[PackedBundle]
    media_bundles: list[PackedBundle]
    text_crc: str
    media_crc: str
    message_total: int
    media_total: int


def _pack_bundle(payload_obj: dict, message_count: int) -> PackedBundle:
    payload_json = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
    payload = pack_text(payload_json)
    data = payload.encode("utf-8")
    return PackedBundle(payload=payload, crc=_payload_crc_bytes(data), message_count=message_count, byte_len=len(data))


def _bundle_records(
    base_payload: dict,
    records: list[dict],
    target_bytes: int,
    weight_fn,
    *,
    first_payload_overrides: dict[str, str] | None = None,
) -> list[PackedBundle]:
    if not records:
        return []

    bundles: list[PackedBundle] = []
    current: list[dict] = []
    current_weight = 0
    first_bundle = True

    def _flush_current() -> None:
        nonlocal current, current_weight, first_bundle
        payload = {**base_payload, "messages": current}
        if first_bundle and first_payload_overrides:
            for key, value in first_payload_overrides.items():
                if value:
                    payload[key] = value
        bundles.append(_pack_bundle(payload, len(current)))
        current = []
        current_weight = 0
        first_bundle = False

    for record in records:
        weight = max(1, int(weight_fn(record)))
        if current and current_weight + weight > target_bytes:
            _flush_current()
        current.append(record)
        current_weight += weight

    if current:
        _flush_current()
    return bundles


class SessionStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[tuple[str, str], float] = {}

    def _cleanup(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        dead = [k for k, exp in self.sessions.items() if exp <= now]
        for k in dead:
            self.sessions.pop(k, None)

    def issue(self, client_id: str, ttl_seconds: int) -> str:
        token = secrets.token_hex(8)
        now = time.time()
        with self.lock:
            self._cleanup(now)
            self.sessions[(client_id, token)] = now + max(60, ttl_seconds)
        return token

    def verify(self, client_id: str, token: str) -> bool:
        now = time.time()
        with self.lock:
            self._cleanup(now)
            exp = self.sessions.get((client_id, token), 0.0)
            return exp > now


class BridgeCache:
    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.channels_override: list[str] = []
        self.payloads: dict[int, ChannelPayload] = {}
        self.avatar_cache: dict[str, tuple[str, str]] = {}
        self.version: str = "0"
        self.count: int = 0

    def _access_mode(self) -> str:
        mode = (get_setting("dns_access_mode", "free") or "free").strip().lower()
        return "fixed" if mode == "fixed" else "free"

    def _configured_channels(self) -> list[str]:
        raw = get_setting("telegram_channels", "") or ""
        return [normalize_tg_s_url(c) for c in parse_csv(raw)]

    def _merged_channels(self, base: list[str], extra: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in [*base, *extra]:
            if not value:
                continue
            try:
                normalized = normalize_tg_s_url(value)
            except Exception:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def set_channels_override(self, channels: list[str]) -> None:
        clean = self._merged_channels([], channels)
        with self.lock:
            self.channels_override = clean

    def persist_channels(self, channels: list[str]) -> list[str]:
        current = self._configured_channels()
        merged = self._merged_channels(channels, current)
        if merged != current:
            set_setting("telegram_channels", ",".join(merged))
            print(f"[dns-bridge] persisted channels={len(merged)}")
        self.set_channels_override(merged)
        return merged

    def get_channels_override(self) -> list[str]:
        with self.lock:
            return list(self.channels_override)

    def refresh_from_telegram(self) -> None:
        channels = self._configured_channels()
        if self._access_mode() != "fixed":
            override = self.get_channels_override()
            if override:
                channels = self._merged_channels(channels, override)

        proxies_raw = get_setting("telegram_proxies", "") or ""
        proxies = parse_csv(proxies_raw)

        now = int(time.time())
        max_photo_bytes = int(__import__("os").getenv("DNS_MEDIA_MAX_BYTES", "180000"))
        max_avatar_bytes = _avatar_max_bytes()
        media_cache: dict[str, tuple[str, str]] = {}
        avatar_cache_prev = dict(self.avatar_cache)
        avatar_cache_next: dict[str, tuple[str, str]] = {}
        payloads_new: dict[int, ChannelPayload] = {}

        for url in channels:
            try:
                html = fetch_html_with_proxies(url, proxies, attempts=3, timeout_seconds=20, retry_delay_seconds=10)
                meta = parse_channel_meta(html)
                items = parse_recent_messages(html, limit=self.cfg.recent_per_channel)
            except Exception as exc:
                print(f"[dns-bridge] skip channel {url}: {exc}")
                continue

            messages_payload: list[dict] = []
            for m in items:
                if int(m.get("message_id") or 0) <= 0:
                    continue

                photo_urls = [str(url_part).strip() for url_part in (m.get("photo_urls") or []) if str(url_part).strip()]
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

            username = url.rsplit("/", 1)[-1]
            avatar_url = (meta.get("avatar_url", "") or "").strip()
            avatar_mime = ""
            avatar_b64 = ""
            if avatar_url:
                cached_avatar = avatar_cache_prev.get(avatar_url)
                if cached_avatar:
                    avatar_mime, avatar_b64 = cached_avatar
                else:
                    try:
                        fetched_avatar = fetch_photo_base64_with_proxies(
                            avatar_url,
                            proxies,
                            attempts=2,
                            timeout_seconds=20,
                            retry_delay_seconds=5,
                            max_bytes=max_avatar_bytes,
                        )
                    except Exception:
                        fetched_avatar = None
                    if fetched_avatar:
                        avatar_mime, avatar_b64 = fetched_avatar
                if avatar_b64:
                    avatar_cache_next[avatar_url] = (avatar_mime, avatar_b64)

            text_records = [
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
                for m in messages_payload
            ]
            text_records.sort(key=lambda item: (_text_message_weight(item), int(item.get("message_id") or 0)))

            media_records = [
                {
                    "message_id": int(m.get("message_id") or 0),
                    "has_media": bool(m.get("has_media")),
                    "media_kind": m.get("media_kind", "") or "",
                    "photo_mime": m.get("photo_mime", "") or "",
                    "photo_b64": m.get("photo_b64", "") or "",
                    "photos_json": m.get("photos_json", "") or "",
                }
                for m in messages_payload
                if str(m.get("photos_json", "") or "").strip() or str(m.get("photo_b64", "") or "").strip()
            ]
            media_records.sort(key=lambda item: (_media_message_weight(item), int(item.get("message_id") or 0)))

            text_base = {
                "stage": "text",
                "source_url": url,
                "username": username,
                "title": meta.get("title", ""),
                "avatar_url": avatar_url,
            }
            media_base = {
                "stage": "media",
                "source_url": url,
                "username": username,
            }
            text_bundles = _bundle_records(
                text_base,
                text_records,
                _text_bundle_target_bytes(),
                _text_message_weight,
                first_payload_overrides={
                    "avatar_mime": avatar_mime,
                    "avatar_b64": avatar_b64,
                },
            )
            media_bundles = _bundle_records(media_base, media_records, _media_bundle_target_bytes(), _media_message_weight)
            payloads_new[len(payloads_new) + 1] = ChannelPayload(
                text_bundles=text_bundles,
                media_bundles=media_bundles,
                text_crc=_combined_crc([bundle.crc for bundle in text_bundles]),
                media_crc=_combined_crc([bundle.crc for bundle in media_bundles]),
                message_total=len(text_records),
                media_total=len(media_records),
            )

        with self.lock:
            self.payloads = payloads_new
            self.avatar_cache = avatar_cache_next
            self.version = str(now)
            self.count = len(payloads_new)

    def get_meta(self) -> tuple[str, int]:
        with self.lock:
            return self.version, self.count

    def get_payload(self, idx: int) -> ChannelPayload | None:
        with self.lock:
            return self.payloads.get(idx)


def _refresh_loop(cache: BridgeCache, sec: int) -> None:
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
        self.zone_apex = self.domain
        labels = self.domain.rstrip(".").split(".")
        parent = ".".join(labels[1:]) if len(labels) > 1 else self.domain.rstrip(".")
        self.ns_host = f"ns.{parent}."
        self.soa_rname = f"hostmaster.{parent}."
        fallback_host = (get_setting("dns_fallback_host", "127.0.0.1") or "127.0.0.1").strip()
        fallback_port_raw = (get_setting("dns_fallback_port", "5300") or "5300").strip()
        try:
            fallback_port = int(fallback_port_raw)
        except Exception:
            fallback_port = 5300
        self.fallback_host = fallback_host or "127.0.0.1"
        self.fallback_port = max(1, min(65535, fallback_port))

        self.auth_re = re.compile(r"^auth\.([a-z0-9-]{1,32})\.([0-9a-f]{40})$")
        self.meta_re = re.compile(r"^meta(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_meta_re = re.compile(r"^chan\.(\d+)\.meta\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_part_re = re.compile(r"^chan\.(\d+)\.part\.(\d+)\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_text_meta_re = re.compile(r"^chan\.(\d+)\.text\.meta\.(\d+)\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_text_part_re = re.compile(r"^chan\.(\d+)\.text\.part\.(\d+)\.(\d+)\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_media_meta_re = re.compile(r"^chan\.(\d+)\.media\.meta\.(\d+)\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.chan_media_part_re = re.compile(r"^chan\.(\d+)\.media\.part\.(\d+)\.(\d+)\.sz(\d+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.up_meta_re = re.compile(r"^upmeta(?:\.n([0-9a-f]{6,16}))?\.(\d+)\.([0-9a-f]{8})(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.up_part_re = re.compile(r"^uppart(?:\.n([0-9a-f]{6,16}))?\.(\d+)\.([0-9a-f]+)(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")
        self.up_commit_re = re.compile(r"^upcommit(?:\.n([0-9a-f]{6,16}))?(?:\.([a-z0-9-]{1,32})\.([a-z0-9]{3,64}))?$")

        self.sessions = SessionStore()
        self.pending_uploads: dict[tuple[str, str, str], dict] = {}

    def _bundle_meta(self, bundle: PackedBundle, sz: int, extra: str = "") -> str:
        parts = (bundle.byte_len + sz - 1) // sz
        suffix = f";{extra}" if extra else ""
        return f"sz={sz};parts={parts};len={bundle.byte_len};crc={bundle.crc};messages={bundle.message_count}{suffix}"

    def _reply_bundle_meta(self, reply, qname_obj, bundle: PackedBundle | None, sz: int, extra: str = ""):
        if not bundle:
            reply.header.rcode = RCODE.NXDOMAIN
            return reply
        return self._reply_txt(reply, qname_obj, self._bundle_meta(bundle, sz, extra=extra))

    def _reply_bundle_part(self, reply, qname_obj, bundle: PackedBundle | None, part: int, sz: int):
        if not bundle:
            reply.header.rcode = RCODE.NXDOMAIN
            return reply
        parts = (bundle.byte_len + sz - 1) // sz
        if part < 1 or part > parts:
            reply.header.rcode = RCODE.NXDOMAIN
            return reply
        payload_bytes = bundle.payload.encode("utf-8")
        chunk = payload_bytes[(part - 1) * sz : (part * sz)]
        reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT([chunk]), ttl=self.cfg.ttl))
        return reply

    def _reply_txt(self, reply, qname_obj, value: str):
        reply.add_answer(RR(qname_obj, QTYPE.TXT, rdata=TXT(value), ttl=self.cfg.ttl))
        return reply

    def _soa_record(self) -> RR:
        serial = int(time.time())
        soa = SOA(self.ns_host, self.soa_rname, (serial, 3600, 600, 86400, 60))
        return RR(self.zone_apex, QTYPE.SOA, rdata=soa, ttl=max(60, self.cfg.ttl))

    def _add_soa_authority(self, reply) -> None:
        reply.add_auth(self._soa_record())

    def _forward_to_fallback(self, request):
        payload = request.pack()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(3.0)
            s.sendto(payload, (self.fallback_host, self.fallback_port))
            data, _ = s.recvfrom(65535)
            return DNSRecord.parse(data)
        finally:
            s.close()

    def _access_mode(self) -> str:
        mode = (get_setting("dns_access_mode", "free") or "free").strip().lower()
        return "fixed" if mode == "fixed" else "free"

    def _password(self) -> str:
        return (get_setting("dns_password", "") or "").strip()

    def _session_ttl(self) -> int:
        raw = get_setting("dns_session_ttl_seconds", "3600") or "3600"
        try:
            n = int(raw)
        except Exception:
            n = 3600
        return max(60, min(86400, n))

    def _auth_enabled(self) -> bool:
        return bool(self._password())

    def _password_sig(self, password: str) -> str:
        return hashlib.sha1(password.encode("utf-8")).hexdigest()

    def _resolve_client_session(self, cid: str | None, sess: str | None) -> tuple[str, str]:
        client_id = (cid or "public").strip().lower() or "public"
        session = (sess or "public").strip().lower() or "public"
        return client_id, session

    def _verify_session(self, client_id: str, session: str) -> bool:
        if not self._auth_enabled():
            return True
        return self.sessions.verify(client_id, session)

    def resolve(self, request, handler):
        reply = request.reply()
        reply.header.ra = 0
        reply.header.ad = 0
        qname_obj = request.q.qname
        qname = str(qname_obj).lower()
        qtype = QTYPE[request.q.qtype]

        if not qname.endswith(self.domain):
            try:
                return self._forward_to_fallback(request)
            except Exception as exc:
                print(f"[dns-bridge] fallback forward error for {qname}: {exc}")
                reply.header.rcode = RCODE.SERVFAIL
                return reply

        sub = qname[: -len(self.domain)].rstrip(".")

        # Serve minimal authoritative data for delegated child-zone compatibility.
        if qtype == "NS" and sub == "":
            reply.add_answer(RR(self.zone_apex, QTYPE.NS, rdata=NS(self.ns_host), ttl=max(60, self.cfg.ttl)))
            return reply
        if qtype == "SOA" and sub == "":
            reply.add_answer(self._soa_record())
            return reply
        if qtype != "TXT":
            self._add_soa_authority(reply)
            return reply

        try:
            m = self.auth_re.match(sub)
            if m:
                client_id, sig = m.group(1), m.group(2)
                if not self._auth_enabled():
                    return self._reply_txt(reply, qname_obj, "ok=1;s=public;ttl=86400")

                if sig != self._password_sig(self._password()):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=bad_password")

                token = self.sessions.issue(client_id, self._session_ttl())
                return self._reply_txt(reply, qname_obj, f"ok=1;s={token};ttl={self._session_ttl()}")

            m = self.up_meta_re.match(sub)
            if m:
                nonce = (m.group(1) or "legacy").strip().lower() or "legacy"
                total = max(1, int(m.group(2)))
                crc = m.group(3)
                client_id, session = self._resolve_client_session(m.group(4), m.group(5))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                if self._access_mode() == "fixed":
                    return self._reply_txt(reply, qname_obj, "ok=0;err=fixed_mode")
                self.pending_uploads[(client_id, session, nonce)] = {"total": total, "crc": crc, "parts": {}}
                return self._reply_txt(reply, qname_obj, "ok=1")

            m = self.up_part_re.match(sub)
            if m:
                nonce = (m.group(1) or "legacy").strip().lower() or "legacy"
                idx = int(m.group(2))
                data = m.group(3)
                client_id, session = self._resolve_client_session(m.group(4), m.group(5))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                if self._access_mode() == "fixed":
                    return self._reply_txt(reply, qname_obj, "ok=0;err=fixed_mode")
                state = self.pending_uploads.setdefault((client_id, session, nonce), {"total": 0, "crc": "", "parts": {}})
                if idx >= 1:
                    state["parts"][idx] = data
                return self._reply_txt(reply, qname_obj, "ok=1")

            m = self.up_commit_re.match(sub)
            if m:
                nonce = (m.group(1) or "legacy").strip().lower() or "legacy"
                client_id, session = self._resolve_client_session(m.group(2), m.group(3))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                if self._access_mode() == "fixed":
                    return self._reply_txt(reply, qname_obj, "ok=0;err=fixed_mode")

                state = self.pending_uploads.get((client_id, session, nonce))
                if not state or int(state.get("total", 0)) <= 0:
                    return self._reply_txt(reply, qname_obj, "ok=0;err=no_meta")

                total = int(state["total"])
                joined = "".join(state["parts"].get(i, "") for i in range(1, total + 1))
                raw = bytes.fromhex(joined)
                crc = f"{zlib.crc32(raw) & 0xffffffff:08x}"
                if crc != state.get("crc", ""):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=crc")

                payload = json.loads(raw.decode("utf-8", errors="ignore"))
                channels = payload.get("channels", []) if isinstance(payload, dict) else []
                persisted = self.cache.persist_channels(channels if isinstance(channels, list) else [])
                self.cache.refresh_from_telegram()
                self.pending_uploads.pop((client_id, session, nonce), None)
                return self._reply_txt(reply, qname_obj, f"ok=1;applied=1;channels={len(persisted)}")

            m = self.meta_re.match(sub)
            if m:
                client_id, session = self._resolve_client_session(m.group(1), m.group(2))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                ver, count = self.cache.get_meta()
                return self._reply_txt(reply, qname_obj, f"v={ver};n={count}")

            m = self.chan_meta_re.match(sub)
            if m:
                idx = int(m.group(1))
                sz = _safe_chunk_size(int(m.group(2)))
                client_id, session = self._resolve_client_session(m.group(3), m.group(4))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")

                row = self.cache.get_payload(idx)
                if not row:
                    reply.header.rcode = RCODE.NXDOMAIN
                    return reply
                return self._reply_txt(
                    reply,
                    qname_obj,
                    (
                        f"v=2;i={idx};sz={sz};tb={len(row.text_bundles)};mb={len(row.media_bundles)};"
                        f"tm={row.message_total};mm={row.media_total};tc={row.text_crc};mc={row.media_crc}"
                    ),
                )

            m = self.chan_part_re.match(sub)
            if m:
                idx = int(m.group(1))
                part = int(m.group(2))
                sz = _safe_chunk_size(int(m.group(3)))
                client_id, session = self._resolve_client_session(m.group(4), m.group(5))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")

                row = self.cache.get_payload(idx)
                if not row:
                    reply.header.rcode = RCODE.NXDOMAIN
                    return reply
                bundle = row.text_bundles[0] if row.text_bundles else None
                return self._reply_bundle_part(reply, qname_obj, bundle, part, sz)

            m = self.chan_text_meta_re.match(sub)
            if m:
                idx = int(m.group(1))
                bundle_idx = int(m.group(2))
                sz = _safe_chunk_size(int(m.group(3)))
                client_id, session = self._resolve_client_session(m.group(4), m.group(5))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                row = self.cache.get_payload(idx)
                bundle = row.text_bundles[bundle_idx - 1] if row and 1 <= bundle_idx <= len(row.text_bundles) else None
                return self._reply_bundle_meta(reply, qname_obj, bundle, sz, extra=f"i={idx};b={bundle_idx};stage=text")

            m = self.chan_text_part_re.match(sub)
            if m:
                idx = int(m.group(1))
                bundle_idx = int(m.group(2))
                part = int(m.group(3))
                sz = _safe_chunk_size(int(m.group(4)))
                client_id, session = self._resolve_client_session(m.group(5), m.group(6))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                row = self.cache.get_payload(idx)
                bundle = row.text_bundles[bundle_idx - 1] if row and 1 <= bundle_idx <= len(row.text_bundles) else None
                return self._reply_bundle_part(reply, qname_obj, bundle, part, sz)

            m = self.chan_media_meta_re.match(sub)
            if m:
                idx = int(m.group(1))
                bundle_idx = int(m.group(2))
                sz = _safe_chunk_size(int(m.group(3)))
                client_id, session = self._resolve_client_session(m.group(4), m.group(5))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                row = self.cache.get_payload(idx)
                bundle = row.media_bundles[bundle_idx - 1] if row and 1 <= bundle_idx <= len(row.media_bundles) else None
                return self._reply_bundle_meta(reply, qname_obj, bundle, sz, extra=f"i={idx};b={bundle_idx};stage=media")

            m = self.chan_media_part_re.match(sub)
            if m:
                idx = int(m.group(1))
                bundle_idx = int(m.group(2))
                part = int(m.group(3))
                sz = _safe_chunk_size(int(m.group(4)))
                client_id, session = self._resolve_client_session(m.group(5), m.group(6))
                if not self._verify_session(client_id, session):
                    return self._reply_txt(reply, qname_obj, "ok=0;err=auth")
                row = self.cache.get_payload(idx)
                bundle = row.media_bundles[bundle_idx - 1] if row and 1 <= bundle_idx <= len(row.media_bundles) else None
                return self._reply_bundle_part(reply, qname_obj, bundle, part, sz)
        except Exception as exc:
            print("[dns-bridge] resolve error:", exc)
            reply.header.rcode = RCODE.SERVFAIL
            self._add_soa_authority(reply)
            return reply

        # Unknown TXT label under this zone -> NXDOMAIN with SOA.
        reply.header.rcode = RCODE.NXDOMAIN
        self._add_soa_authority(reply)
        return reply


def run_dns_bridge_server() -> None:
    meta = app_meta()
    domain = get_setting("dns_domain", "t.example.com") or "t.example.com"
    port = int(get_setting("dns_port", "5533") or 5533)
    cfg = BridgeConfig(domain=domain, port=port)
    cache = BridgeCache(cfg)
    cache.refresh_from_telegram()

    t = threading.Thread(target=_refresh_loop, args=(cache, cfg.refresh_seconds), daemon=True)
    t.start()

    resolver = BridgeResolver(cache, cfg)
    server_udp = DNSServer(resolver, port=cfg.port, address=cfg.address, tcp=False)
    server_udp.start_thread()

    tcp_enabled = False
    try:
        server_tcp = DNSServer(resolver, port=cfg.port, address=cfg.address, tcp=True)
        server_tcp.start_thread()
        tcp_enabled = True
    except Exception as exc:
        print(f"[dns-bridge] tcp listener disabled: {exc}")

    mode = (get_setting("dns_access_mode", "free") or "free").strip().lower()
    auth = "on" if (get_setting("dns_password", "") or "").strip() else "off"
    proto = "udp+tcp" if tcp_enabled else "udp"
    fallback_host = (get_setting("dns_fallback_host", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    fallback_port = (get_setting("dns_fallback_port", "5300") or "5300").strip() or "5300"
    print(
        f"[dns-bridge] {meta.app_name} server {meta.version_name} ({meta.version_code}) "
        f"channel={meta.release_channel}"
    )
    print(
        f"[dns-bridge] running {proto} on {cfg.address}:{cfg.port} "
        f"domain={cfg.domain} mode={mode} auth={auth} fallback={fallback_host}:{fallback_port}"
    )
    while True:
        time.sleep(3600)

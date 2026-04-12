from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import time
import zlib

from dnslib import NS, QTYPE, RCODE, RR, SOA, TXT, DNSRecord
from dnslib.server import BaseResolver, DNSServer

from app.settings_store import get_setting
from app.versioning import app_meta

from .core import (
    BridgeCache,
    BridgeConfig,
    PackedBundle,
    SessionStore,
    _normalized_domains,
    _refresh_loop,
    _safe_chunk_size,
)


class BridgeResolver(BaseResolver):
    def __init__(self, cache: BridgeCache, cfg: BridgeConfig):
        self.cache = cache
        self.cfg = cfg

        configured_domains = cfg.domains or [cfg.domain]
        zones: list[dict[str, str]] = []
        seen_domains: set[str] = set()
        for raw_domain in configured_domains:
            domain = (raw_domain or "").strip().lower().rstrip(".")
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            fqdn = f"{domain}."
            labels = domain.split(".")
            parent = ".".join(labels[1:]) if len(labels) > 1 else domain
            zones.append(
                {
                    "domain": fqdn,
                    "zone_apex": fqdn,
                    "ns_host": f"ns.{parent}.",
                    "soa_rname": f"hostmaster.{parent}.",
                }
            )

        if not zones:
            fallback_domain = "t.example.com"
            zones.append(
                {
                    "domain": f"{fallback_domain}.",
                    "zone_apex": f"{fallback_domain}.",
                    "ns_host": "ns.example.com.",
                    "soa_rname": "hostmaster.example.com.",
                }
            )

        # Prefer the most specific suffix when multiple zones can match.
        zones.sort(key=lambda item: len(item["domain"]), reverse=True)
        self.zones = zones
        # Backward-compatible primary zone fields.
        self.domain = zones[0]["domain"]
        self.zone_apex = zones[0]["zone_apex"]
        self.ns_host = zones[0]["ns_host"]
        self.soa_rname = zones[0]["soa_rname"]

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

    def _soa_record(self, zone: dict[str, str]) -> RR:
        serial = int(time.time())
        soa = SOA(zone["ns_host"], zone["soa_rname"], (serial, 3600, 600, 86400, 60))
        return RR(zone["zone_apex"], QTYPE.SOA, rdata=soa, ttl=max(60, self.cfg.ttl))

    def _add_soa_authority(self, reply, zone: dict[str, str]) -> None:
        reply.add_auth(self._soa_record(zone))

    def _match_zone(self, qname: str) -> dict[str, str] | None:
        for zone in self.zones:
            if qname.endswith(zone["domain"]):
                return zone
        return None

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

        zone = self._match_zone(qname)
        if not zone:
            try:
                return self._forward_to_fallback(request)
            except Exception as exc:
                print(f"[dns-bridge] fallback forward error for {qname}: {exc}")
                reply.header.rcode = RCODE.SERVFAIL
                return reply

        sub = qname[: -len(zone["domain"])].rstrip(".")

        # Serve minimal authoritative data for delegated child-zone compatibility.
        if qtype == "NS" and sub == "":
            reply.add_answer(RR(zone["zone_apex"], QTYPE.NS, rdata=NS(zone["ns_host"]), ttl=max(60, self.cfg.ttl)))
            return reply
        if qtype == "SOA" and sub == "":
            reply.add_answer(self._soa_record(zone))
            return reply
        if qtype != "TXT":
            self._add_soa_authority(reply, zone)
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
            self._add_soa_authority(reply, zone)
            return reply

        # Unknown TXT label under this zone -> NXDOMAIN with SOA.
        reply.header.rcode = RCODE.NXDOMAIN
        self._add_soa_authority(reply, zone)
        return reply


def run_dns_bridge_server() -> None:
    meta = app_meta()
    domain = (get_setting("dns_domain", "t.example.com") or "t.example.com").strip()
    domains_raw = get_setting("dns_domains", "") or ""
    domains = _normalized_domains(domain, domains_raw)

    def _setting_int(name: str, default: int, min_value: int, max_value: int) -> int:
        raw = (get_setting(name, str(default)) or str(default)).strip()
        try:
            value = int(raw)
        except Exception:
            value = default
        return max(min_value, min(max_value, value))

    bind_address = (get_setting("dns_bind_address", "0.0.0.0") or "0.0.0.0").strip() or "0.0.0.0"
    port = _setting_int("dns_port", 5533, 1, 65535)
    ttl = _setting_int("dns_ttl", 30, 1, 3600)
    refresh_seconds = _setting_int("dns_refresh_seconds", 60, 20, 86400)
    recent_per_channel = _setting_int("dns_recent_per_channel", 50, 1, 300)

    cfg = BridgeConfig(
        domain=domains[0],
        port=port,
        address=bind_address,
        ttl=ttl,
        refresh_seconds=refresh_seconds,
        recent_per_channel=recent_per_channel,
        domains=domains,
    )
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
        f"domains={','.join(domains)} mode={mode} auth={auth} fallback={fallback_host}:{fallback_port} "
        f"ttl={cfg.ttl}s refresh={cfg.refresh_seconds}s recent_per_channel={cfg.recent_per_channel}"
    )
    while True:
        time.sleep(3600)


import logging
import threading
import time
from typing import Callable, Optional

from sqlalchemy import select

from app.db import SessionLocal, ensure_schema
from app.dns_bridge import load_dns_domains, push_channels_to_domains, sync_from_dns_to_main_db
from app.models import Channel, Message
from app.runtime_debug import record_event, setup_logging
from app.scraper import (
    fetch_html_with_proxies,
    fetch_photo_base64_with_proxies,
    parse_channel_meta,
    parse_recent_messages,
)
from app.settings_store import get_setting
from app.utils import normalize_photo_items, normalize_tg_s_url, parse_csv, primary_photo_fields, serialize_photo_items

logger = logging.getLogger("kabootar.sync")
_SYNC_EXEC_LOCK = threading.Lock()
ProgressCallback = Optional[Callable[[dict], None]]


def _emit_progress(progress: ProgressCallback, **payload) -> None:
    if not progress:
        return
    try:
        progress(dict(payload))
    except Exception:
        logger.exception("sync progress callback failed")


def _configured_client_channels(priority_channel: str = "") -> list[str]:
    raw = (get_setting("dns_client_channels", "") or get_setting("direct_channels", "") or "").strip()
    channels = [normalize_tg_s_url(c) for c in parse_csv(raw)]
    if priority_channel:
        try:
            wanted = normalize_tg_s_url(priority_channel)
        except Exception:
            wanted = ""
        if wanted and wanted in channels:
            channels = [wanted, *[item for item in channels if item != wanted]]
    return channels


def collect_recent_messages(url: str, proxies: list[str], target_count: int, attempts: int, timeout_seconds: int, retry_delay_seconds: int) -> list[dict]:
    collected: list[dict] = []
    seen_ids: set[int] = set()
    next_url = url

    for _ in range(10):
        html = fetch_html_with_proxies(
            next_url,
            proxies,
            attempts=attempts,
            timeout_seconds=timeout_seconds,
            retry_delay_seconds=retry_delay_seconds,
        )
        page_items = parse_recent_messages(html, limit=100)
        if not page_items:
            break

        for item in page_items:
            mid = item.get('message_id')
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            collected.append(item)

        collected = sorted(collected, key=lambda x: x['message_id'])
        if len(collected) >= target_count:
            return collected[-target_count:]

        oldest = page_items[0].get('message_id')
        if not oldest:
            break
        next_url = f"{url}?before={oldest}"

    return collected[-target_count:]


def _utf8_len(value: str) -> int:
    return len((value or "").encode("utf-8"))


def _direct_text_weight(item: dict) -> int:
    return (
        96
        + _utf8_len(item.get("text", ""))
        + _utf8_len(item.get("media_kind", ""))
        + _utf8_len(item.get("reply_author", ""))
        + _utf8_len(item.get("reply_text", ""))
        + _utf8_len(item.get("forward_source", ""))
    )


def _direct_batch_size(name: str, default: int, lower: int, upper: int) -> int:
    raw = (__import__("os").getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def _direct_text_batch_size() -> int:
    return _direct_batch_size("DIRECT_TEXT_APPLY_BATCH_SIZE", 5, 1, 20)


def _direct_media_batch_size() -> int:
    return _direct_batch_size("DIRECT_MEDIA_APPLY_BATCH_SIZE", 2, 1, 10)


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


def _sync_once_impl(progress: ProgressCallback = None, force_server_refresh: bool = False, priority_channel: str = "") -> dict:
    setup_logging()
    ensure_schema()
    started = time.time()
    source_mode = (get_setting("source_mode", "dns") or "dns").strip().lower()
    if source_mode == "telegram":
        source_mode = "direct"
    record_event("sync_start", mode=source_mode)
    _emit_progress(progress, kind="sync_start", mode=source_mode, started_at=int(started))
    if source_mode == "dns":
        refresh_result = None
        if force_server_refresh:
            push_channels = _configured_client_channels(priority_channel=priority_channel)
            domain_targets = load_dns_domains()
            _emit_progress(
                progress,
                kind="server_refresh_start",
                mode=source_mode,
                domains_total=len(domain_targets),
                channels_total=len(push_channels),
            )
            if push_channels and domain_targets:
                refresh_result = push_channels_to_domains(push_channels)
            else:
                refresh_result = {
                    "results": [],
                    "domains": len(domain_targets),
                    "channels": len(push_channels),
                    "skipped": "no_domains" if not domain_targets else "no_channels",
                }
            refresh_ok = not any(not row.get("ok", False) for row in refresh_result.get("results", []))
            _emit_progress(
                progress,
                kind="server_refresh_done",
                mode=source_mode,
                ok=refresh_ok,
                result=refresh_result,
            )

        result = sync_from_dns_to_main_db(progress=progress)
        if refresh_result is not None:
            result["server_refresh"] = refresh_result
        record_event(
            "sync_finish",
            mode=source_mode,
            ok=int(result.get("errors", 0) or 0) == 0,
            elapsed_ms=int((time.time() - started) * 1000),
            result=result,
        )
        _emit_progress(
            progress,
            kind="sync_finish",
            mode=source_mode,
            ok=int(result.get("errors", 0) or 0) == 0,
            result=result,
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return result

    direct_channels_raw = get_setting("direct_channels", "") or ""
    direct_proxies_raw = get_setting("direct_proxies", "") or ""
    channels = [normalize_tg_s_url(c) for c in parse_csv(direct_channels_raw)]
    if priority_channel:
        try:
            wanted = normalize_tg_s_url(priority_channel)
        except Exception:
            wanted = ""
        if wanted and wanted in channels:
            channels = [wanted, *[item for item in channels if item != wanted]]
    if not channels:
        result = {"saved": 0, "channels": 0}
        record_event("sync_finish", mode=source_mode, ok=True, elapsed_ms=int((time.time() - started) * 1000), result=result)
        _emit_progress(progress, kind="sync_finish", mode=source_mode, ok=True, result=result, elapsed_ms=int((time.time() - started) * 1000))
        return result
    proxies = parse_csv(direct_proxies_raw)

    saved = 0

    media_cache: dict[str, tuple[str, str]] = {}
    _emit_progress(progress, kind="sync_plan", mode=source_mode, channels_total=len(channels), domains_total=0)

    try:
        with SessionLocal() as db:
            for channel_index, url in enumerate(channels, start=1):
                attempts = int(__import__('os').getenv('RETRY_ATTEMPTS', '3'))
                timeout_seconds = int(__import__('os').getenv('REQUEST_TIMEOUT_SECONDS', '20'))
                retry_delay_seconds = int(__import__('os').getenv('RETRY_DELAY_SECONDS', '60'))
                recent_target = int(__import__('os').getenv('FETCH_RECENT_PER_CHANNEL', '50'))
                max_photo_bytes = int(__import__('os').getenv('DNS_MEDIA_MAX_BYTES', '180000'))

                logger.info("sync channel start url=%s attempts=%s timeout=%ss", url, attempts, timeout_seconds)
                _emit_progress(
                    progress,
                    kind="channel_start",
                    mode=source_mode,
                    domain="local",
                    channel_index=channel_index,
                    channels_total=len(channels),
                    source_url=url,
                )
                html = fetch_html_with_proxies(
                    url,
                    proxies,
                    attempts=attempts,
                    timeout_seconds=timeout_seconds,
                    retry_delay_seconds=retry_delay_seconds,
                )
                recent = parse_recent_messages(html, limit=100)
                if len(recent) < recent_target:
                    recent = collect_recent_messages(
                        url,
                        proxies,
                        target_count=recent_target,
                        attempts=attempts,
                        timeout_seconds=timeout_seconds,
                        retry_delay_seconds=retry_delay_seconds,
                    )
                else:
                    recent = recent[-recent_target:]

                if not recent:
                    logger.info("sync channel empty url=%s", url)
                    _emit_progress(
                        progress,
                        kind="channel_done",
                        mode=source_mode,
                        domain="local",
                        channel_index=channel_index,
                        channels_total=len(channels),
                        source_url=url,
                        message_total=0,
                        message_done=0,
                        channel_saved=0,
                    )
                    continue

                meta = parse_channel_meta(html)

                ch = db.scalar(select(Channel).where(Channel.source_url == url))
                if not ch:
                    ch = Channel(
                        username=url.rsplit('/', 1)[-1],
                        source_url=url,
                        title=meta.get('title', '') or url.rsplit('/', 1)[-1],
                        avatar_url=meta.get('avatar_url', ''),
                    )
                    db.add(ch)
                    db.flush()
                else:
                    ch.title = meta.get('title', '') or ch.title or ch.username
                    ch.avatar_url = meta.get('avatar_url', '') or ch.avatar_url
                    db.flush()

                channel_saved = 0
                changed_message_ids: set[int] = set()
                text_items = sorted(recent, key=lambda item: (_direct_text_weight(item), int(item.get('message_id') or 0)))
                media_items = [
                    item
                    for item in recent
                    if [url for url in (item.get("photo_urls") or []) if str(url).strip()]
                    or str(item.get("photo_url") or "").strip()
                ]
                total_work = len(text_items) + len(media_items)
                _emit_progress(
                    progress,
                    kind="channel_plan",
                    mode=source_mode,
                    domain="local",
                    channel_index=channel_index,
                    channels_total=len(channels),
                    source_url=url,
                    message_total=total_work,
                )

                dirty = 0
                for item_index, item in enumerate(text_items, start=1):
                    existing = db.scalar(
                        select(Message).where(
                            Message.channel_id == ch.id,
                            Message.message_id == item['message_id'],
                        )
                    )
                    published_at = item.get('published_at', '')
                    text = item.get('text', '')
                    has_media = bool(item.get('has_media'))
                    media_kind = item.get('media_kind', '') or ''
                    reply_to_message_id = item.get('reply_to_message_id')
                    reply_author = item.get('reply_author', '') or ''
                    reply_text = item.get('reply_text', '') or ''
                    forward_source = item.get('forward_source', '') or ''

                    changed = False
                    if existing:
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
                    else:
                        db.add(
                            Message(
                                channel_id=ch.id,
                                message_id=item['message_id'],
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
                        changed = True

                    if changed:
                        changed_message_ids.add(int(item['message_id']))
                        channel_saved += 1
                    dirty += 1
                    if item_index == len(text_items) or item_index % 5 == 0:
                        _emit_progress(
                            progress,
                            kind="channel_progress",
                            mode=source_mode,
                            domain="local",
                            channel_index=channel_index,
                            channels_total=len(channels),
                            source_url=url,
                            message_total=total_work,
                            message_done=item_index,
                            channel_saved=channel_saved,
                        )
                    if dirty >= _direct_text_batch_size():
                        db.commit()
                        dirty = 0
                if dirty or not text_items:
                    db.commit()

                dirty = 0
                for media_index, item in enumerate(media_items, start=1):
                    photo_urls = [str(url).strip() for url in (item.get("photo_urls") or []) if str(url).strip()]
                    if not photo_urls and str(item.get("photo_url") or "").strip():
                        photo_urls = [str(item.get("photo_url") or "").strip()]
                    photo_items = _fetch_photo_items(
                        photo_urls,
                        proxies,
                        media_cache,
                        timeout_seconds=timeout_seconds,
                        max_photo_bytes=max_photo_bytes,
                    )
                    photo_mime, photo_b64 = primary_photo_fields(photo_items)
                    photos_json = serialize_photo_items(photo_items)

                    existing = db.scalar(
                        select(Message).where(
                            Message.channel_id == ch.id,
                            Message.message_id == item['message_id'],
                        )
                    )
                    changed = False
                    if existing:
                        if existing.photo_mime != photo_mime:
                            existing.photo_mime = photo_mime
                            changed = True
                        if existing.photo_b64 != photo_b64:
                            existing.photo_b64 = photo_b64
                            changed = True
                        if existing.photos_json != photos_json:
                            existing.photos_json = photos_json
                            changed = True
                        if not bool(existing.has_media):
                            existing.has_media = True
                            changed = True
                        media_kind = item.get('media_kind', '') or ''
                        if media_kind and existing.media_kind != media_kind:
                            existing.media_kind = media_kind
                            changed = True
                    else:
                        db.add(
                            Message(
                                channel_id=ch.id,
                                message_id=item['message_id'],
                                published_at=item.get('published_at', ''),
                                text=item.get('text', ''),
                                has_media=True,
                                media_kind=item.get('media_kind', '') or ('photo' if photo_b64 else ''),
                                photo_mime=photo_mime,
                                photo_b64=photo_b64,
                                photos_json=photos_json,
                                reply_to_message_id=item.get('reply_to_message_id'),
                                reply_author=item.get('reply_author', '') or '',
                                reply_text=item.get('reply_text', '') or '',
                                forward_source=item.get('forward_source', '') or '',
                            )
                        )
                        changed = True

                    if changed:
                        changed_message_ids.add(int(item['message_id']))
                        channel_saved += 1
                    dirty += 1
                    done_count = len(text_items) + media_index
                    if done_count == total_work or done_count % 5 == 0:
                        _emit_progress(
                            progress,
                            kind="channel_progress",
                            mode=source_mode,
                            domain="local",
                            channel_index=channel_index,
                            channels_total=len(channels),
                            source_url=url,
                            message_total=total_work,
                            message_done=done_count,
                            channel_saved=channel_saved,
                        )
                    if dirty >= _direct_media_batch_size():
                        db.commit()
                        dirty = 0
                if dirty or not media_items:
                    db.commit()

                if not changed_message_ids:
                    logger.info("sync channel unchanged url=%s fetched=%s", url, len(recent))
                    _emit_progress(
                        progress,
                        kind="channel_done",
                        mode=source_mode,
                        domain="local",
                        channel_index=channel_index,
                        channels_total=len(channels),
                        source_url=url,
                        message_total=total_work,
                        message_done=total_work,
                        channel_saved=0,
                    )
                    continue

                saved += channel_saved
                logger.info("sync channel saved url=%s saved=%s fetched=%s", url, channel_saved, len(recent))
                _emit_progress(
                    progress,
                    kind="channel_done",
                    mode=source_mode,
                    domain="local",
                    channel_index=channel_index,
                    channels_total=len(channels),
                    source_url=url,
                    message_total=total_work,
                    message_done=total_work,
                    channel_saved=channel_saved,
                )

    except Exception as exc:
        logger.exception("sync failed mode=%s", source_mode)
        record_event("sync_finish", level="error", mode=source_mode, ok=False, elapsed_ms=int((time.time() - started) * 1000), error=str(exc))
        _emit_progress(progress, kind="sync_error", mode=source_mode, error=str(exc), elapsed_ms=int((time.time() - started) * 1000))
        raise
    result = {"saved": saved, "channels": len(channels)}
    record_event("sync_finish", mode=source_mode, ok=True, elapsed_ms=int((time.time() - started) * 1000), result=result)
    _emit_progress(progress, kind="sync_finish", mode=source_mode, ok=True, result=result, elapsed_ms=int((time.time() - started) * 1000))
    return result


def sync_once(progress: ProgressCallback = None, force_server_refresh: bool = False, priority_channel: str = "") -> dict:
    with _SYNC_EXEC_LOCK:
        return _sync_once_impl(progress=progress, force_server_refresh=force_server_refresh, priority_channel=priority_channel)

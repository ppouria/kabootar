import base64
import re
import time
from html import unescape
from typing import Optional

import requests

from app.utils import normalize_proxy_url

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"


def strip_tags(html_fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html_fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _extract_style_url(style: str) -> str:
    m = re.search(r"url\(['\"]?([^'\")]+)", style or "")
    if not m:
        return ""
    out = m.group(1).strip()
    if out.startswith("//"):
        out = "https:" + out
    return out


def _extract_element_html(html: str, class_token: str, tags: tuple[str, ...] = ("div",)) -> str:
    match = None
    match_tag = ""
    for tag in tags:
        found = re.search(
            rf"<{tag}\b[^>]*class=\"[^\"]*{re.escape(class_token)}[^\"]*\"[^>]*>",
            html,
            flags=re.I | re.S,
        )
        if found and (match is None or found.start() < match.start()):
            match = found
            match_tag = tag
    if not match or not match_tag:
        return ""

    token_re = re.compile(rf"<{match_tag}\b[^>]*>|</{match_tag}>", flags=re.I | re.S)
    depth = 0
    end = match.end()
    for token in token_re.finditer(html, match.start()):
        text = token.group(0).lower()
        if text.startswith(f"<{match_tag}") and not text.startswith(f"</{match_tag}>"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end = token.end()
                break
    return html[match.start():end]


def _inner_html(element_html: str) -> str:
    if not element_html:
        return ""
    start = element_html.find(">")
    end = element_html.rfind("</")
    if start == -1 or end == -1 or end <= start:
        return ""
    return element_html[start + 1 : end]


def parse_channel_meta(html: str) -> dict:
    title = ""
    avatar_url = ""

    og_title = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
    if og_title:
        title = unescape(og_title.group(1)).strip()

    if not title:
        page_title = re.search(r"<title>(.*?)</title>", html, flags=re.S | re.I)
        if page_title:
            title = unescape(page_title.group(1)).replace("Telegram:", "").strip()

    photo = re.search(r'<i class="tgme_page_photo_image"[^>]*style="([^"]+)"', html)
    if photo:
        style = photo.group(1)
        m = re.search(r"url\(['\"]?([^'\")]+)", style)
        if m:
            avatar_url = m.group(1).strip()

    if avatar_url.startswith("//"):
        avatar_url = "https:" + avatar_url

    return {
        "title": title,
        "avatar_url": avatar_url,
    }


def _parse_message_block(block: str) -> Optional[dict]:
    post_match = re.search(r'data-post="([^"]+)"', block)
    if not post_match:
        return None
    post = post_match.group(1)

    id_match = re.search(r"/(\d+)$", post)
    msg_id = int(id_match.group(1)) if id_match else None
    if msg_id is None:
        return None

    date_match = re.search(r'datetime="([^"]+)"', block)
    dt = date_match.group(1) if date_match else ""

    text_html = _extract_element_html(block, "js-message_text", tags=("div",))
    text_inner = _inner_html(text_html)
    has_media = bool(re.search(r'tgme_widget_message_(photo|video|document|voice|audio)', block))
    photo_url = ""
    photo_style_match = re.search(r'tgme_widget_message_photo_wrap[^>]*style="([^"]+)"', block, flags=re.S)
    if photo_style_match:
        photo_url = _extract_style_url(photo_style_match.group(1))

    reply_to_message_id: int | None = None
    reply_author = ""
    reply_text = ""
    reply_html = _extract_element_html(block, "tgme_widget_message_reply", tags=("a", "div"))
    if reply_html:
        reply_tag = reply_html.split(">", 1)[0]
        href_match = re.search(r'href="([^"]+)"', reply_tag)
        if href_match:
            mid_match = re.search(r"/(\d+)(?:\?|$)", href_match.group(1))
            if mid_match:
                reply_to_message_id = int(mid_match.group(1))
        author_html = _extract_element_html(reply_html, "tgme_widget_message_author_name", tags=("span", "div"))
        if not author_html:
            author_html = _extract_element_html(reply_html, "tgme_widget_message_reply_author", tags=("div", "span"))
        if author_html:
            reply_author = strip_tags(_inner_html(author_html))
        text_reply_html = _extract_element_html(reply_html, "js-message_reply_text", tags=("div", "span"))
        if not text_reply_html:
            text_reply_html = _extract_element_html(reply_html, "tgme_widget_message_reply_text", tags=("div", "span"))
        if text_reply_html:
            reply_text = strip_tags(_inner_html(text_reply_html))
        if text_inner:
            text_inner = text_inner.replace(reply_html, "", 1)

    text = strip_tags(text_inner) if text_inner else ""

    return {
        "message_id": msg_id,
        "published_at": dt,
        "text": text,
        "has_media": has_media,
        "photo_url": photo_url,
        "reply_to_message_id": reply_to_message_id,
        "reply_author": reply_author,
        "reply_text": reply_text,
        "post": post,
    }


def parse_latest_message(html: str) -> Optional[dict]:
    items = parse_recent_messages(html, limit=1)
    return items[-1] if items else None


def parse_recent_messages(html: str, limit: int = 50) -> list[dict]:
    blocks = re.findall(r'(<div class="tgme_widget_message_wrap[^>]*>.*?</div>\s*</div>)', html, flags=re.S)
    if not blocks:
        return []

    out: list[dict] = []
    for block in blocks[-max(1, limit):]:
        parsed = _parse_message_block(block)
        if parsed:
            out.append(parsed)

    return out


def fetch_html_with_proxies(url: str, proxies: list[str], attempts: int = 3, timeout_seconds: int = 20, retry_delay_seconds: int = 60) -> str:
    proxy_list = [normalize_proxy_url(p) for p in proxies if p.strip()]
    if not proxy_list:
        proxy_list = [""]

    last_err = None
    for i in range(1, attempts + 1):
        for proxy in proxy_list:
            try:
                kwargs = {"timeout": timeout_seconds, "headers": {"User-Agent": UA}}
                if proxy:
                    kwargs["proxies"] = {"http": proxy, "https": proxy}
                r = requests.get(url, **kwargs)
                r.raise_for_status()
                return r.text
            except Exception as e:
                last_err = e
        if i < attempts:
            time.sleep(retry_delay_seconds)

    raise RuntimeError(f"failed fetch after retries: {last_err}")


def fetch_photo_base64_with_proxies(
    url: str,
    proxies: list[str],
    attempts: int = 2,
    timeout_seconds: int = 20,
    retry_delay_seconds: int = 5,
    max_bytes: int = 180_000,
) -> tuple[str, str] | None:
    if not url:
        return None

    proxy_list = [normalize_proxy_url(p) for p in proxies if p.strip()]
    if not proxy_list:
        proxy_list = [""]

    last_err = None
    for i in range(1, attempts + 1):
        for proxy in proxy_list:
            try:
                kwargs = {
                    "timeout": timeout_seconds,
                    "headers": {"User-Agent": UA, "Accept": "image/*"},
                }
                if proxy:
                    kwargs["proxies"] = {"http": proxy, "https": proxy}
                r = requests.get(url, **kwargs)
                r.raise_for_status()
                body = r.content or b""
                if not body:
                    return None
                if len(body) > max_bytes:
                    return None
                mime = (r.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
                if not mime.startswith("image/"):
                    mime = "image/jpeg"
                return mime, base64.b64encode(body).decode("ascii")
            except Exception as e:
                last_err = e
        if i < attempts:
            time.sleep(retry_delay_seconds)

    raise RuntimeError(f"failed photo fetch after retries: {last_err}")
